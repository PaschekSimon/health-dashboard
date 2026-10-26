r"""Check the API's response models against your real data, without a running
server and without writing to it: every GET route is called through
FastAPI's TestClient on a copy of data\health.db (the MCP cache is opened
read-only, as always).

    uv run python tests\check_api_models.py [--data C:\path\to\Health\data] [--days 60]

For each route it reports the status code and two problems a 200 can hide:
- keys the query returns that no model lists (they still reach the app, but
  the generated TypeScript types don't know them),
- values that differ between the query's answer and the response.
--data is for running it from another checkout (e.g. a git worktree of a
branch) against the data folder of your main one.

tests/test_api.py runs the same checks on the synthetic test data.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import typing
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel

from health_dashboard import app as appmod
from health_dashboard.config import get_paths
from health_dashboard.core import explore, meta, queries
from health_dashboard.core import schemas as S


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


def use_data(data, tmp):
    """Point the backend (HEALTH_DATA_DIR) at a copy of data/health.db in tmp
    (read-only on the original)."""
    src = sqlite3.connect((Path(data) / "health.db").resolve().as_uri() + "?mode=ro", uri=True)
    dst = sqlite3.connect(tmp / "health.db")
    src.backup(dst)
    src.close()
    dst.close()
    os.environ["HEALTH_DATA_DIR"] = str(tmp)
    for name in (".last_sync", "scores_config.json", "settings.json"):
        if (Path(data) / name).exists():
            shutil.copy2(Path(data) / name, tmp / name)


def routes(today, days):
    """[(url, response model, function giving the raw answer from a queries.Db)] for every GET route."""
    fr, to = (today - timedelta(days=days)).isoformat(), today.isoformat()
    with queries.Db() as db:
        runs = queries.runs(db, fr, to, all_types=True)["runs"]
        ecgs = explore.longterm(db)["ecg"]
        tables = explore.catalog(db)["tables"]
    out = [("/meta", S.Meta, lambda db: meta.meta()), ("/status", S.Status, queries.status),
           ("/longterm", S.LongTerm, explore.longterm), ("/data", S.Catalog, explore.catalog),
           (f"/recovery?from={fr}&to={to}", S.RecoveryRange, lambda db: queries.recovery(db, fr, to)),
           (f"/sleep?from={fr}&to={to}", S.SleepRange, lambda db: queries.sleep(db, fr, to)),
           (f"/runs?from={fr}&to={to}&all_types=true", S.RunList, lambda db: queries.runs(db, fr, to, True)),
           (f"/activity?from={fr}&to={to}", S.ActivityRange, lambda db: explore.activity(db, fr, to)),
           (f"/training?from={fr}&to={to}", S.Training, lambda db: explore.training(db, fr, to)),
           (f"/annotations?from={fr}&to={to}", list[S.Annotation], lambda db: queries.ann.list_(db.a, fr, to))]
    for d in [(today - timedelta(days=n)).isoformat() for n in (0, 1, 7, 30)]:
        out += [(f"/dashboard?date={d}", S.Dashboard, lambda db, d=d: queries.dashboard(db, d)),
                (f"/day/{d}", S.DayDetail, lambda db, d=d: explore.day_detail(db, d))]
        out += [(f"/explain/{s}?date={d}", S.Explanation, lambda db, d=d, s=s: queries.explain_score(db, d, s))
                for s in ("recovery", "sleep", "strain")]
    for m in queries.METRICS:
        out.append((f"/trend/{m}?from={fr}&to={to}", S.Trend, lambda db, m=m: queries.trend(db, m, fr, to)))
    for r in runs:
        out += [(f"/runs/{r['id']}", S.RunDetail, lambda db, i=r["id"]: queries.run_detail(db, i)),
                (f"/workout-series/{r['id']}", S.WorkoutSeries, lambda db, i=r["id"]: explore.run_series(db, i))]
    for e in ecgs[:3]:
        rid = e["reading_id"].rsplit("/", 1)[-1]
        out.append((f"/ecg/{rid}", S.Ecg, lambda db, i=rid: explore.ecg_waveform(db, i)))
    for t in tables:
        out.append((f"/data/{t['source']}/{t['table']}?limit=20", S.TableRows,
                    lambda db, t=t: explore.browse(db, t["source"], t["table"], limit=20)))
    return out


def check(client, url, model, fn):
    """(status code, [problems]) for one route."""
    try:
        r = client.get(url)
    except Exception as e:  # a response that fails its model raises here, with the details
        return 500, [f"{type(e).__name__}: {str(e)[:1500]}"]
    if r.status_code != 200:
        # a workout without Google's record has no series
        ok = r.status_code == 404 and url.startswith("/workout-series/")
        return r.status_code, [] if ok else [r.text[:1500]]
    with queries.Db() as db:
        raw = json.loads(json.dumps(fn(db)))
    if raw is None:
        return 200, []
    return 200, [f"not in the model: {k}" for k in undeclared(model, raw)] + differs(raw, r.json())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, help="data folder to read (default: this checkout's data/)")
    ap.add_argument("--days", type=int, default=60, help="how far back the range routes look (default 60)")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="health-check-"))
    failed, todo = 0, []
    try:
        use_data(args.data or get_paths().data, tmp)
        todo = routes(date.today(), args.days)
        with TestClient(appmod.app) as client:
            for url, model, fn in todo:
                status, problems = check(client, url, model, fn)
                failed += bool(problems)
                print(f"{'ok  ' if not problems else 'FAIL'} {status} {url}")
                for p in problems[:10]:
                    print("     ", p)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(todo) - failed}/{len(todo)} routes ok")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
