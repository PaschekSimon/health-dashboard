"""Local API for the health dashboard. Binds to 127.0.0.1 by default.

    health serve            (= uvicorn health_dashboard.app:app --host 127.0.0.1 --port 8765)

Endpoints are listed in docs/api.md; interactive docs at http://127.0.0.1:8765/docs.
Data paths come from the environment on every request (config.get_paths).
"""

import ipaddress
import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import get_paths
from .core import annotations as ann
from .core import explore, jobs, meta, queries
from .core import schemas as S

# Windows' registry doesn't always know these, and Chrome wants the right type
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("font/woff2", ".woff2")


@asynccontextmanager
async def lifespan(_app):
    conn = ann.connect()
    # WAL lets the API keep reading while a sync or recompute rewrites the score
    # tables (otherwise a request waits up to the 30 s busy timeout). The mode
    # is stored in health.db, so this switches it once for every program.
    conn.execute("PRAGMA journal_mode=WAL")
    ann.ensure_schema(conn)  # annotations + sync_runs exist before the first request
    conn.close()
    jobs.connect().close()
    yield


app = FastAPI(title="Health backend", version="1.1", lifespan=lifespan)


# ------------------------------------------------------------- guard ----
# The API has no login: it trusts whoever can reach it (this PC, and your
# tailnet through `tailscale serve`). Two checks keep other websites out:
#  - Host: only names that can point here. A public name that resolves to
#    127.0.0.1 (DNS rebinding) carries its own Host and is turned away.
#  - Origin: a page on another site can still fire a simple POST at
#    127.0.0.1 (e.g. /annotations/5/confirm); the browser sends that site as
#    Origin, so writes from any origin but ours are refused. Unlike Host, an
#    IP address here only counts if it's this PC, your LAN or your tailnet: a
#    page served from a public IP is another site.
# Extra host names (comma-separated) can be added with HEALTH_ALLOWED_HOSTS.

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
# this PC, the LAN (IPv4 private ranges, link-local, IPv6 unique local) and
# the tailnet (Tailscale's 100.64.0.0/10; its IPv6 addresses are unique local)
OWN_NETWORKS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10",
    "::1/128", "fc00::/7", "fe80::/10")]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _extra_hosts():
    return {h.strip().lower() for h in os.environ.get("HEALTH_ALLOWED_HOSTS", "").split(",") if h.strip()}


def allowed_host(name):
    """localhost, an IP address, a Tailscale name (*.ts.net), a bare machine
    name without dots, or HEALTH_ALLOWED_HOSTS. A rebinding attack needs a
    public DNS name, and those always have a dot."""
    name = (name or "").lower().rstrip(".")
    if not name:
        return False
    if name in LOCAL_HOSTS or name in _extra_hosts() or name.endswith(".ts.net") or "." not in name:
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def allowed_origin(origin):
    """Origin of a page that may write: one on a name allowed_host accepts, or
    on an IP address of this PC, the LAN or the tailnet."""
    if not origin or origin == "null":
        return False
    name = urlsplit(origin).hostname
    try:
        ip = ipaddress.ip_address(name or "")
    except ValueError:
        return allowed_host(name)
    ip = getattr(ip, "ipv4_mapped", None) or ip
    return any(ip in n for n in OWN_NETWORKS)


def _hostname(netloc):
    try:
        return urlsplit("//" + netloc).hostname
    except ValueError:
        return None


@app.middleware("http")
async def same_site_only(request, call_next):
    if not allowed_host(_hostname(request.headers.get("host", ""))):
        return PlainTextResponse("unknown host", status_code=403)
    origin = request.headers.get("origin")
    if origin and request.method not in SAFE_METHODS:
        if not allowed_origin(origin):
            return PlainTextResponse("cross-site request refused", status_code=403)
    return await call_next(request)


def _db():
    return queries.Db()


def _bad(e):
    raise HTTPException(400, str(e))


# ---------------------------------------------------------------- meta ----

