"""The package runs from any data folder: paths come from the environment
when called (not at import), and the API and the jobs use them."""

import shutil
import sys

from fastapi.testclient import TestClient

from health_dashboard import procs
from health_dashboard.config import child_env, get_paths
from health_dashboard.core import jobs
from health_dashboard.settings import load_settings


def data_dir(fx, tmp_path):
    """A data folder with copies of the fixture's databases and a settings.json."""
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(fx.health_db, d / "health.db")
    shutil.copy(fx.mcp_db, d / "mcp.db")
    (d / "settings.json").write_text(f'{{"watch": "{fx.watch}", "watch_start": "{fx.start}"}}', encoding="utf-8")
    return d


def test_paths_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_HEALTH_MCP_DB_PATH", str(tmp_path / "cache.db"))
    p = get_paths()
    assert p.health_db == tmp_path / "health.db" and p.mcp_db == tmp_path / "cache.db"
    assert p.sync_lock == tmp_path / ".sync.lock" and p.sync_log == tmp_path / "logs" / "sync.log"
    env = child_env(p)
    assert env["HEALTH_DATA_DIR"] == str(tmp_path) and env["GOOGLE_HEALTH_MCP_DB_PATH"] == str(tmp_path / "cache.db")
    # no settings.json and no database yet: default watch, start a year back
    assert load_settings(p).watch == "Pixel Watch 3"


def test_job_steps_run_in_this_environment():
    for name in jobs.SYNC_STEPS + jobs.DERIVED_STEPS:
        cmd = jobs.step_command(name)
        assert cmd[:4] == [sys.executable, "-P", "-m", "health_dashboard"]
    assert jobs.step_command("mcp_cache")[4:] == ["daily-sync"]
    if not procs.WINDOWS:
        assert procs.hidden() == {} and procs.detached() == {"start_new_session": True}


def test_api_reads_the_data_folder(fixture_data, tmp_path, monkeypatch):
    d = data_dir(fixture_data, tmp_path)
    monkeypatch.setenv("HEALTH_DATA_DIR", str(d))
    monkeypatch.setenv("GOOGLE_HEALTH_MCP_DB_PATH", str(d / "mcp.db"))
    from health_dashboard.app import app
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/status").status_code == 200
        day = client.get("/dashboard", params={"date": str(fixture_data.today)})
        assert day.status_code == 200
        assert client.get("/app/").status_code in (200, 503)   # 503: web/ not built (see test_web_app)
        r = client.post("/annotations", json={"kind": "note", "start": str(fixture_data.today), "note": "test"})
        assert r.status_code == 201 and r.json()["recompute"] is None   # a note changes no score
    assert not (tmp_path / "data" / ".sync.lock").exists()


def test_web_app(tmp_path):
    """/app/ serves the built web app, with a hint until it is built; the old
    /next/ address sends you there."""
    from fastapi import FastAPI

    from health_dashboard.app import WebApp, app
    web = FastAPI()
    web.mount("/app", WebApp(directory=tmp_path / "dist", html=True, check_dir=False))
    with TestClient(web) as client:
        r = client.get("/app/")
        assert r.status_code == 503 and "npm run build" in r.text
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
        r = client.get("/app/")
        assert r.status_code == 200 and "root" in r.text
    r = TestClient(app, base_url="http://127.0.0.1").get("/next/", follow_redirects=False)   # no lifespan: no database
    assert r.status_code in (302, 307) and r.headers["location"] == "/app/"
