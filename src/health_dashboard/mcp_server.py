"""MCP server "health-app": your own health data tools over stdio.

Reads the same numbers as the local API (scores, sleep, runs, annotations) and
lets Claude record context (illness, alcohol, travel, ...) as annotations.

    health mcp              (= python -m health_dashboard.mcp_server)

Data paths come from the environment (config.get_paths). A question about the
data starts a full sync in the background when the last one is over 3 hours
old, so the next answer is fresh without asking for a sync.

Logs go to stderr only (Claude Desktop keeps them in mcp-server-health-app.log);
stdout carries the protocol, so nothing here may print to it.
"""

import faulthandler
import logging
import os
import sys
import time
from datetime import datetime, timedelta

try:  # mcp 2.x renamed FastMCP to MCPServer; same decorator API
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from .config import get_paths
from .core import annotations as ann
from .core import jobs, queries

mcp = FastMCP("health-app")
log = logging.getLogger("health-app")
AUTO_SYNC_AFTER = timedelta(hours=3)


def _sync_if_stale():
    """Start a sync job (it runs detached) if none succeeded in AUTO_SYNC_AFTER
    and none is running. Never fails the question it was called for."""
    try:
        paths = get_paths()
        conn = jobs.connect(paths)
        try:
            last = jobs.last_successful_sync(conn)
        finally:
            conn.close()
        if (last is None or datetime.now() - last > AUTO_SYNC_AFTER) and not jobs.lock_held(paths):
            run = jobs.start_sync("open", origin="mcp")
            log.info("data older than %s: started sync run %s", AUTO_SYNC_AFTER, run.get("id"))
    except Exception:
        log.exception("could not start the background sync")


def _recompute_if(changed):
    return jobs.start_recompute(origin="mcp") if changed else None


@mcp.tool()
def get_day(date: str) -> dict:
    """Everything for one day (YYYY-MM-DD): Recovery % with its z-scores, last
    night's sleep (night keyed by wake date) with Sleep Performance, Strain and
    a strain target, steps, this week's running (km, minutes, TRIMP and
    easy_pct: share of run time at or below the top of Z2), the daily-check warning
    (AMBER/RED) and the annotations (illness, alcohol, ...) touching that day."""
    _sync_if_stale()
    with queries.Db() as db:
        try:
            return queries.dashboard(db, date)
        except ValueError as e:  # bad date: tell the model why
            return {"error": str(e)}


@mcp.tool()
def get_trend(metric: str, date_from: str, date_to: str) -> dict:
    """Daily values of one metric between two dates (YYYY-MM-DD, inclusive),
    with n/mean/min/max and the annotations in that range (each point lists the
    ids of annotations touching it). Metrics: recovery, hrv_ms, rhr,
    sleep_performance, asleep_min, sleep_min (night only), nap_min (minutes napped
    per day), strain, steps, google_hrv_rmssd,
    nrem_hr, breathing, skin_temp, spo2, vo2max, weight, trimp, google_readiness."""
    _sync_if_stale()
    with queries.Db() as db:
        try:
            return queries.trend(db, metric, date_from, date_to)
        except ValueError as e:  # bad date or metric: tell the model why
            return {"error": str(e)}


@mcp.tool()
def list_runs(date_from: str, date_to: str) -> dict:
    """Watch-recorded runs between two dates (YYYY-MM-DD): km, minutes, avg HR,
    TRIMP, Strain, minutes per HR zone, efficiency factor, decoupling, HR
    recovery, plus annotations (e.g. run_intent) touching them, and a
    `running` summary (runs, km, minutes, TRIMP, easy_pct). Use the `id` with
    get_run."""
    _sync_if_stale()
    with queries.Db() as db:
        try:
            return queries.runs(db, date_from, date_to)
        except ValueError as e:  # bad date: tell the model why
            return {"error": str(e)}


@mcp.tool()
def get_run(workout_id: str) -> dict:
    """One workout by id (the short numeric id from list_runs, or the full
    log_id): full analysis row, Google's summary and km splits, Strain,
    minutes per HR zone Z1-Z5 as the app shows them (hr_zones; Z3/Z4 split
    Google's vigorous zone in the middle), and annotations targeting it or its
    day."""
    _sync_if_stale()
    with queries.Db() as db:
        r = queries.run_detail(db, workout_id)
    return r or {"error": f"no workout with id {workout_id}"}


@mcp.tool()
def list_annotations(date_from: str, date_to: str, kind: str | None = None) -> list[dict]:
    """Annotations (context the watch doesn't have) touching a date range,
    optionally of one kind. Each has status suggested/confirmed/rejected; only
    confirmed illness/alcohol ones are excluded from baselines
    (`excludes_nights` shows which nights)."""
    conn = ann.connect()
    try:
        return ann.list_(conn, date_from, date_to, kind)
    finally:
        conn.close()