@app.get("/meta", response_model=S.Meta)
def get_meta():
    """Fixed lists and limits: annotation kinds, sync steps by job reason,
    Recovery and Strain bands, Strain targets, trend metrics."""
    return meta.meta()


# ---------------------------------------------------------------- sync ----

class SyncRequest(BaseModel):
    reason: Literal["open", "button"] = "button"


@app.post("/sync", response_model=S.SyncRun)
def post_sync(req: SyncRequest):
    """Start a sync job (single flight). reason=open skips if the last successful
    sync was < 15 min ago; reason=button always runs."""
    return jobs.start_sync(req.reason, origin="api")


@app.get("/sync/{run_id}", response_model=S.SyncRun)
def get_sync(run_id: int):
    conn = jobs.connect()
    try:
        run = jobs.get_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(404, "no such sync run")
    return run


@app.get("/status", response_model=S.Status)
def get_status():
    with _db() as db:
        return queries.status(db)


# ---------------------------------------------------------------- reads ----

@app.get("/dashboard", response_model=S.Dashboard)
def get_dashboard(date: date | None = None):
    with _db() as db:
        return queries.dashboard(db, date)


@app.get("/recovery", response_model=S.RecoveryRange)
def get_recovery(from_: date | None = Query(None, alias="from"), to: date | None = None):
    with _db() as db:
        try:
            return queries.recovery(db, from_, to)
        except ValueError as e:
            _bad(e)


@app.get("/sleep", response_model=S.SleepRange)
def get_sleep(from_: date | None = Query(None, alias="from"), to: date | None = None):
    with _db() as db:
        try:
            return queries.sleep(db, from_, to)
        except ValueError as e:
            _bad(e)


@app.get("/runs", response_model=S.RunList)
def get_runs(from_: date | None = Query(None, alias="from"), to: date | None = None, all_types: bool = False):
    with _db() as db:
        try:
            return queries.runs(db, from_, to, all_types)
        except ValueError as e:
            _bad(e)


@app.get("/runs/{run_id:path}", response_model=S.RunDetail)
def get_run(run_id: str):
    with _db() as db:
        r = queries.run_detail(db, run_id)
    if r is None:
        raise HTTPException(404, "no such workout")
    return r


@app.get("/trend/{metric}", response_model=S.Trend)
def get_trend(metric: str, from_: date | None = Query(None, alias="from"), to: date | None = None):
    with _db() as db:
        try:
            return queries.trend(db, metric, from_, to)
        except ValueError as e:
            _bad(e)


@app.get("/activity", response_model=S.ActivityRange)
def get_activity(from_: date | None = Query(None, alias="from"), to: date | None = None):
    with _db() as db:
        try:
            return explore.activity(db, from_, to)
        except ValueError as e:
            _bad(e)


@app.get("/day/{day}", response_model=S.DayDetail)
def get_day(day: date):
    """Intraday view of one date: HR (5-min means), steps per hour, sleep, workouts."""
    with _db() as db:
        return explore.day_detail(db, day)


@app.get("/training", response_model=S.Training)
def get_training(from_: date | None = Query(None, alias="from"), to: date | None = None):
    with _db() as db:
        try:
            return explore.training(db, from_, to)
        except ValueError as e:
            _bad(e)


@app.get("/workout-series/{run_id:path}", response_model=S.WorkoutSeries)
def get_workout_series(run_id: str):
    with _db() as db:
        r = explore.run_series(db, run_id)
    if r is None:
        raise HTTPException(404, "no such workout")
    return r


@app.get("/longterm", response_model=S.LongTerm)
def get_longterm():
    with _db() as db:
        return explore.longterm(db)


@app.get("/ecg/{reading_id:path}", response_model=S.Ecg)
def get_ecg(reading_id: str):
    with _db() as db:
        r = explore.ecg_waveform(db, reading_id)
    if r is None:
        raise HTTPException(404, "no such ECG")
    return r


@app.get("/data", response_model=S.Catalog)
def get_catalog():
    """Every table in health.db and the MCP cache, with row counts and date range."""
    with _db() as db:
        return explore.catalog(db)


