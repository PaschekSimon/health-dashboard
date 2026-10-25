r"""Check the API's response models against your real data, without a running
server and without writing to it: every GET route is called through
FastAPI's TestClient on a copy of data\health.db (the MCP cache is opened
read-only, as always).

    .venv\Scripts\python.exe tests\check_api_models.py [--data C:\path\to\Health\data] [--days 60]

For each route it reports the status code and two problems a 200 can hide:
- keys the query returns that no model lists (they still reach the app, but
  the generated TypeScript types don't know them),
- values that differ between the query's answer and the response.
--data is for running it from another checkout (e.g. a git worktree of a
branch) against the data folder of your main one.
"""

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import types
import typing
from datetime import date, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

ap = argparse.ArgumentParser()
ap.add_argument("--data", type=Path, help="data folder to read (default: this checkout's data/)")
ap.add_argument("--days", type=int, default=60, help="how far back the range routes look (default 60)")
args = ap.parse_args()

from health_core import paths  # noqa: E402

data = args.data or paths.DATA
tmp = Path(tempfile.mkdtemp(prefix="health-check-"))
src = sqlite3.connect((data / "health.db").resolve().as_uri() + "?mode=ro", uri=True)
dst = sqlite3.connect(tmp / "health.db")
src.backup(dst)
src.close()
dst.close()
paths.DATA, paths.HEALTH_DB = tmp, tmp / "health.db"
paths.SYNC_LOCK, paths.LAST_SYNC = tmp / ".sync.lock", tmp / ".last_sync"
if (data / ".last_sync").exists():
    shutil.copy2(data / ".last_sync", paths.LAST_SYNC)
if (data / "scores_config.json").exists():
    shutil.copy2(data / "scores_config.json", tmp / "scores_config.json")

from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import app as appmod  # noqa: E402
from health_core import explore, meta, queries  # noqa: E402
from health_core import schemas as S  # noqa: E402


def undeclared(tp, v, path="$"):
    """Keys in v that the type tp doesn't declare, recursively (first 5 items of lists/dicts)."""
    origin, targs = typing.get_origin(tp), typing.get_args(tp)
    if origin is typing.Annotated:
        return undeclared(targs[0], v, path)
    if origin in (typing.Union, types.UnionType):
        for a in targs:
            if isinstance(a, type) and issubclass(a, BaseModel) and isinstance(v, dict):
                score = a.model_fields.get("score")
                if score is not None and typing.get_args(score.annotation) != (v.get("score"),):
                    continue
                return undeclared(a, v, path)
            if typing.get_origin(a) in (list, dict):
                return undeclared(a, v, path)
        return []
    if origin is list and isinstance(v, list):
        return sum((undeclared(targs[0], x, f"{path}[{i}]") for i, x in enumerate(v[:5])), [])
    if origin is dict and isinstance(v, dict) and len(targs) == 2:
        return sum((undeclared(targs[1], x, f"{path}.{k}") for k, x in list(v.items())[:5]), [])
    if isinstance(tp, type) and issubclass(tp, BaseModel) and isinstance(v, dict):
        fields = {(f.alias or n): f for n, f in tp.model_fields.items()}
        out = [f"{path}.{k}" for k in v if k not in fields]
        return out + sum((undeclared(fields[k].annotation, x, f"{path}.{k}") for k, x in v.items() if k in fields), [])
    return []


def differs(raw, got, path="$"):
    """raw must be inside got; keys only in got must be null or empty."""
    if isinstance(raw, dict) and isinstance(got, dict):
        errs = [f"{path}.{k} missing" for k in raw if k not in got]
        errs += [f"{path}.{k} added as {got[k]!r}" for k in got if k not in raw and got[k] not in (None, [])]
        return errs + sum((differs(raw[k], got[k], f"{path}.{k}") for k in raw if k in got), [])
    if isinstance(raw, list) and isinstance(got, list):
        if len(raw) != len(got):
            return [f"{path}: {len(raw)} items, response has {len(got)}"]
        return sum((differs(x, y, f"{path}[{i}]") for i, (x, y) in enumerate(zip(raw, got))), [])
    return [] if raw == got else [f"{path}: {raw!r} became {got!r}"]


today = date.today()
fr, to = (today - timedelta(days=args.days)).isoformat(), today.isoformat()
with queries.Db() as db:
    runs = queries.runs(db, fr, to, all_types=True)["runs"]
    ecgs = explore.longterm(db)["ecg"]
    tables = explore.catalog(db)["tables"]
days = [(today - timedelta(days=n)).isoformat() for n in (0, 1, 7, 30)]

routes = [("/meta", S.Meta, lambda db: meta.meta()), ("/status", S.Status, queries.status),
          ("/longterm", S.LongTerm, explore.longterm), ("/data", S.Catalog, explore.catalog),
          (f"/recovery?from={fr}&to={to}", S.RecoveryRange, lambda db: queries.recovery(db, fr, to)),
          (f"/sleep?from={fr}&to={to}", S.SleepRange, lambda db: queries.sleep(db, fr, to)),
          (f"/runs?from={fr}&to={to}&all_types=true", S.RunList, lambda db: queries.runs(db, fr, to, True)),
          (f"/activity?from={fr}&to={to}", S.ActivityRange, lambda db: explore.activity(db, fr, to)),
          (f"/training?from={fr}&to={to}", S.Training, lambda db: explore.training(db, fr, to)),
          (f"/annotations?from={fr}&to={to}", list[S.Annotation], lambda db: queries.ann.list_(db.a, fr, to))]
for d in days:
    routes += [(f"/dashboard?date={d}", S.Dashboard, lambda db, d=d: queries.dashboard(db, d)),
               (f"/day/{d}", S.DayDetail, lambda db, d=d: explore.day_detail(db, d))]
    routes += [(f"/explain/{s}?date={d}", S.Explanation, lambda db, d=d, s=s: queries.explain_score(db, d, s))
               for s in ("recovery", "sleep", "strain")]
for m in queries.METRICS:
    routes.append((f"/trend/{m}?from={fr}&to={to}", S.Trend, lambda db, m=m: queries.trend(db, m, fr, to)))
for r in runs:
    routes += [(f"/runs/{r['id']}", S.RunDetail, lambda db, i=r["id"]: queries.run_detail(db, i)),
               (f"/workout-series/{r['id']}", S.WorkoutSeries, lambda db, i=r["id"]: explore.run_series(db, i))]
for e in ecgs[:3]:
    rid = e["reading_id"].rsplit("/", 1)[-1]
    routes.append((f"/ecg/{rid}", S.Ecg, lambda db, i=rid: explore.ecg_waveform(db, i)))
for t in tables:
    routes.append((f"/data/{t['source']}/{t['table']}?limit=20", S.TableRows,
                   lambda db, t=t: explore.browse(db, t["source"], t["table"], limit=20)))

failed = 0
with TestClient(appmod.app) as client:
    for url, model, fn in routes:
        r = client.get(url)
        problems = []
        if r.status_code == 200:
            with queries.Db() as db:
                raw = json.loads(json.dumps(fn(db)))
            if raw is not None:
                problems = [f"not in the model: {k}" for k in undeclared(model, raw)] + differs(raw, r.json())
        elif not (r.status_code == 404 and url.startswith("/workout-series/")):  # workouts without a Google row
            problems = [r.text[:1500]]
        failed += bool(problems)
        print(f"{'ok  ' if not problems else 'FAIL'} {r.status_code} {url}")
        for p in problems[:10]:
            print("     ", p)
shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{len(routes) - failed}/{len(routes)} routes ok")
sys.exit(1 if failed else 0)
