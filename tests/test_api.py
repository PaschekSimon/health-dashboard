"""Every GET route of the API on the synthetic data, through FastAPI's
TestClient: each answers 200, every key the queries return is listed in its
response model (so the generated TypeScript types know it), and the response
carries the query's answer unchanged. The checks are the ones
backend/tests/check_api_models.py runs on your real data."""

import dataclasses
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
os.environ.setdefault("APPDATA", tempfile.gettempdir())  # backend paths.py reads it at import
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "tests"))

import baseline as bl  # noqa: E402
import check_api_models as cam  # noqa: E402
import daily_check  # noqa: E402
import run_report  # noqa: E402
import weekly_summary  # noqa: E402
from conftest import run_scores, scoring_env  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from health_core import explore, paths, queries  # noqa: E402


@pytest.fixture(scope="module")
def api_data(fixture_data, tmp_path_factory):
    """A copy of the fixture with scores and the run/day/week analyses
    computed, and the backend pointed at it."""
    tmp = tmp_path_factory.mktemp("api")
    for f in (fixture_data.health_db, fixture_data.mcp_db):
        shutil.copy2(f, tmp / f.name)
    fx = dataclasses.replace(fixture_data, dir=tmp, health_db=tmp / fixture_data.health_db.name,
                             mcp_db=tmp / fixture_data.mcp_db.name)
    with sqlite3.connect(fx.mcp_db) as mcp:  # the MCP cache always has it; run_report reads GPS tracks from it
        mcp.execute("CREATE TABLE IF NOT EXISTS exercise_routes (log_id TEXT PRIMARY KEY, date TEXT NOT NULL, "
                    "tcx TEXT, provider TEXT)")
    run_scores(fx)
    with pytest.MonkeyPatch.context() as mp:
        scoring_env(mp, fx, tmp / "scores_config.json")
        mp.setattr(bl, "HEALTH_DB", fx.health_db)
        mp.setattr(bl, "MCP_DB", fx.mcp_db)
        daily_check.run()
        run_report.run()
        weekly_summary.run()
    with pytest.MonkeyPatch.context() as mp:
        for name, value in (("DATA", tmp), ("HEALTH_DB", fx.health_db), ("MCP_DB", fx.mcp_db),
                            ("SYNC_LOCK", tmp / ".sync.lock"), ("LAST_SYNC", tmp / ".last_sync")):
            mp.setattr(paths, name, value)
        for mod in (queries, explore):
            mp.setattr(mod, "WATCH", fx.watch)
        yield fx


def test_every_get_route_matches_its_model(api_data):
    todo = cam.routes(api_data.today, (api_data.today - api_data.start).days)
    assert any(url.startswith("/runs/") for url, _, _ in todo), "the fixture has no analysed runs"
    failures = {}
    with TestClient(cam.appmod.app) as client:
        for url, model, fn in todo:
            status, problems = cam.check(client, url, model, fn)
            if problems:
                failures[url] = (status, problems[:5])
    assert not failures


def test_run_detail_has_zone_minutes(api_data):
    fr, to = api_data.start.isoformat(), api_data.today.isoformat()
    with TestClient(cam.appmod.app) as client:
        runs = client.get("/runs", params={"from": fr, "to": to}).json()
        run = runs["runs"][-1]
        detail = client.get(f"/runs/{run['id']}").json()
    zones = detail["hr_zones"]["zones"]
    assert [z["zone"] for z in zones] == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    counted = sum(run[k] or 0 for k in ("min_light", "min_moderate", "min_vigorous", "min_peak"))
    assert sum(z["minutes"] or 0 for z in zones) == pytest.approx(counted, abs=0.05)
    assert runs["running"]["runs"] == len(runs["runs"])