@mcp.tool()
def add_annotation(kind: str, start: str, end: str | None = None, attrs: dict | None = None,
                   note: str | None = None, confirmed: bool = False, target: str | None = None) -> dict:
    """Record context for the user's data.

    kind: alcohol, illness, injury, travel, late_meal, stress, run_intent,
      post_run_protocol, bad_sleep_env or note.
    start / end: ISO date or datetime (end inclusive, optional). For alcohol,
      start is the evening of drinking; it affects the night after (wake date
      start + 1) unless target says otherwise.
    attrs: JSON object, e.g. {"drinks": 5} or {"intent": "Z2", "cap_bpm": 150}.
    target: optional night:YYYY-MM-DD (wake date), day:YYYY-MM-DD or workout:<id>.
      Nights are named by the morning you wake up: the night from Sat 24 to
      Sun 25 is night:2026-10-25. Work out dates from today's date: "last
      night" or "yesterday evening" said today means start = yesterday and
      target = night:<today>. For alcohol always pass that night as target.
      If it is unclear which evening the user means (e.g. "on the weekend"),
      ask them before saving. Afterwards tell them which night it was saved
      on (excludes_nights in the result).
    confirmed: set true ONLY when the user stated the fact directly in the
      conversation ("I had 5 beers on Saturday"). Anything you inferred from
      the data or guessed stays false and is saved as a suggestion for them to
      confirm. A confirmed illness or alcohol annotation changes the baselines
      and triggers a recompute of all scores.
    """
    conn = ann.connect()
    try:
        a = ann.add(conn, kind, start, end=end, target=target, attrs=attrs,
                    status="confirmed" if confirmed else "suggested",
                    source="user" if confirmed else "ai", note=note)
    except ann.AnnotationError as e:
        return {"error": str(e)}
    finally:
        conn.close()
    return {"annotation": a, "recompute": _recompute_if(ann.changes_scores(a))}


@mcp.tool()
def delete_annotation(annotation_id: int) -> dict:
    """Delete one annotation by id (e.g. one added by mistake). Deleting a
    confirmed illness/alcohol annotation triggers a recompute of the scores."""
    conn = ann.connect()
    try:
        a, changed = ann.delete(conn, annotation_id)
    except KeyError:
        return {"error": f"no annotation {annotation_id}"}
    finally:
        conn.close()
    return {"deleted": a, "recompute": _recompute_if(changed)}


@mcp.tool()
def explain_score(date: str, score: str) -> dict:
    """Break a score down into its components for one date (YYYY-MM-DD).
    score: recovery (z-scores of HRV, resting HR, sleep, breathing x weights,
    penalties, baseline size), sleep (Sufficiency/Consistency/Efficiency/Calm and
    the sleep need) or strain (load, cardio vs muscular, k, workouts)."""
    _sync_if_stale()
    with queries.Db() as db:
        try:
            return queries.explain_score(db, date, score)
        except ValueError as e:
            return {"error": str(e)}


@mcp.tool()
def sync_now(wait_seconds: int = 40) -> dict:
    """Fetch the newest data from Google now (daily cache + intraday samples) and
    recompute all scores. Only one sync runs at a time; if one is running this
    returns it. Waits up to wait_seconds for it to finish, then returns the
    current status (a full sync usually takes a few minutes; ask again with
    get_sync_status)."""
    run = jobs.start_sync("mcp", origin="mcp")
    return _wait(run, wait_seconds)


@mcp.tool()
def get_sync_status(run_id: int | None = None) -> dict:
    """Status of a sync run (from sync_now), or without run_id: last sync,
    newest data per type and whether a sync is running."""
    if run_id is None:
        with queries.Db() as db:
            return queries.status(db)
    conn = jobs.connect()
    try:
        return jobs.get_run(conn, run_id) or {"error": f"no sync run {run_id}"}
    finally:
        conn.close()


def _wait(run, seconds):
    deadline = time.time() + max(0, min(seconds, 50))
    while run and run["status"] in ("running", "queued") and time.time() < deadline:
        time.sleep(2)
        conn = jobs.connect()
        try:
            run = jobs.get_run(conn, run["id"])
        finally:
            conn.close()
    return run


def main(argv=None):
    # Plain timestamped lines on stderr; a failing tool call is already caught by
    # the SDK, returned to the model as an error and logged here with its traceback.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    faulthandler.enable(sys.stderr)  # hard crashes (e.g. in sqlite) still leave a trace
    log.info("starting (pid %s, data %s)", os.getpid(), get_paths().data)
    try:
        conn = ann.connect()
        ann.ensure_schema(conn)
        conn.close()
        jobs.connect().close()
        mcp.run()
    except BaseException:
        log.exception("stopped with an error")
        raise
    log.info("stopped")


if __name__ == "__main__":
    main()