@app.get("/data/{source}/{table}", response_model=S.TableRows)
def get_table(source: Literal["health", "google"], table: str, from_: date | None = Query(None, alias="from"),
              to: date | None = None, limit: int = 200, offset: int = 0, desc: bool = True):
    with _db() as db:
        try:
            return explore.browse(db, source, table, from_, to, limit, offset, desc)
        except ValueError as e:
            _bad(e)


@app.get("/explain/{score}", response_model=S.Explanation)
def get_explain(score: Literal["recovery", "sleep", "strain"], date: date):
    with _db() as db:
        return queries.explain_score(db, date, score)


# ---------------------------------------------------------- annotations ----

class AnnotationIn(BaseModel):
    kind: str
    start: str
    end: str | None = None
    target: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)
    status: Literal["suggested", "confirmed", "rejected"] = "suggested"
    source: Literal["user", "ai", "strava", "import"] = "user"
    confidence: float | None = None
    raw_text: str | None = None
    note: str | None = None


def _with_recompute(result, changed):
    result = {"annotation": result, "recompute": None}
    if changed:
        result["recompute"] = jobs.start_recompute(origin="api")
    return result


@app.get("/annotations", response_model=list[S.Annotation])
def list_annotations(from_: date | None = Query(None, alias="from"), to: date | None = None,
                     kind: str | None = None, status: str | None = None):
    conn = ann.connect()
    try:
        return ann.list_(conn, from_ and from_.isoformat(), to and to.isoformat(), kind, status)
    finally:
        conn.close()


@app.post("/annotations", status_code=201, response_model=S.AnnotationChange)
def create_annotation(body: AnnotationIn):
    conn = ann.connect()
    try:
        a = ann.add(conn, **body.model_dump())
    except ann.AnnotationError as e:
        _bad(e)
    finally:
        conn.close()
    return _with_recompute(a, ann.changes_scores(a))


def _set_status(ann_id, status):
    conn = ann.connect()
    try:
        a, changed = ann.set_status(conn, ann_id, status)
    except KeyError:
        raise HTTPException(404, "no such annotation") from None
    finally:
        conn.close()
    return _with_recompute(a, changed)


@app.post("/annotations/{ann_id}/confirm", response_model=S.AnnotationChange)
def confirm_annotation(ann_id: int):
    return _set_status(ann_id, "confirmed")


@app.post("/annotations/{ann_id}/reject", response_model=S.AnnotationChange)
def reject_annotation(ann_id: int):
    return _set_status(ann_id, "rejected")


@app.delete("/annotations/{ann_id}", response_model=S.AnnotationChange)
def delete_annotation(ann_id: int):
    conn = ann.connect()
    try:
        a, changed = ann.delete(conn, ann_id)
    except KeyError:
        raise HTTPException(404, "no such annotation") from None
    finally:
        conn.close()
    return _with_recompute(a, changed)


# ------------------------------------------------------------- web app ----

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/app/")


@app.middleware("http")
async def revalidate_web(request, call_next):
    """Make the browser re-check the app files on every load, so edits show up
    without a hard refresh (unchanged files still come back as 304)."""
    response = await call_next(request)
    if request.url.path.startswith("/app"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/next", include_in_schema=False)
@app.get("/next/", include_in_schema=False)
def next_moved():
    """The React app was tried out at /next/ before it replaced the old one."""
    return RedirectResponse("/app/")


class WebApp(StaticFiles):
    """The built web app, or a hint instead of an error before the first build."""

    async def __call__(self, scope, receive, send):
        if not os.path.isdir(self.directory):
            hint = PlainTextResponse("The web app isn't built yet: in web/ run `npm ci` and `npm run build`, "
                                     "then reload.", status_code=503)
            return await hint(scope, receive, send)
        return await super().__call__(scope, receive, send)


app.mount("/app", WebApp(directory=get_paths().frontend, html=True, check_dir=False), name="web")
