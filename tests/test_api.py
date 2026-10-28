"""Every GET route of the API on the synthetic data, through FastAPI's
TestClient: each answers 200, every key the queries return is listed in its
response model (so the generated TypeScript types know it), and the response
carries the query's answer unchanged. The checks are the ones
tests/check_api_models.py runs on your real data."""

import dataclasses
import shutil
import sqlite3

import check_api_models as cam  # tests/ is on sys.path (pyproject.toml)
import pytest
from conftest import fixture_context, run_scores
from fastapi.testclient import TestClient

from health_dashboard.analysis import daily_check, run_report, weekly_summary


@pytest.fixture(scope="module")
def api_data(fixture_data, tmp_path_factory):
    """A data folder with a copy of the fixture, scores and the run/day/week
    analyses computed, and the backend pointed at it (HEALTH_DATA_DIR)."""
    tmp = tmp_path_factory.mktemp("api")
    fx = dataclasses.replace(fixture_data, dir=tmp, health_db=tmp / "health.db", mcp_db=tmp / "mcp.db")
    shutil.copy2(fixture_data.health_db, fx.health_db)
    shutil.copy2(fixture_data.mcp_db, fx.mcp_db)
    (tmp / "settings.json").write_text(f'{{"watch": "{fx.watch}", "watch_start": "{fx.start}"}}',
                                       encoding="utf-8")
    with sqlite3.connect(fx.mcp_db) as mcp:  # the MCP cache always has it; run_report reads GPS tracks from it
        mcp.execute("CREATE TABLE IF NOT EXISTS exercise_routes (log_id TEXT PRIMARY KEY, date TEXT NOT NULL, "
                    "tcx TEXT, provider TEXT)")
    run_scores(fx)
    with fixture_context(fx) as ctx:
        daily_check.run(ctx)
        run_report.run(ctx)
        weekly_summary.run(ctx)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("HEALTH_DATA_DIR", str(tmp))
        mp.setenv("GOOGLE_HEALTH_MCP_DB_PATH", str(fx.mcp_db))
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


def test_non_run_workouts_have_zone_minutes(api_data):
    fr, to = api_data.start.isoformat(), api_data.today.isoformat()
    with TestClient(cam.appmod.app) as client:
        rows = client.get("/runs", params={"from": fr, "to": to, "all_types": True}).json()["runs"]
        other = next(r for r in rows if r["type"] != "RUNNING")
        detail = client.get(f"/runs/{other['id']}").json()
    assert sum(z["minutes"] or 0 for z in detail["hr_zones"]["zones"]) > 0
    assert other["pct_below_vigorous"] is None  # the easy share stays a running figure
