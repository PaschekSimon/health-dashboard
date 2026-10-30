"""Backend edges outside the scores: the cross-site guard, the shared sync
lock, the job records, annotation migrations and reading without a daily
cache."""

import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from health_dashboard import app as appmod
from health_dashboard.config import Paths
from health_dashboard.core import annotations as ann
from health_dashboard.core import explore, jobs, queries

# ------------------------------------------------------------- guard ----


@pytest.mark.parametrize("origin", ["http://203.0.113.5", "http://8.8.8.8:8765", "http://[2001:db8::1]"])
def test_writes_from_a_public_ip_origin_are_refused(origin):
    r = TestClient(appmod.app).post("/annotations/999999/confirm", headers={"Origin": origin})
    assert r.status_code == 403


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8765", "http://localhost:5173", "https://pc.tail1234.ts.net",
                                    "http://pc:8765", "http://100.101.102.103:8765", "http://192.168.1.20:8765"])
def test_writes_from_our_own_origins_pass_the_guard(origin):
    assert appmod.allowed_origin(origin)


def test_public_ip_host_header_is_still_allowed():
    # an IP literal can't be DNS-rebound, so reaching the API by IP is fine
    assert appmod.allowed_host("203.0.113.5")


# -------------------------------------------------------------- lock ----


def _paths(tmp_path):
    return Paths(data=tmp_path, mcp_db=tmp_path / "mcp.db")


def test_a_fresh_lock_taken_during_a_stale_takeover_is_kept(tmp_path, monkeypatch):
    """Two processes see the same stale lock. The first replaces it with its
    own fresh one; the second, still acting on what it saw, must not take that."""
    paths = _paths(tmp_path)
    paths.sync_lock.touch()  # the first process's fresh lock
    real_time, calls = time.time, []

    def time_seen_stale_once():  # the second process looked while the lock was still the stale one
        calls.append(1)
        return real_time() + (jobs.STALE_LOCK + 60 if len(calls) == 1 else 0)

    monkeypatch.setattr(time, "time", time_seen_stale_once)
    assert not jobs.try_lock(paths)
    monkeypatch.setattr(time, "time", real_time)
    assert paths.sync_lock.exists() and jobs.lock_held(paths)


def test_a_stale_lock_is_taken_over_once(tmp_path):
    paths = _paths(tmp_path)
    paths.sync_lock.touch()
    old = time.time() - jobs.STALE_LOCK - 60
    os.utime(paths.sync_lock, (old, old))
    assert jobs.try_lock(paths)
    assert not jobs.try_lock(paths)
    assert jobs.lock_held(paths)
    assert [p.name for p in tmp_path.iterdir()] == [".sync.lock"]


# ------------------------------------------------------------ records ----


def test_a_dead_queued_recompute_does_not_swallow_new_ones(tmp_path, monkeypatch):
    """A queued recompute whose waiting process died stops being reused,
    even while a sync holds the lock."""
    paths = _paths(tmp_path)
    monkeypatch.setattr(jobs, "_spawn", lambda paths, run_id: None)
    paths.sync_lock.touch()  # a sync is running
    conn = jobs.connect(paths)
    long_ago = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO sync_runs (reason, started, status, origin, steps) "
                 "VALUES ('recompute', ?, 'queued', 'api', '[]')", (long_ago,))
    conn.commit()
    dead = conn.execute("SELECT max(id) FROM sync_runs").fetchone()[0]
    conn.close()

    run = jobs.start_recompute(paths=paths)
    assert run["id"] != dead and run["status"] == "queued"
    conn = jobs.connect(paths)
    assert jobs.get_run(conn, dead, paths)["status"] == "interrupted"
    # the new one is waiting and alive: further changes join it
    assert jobs.start_recompute(paths=paths)["id"] == run["id"]
    conn.close()


# -------------------------------------------------------- annotations ----

FIRST_VERSION = """
CREATE TABLE annotations (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, start TEXT NOT NULL, "end" TEXT, target TEXT,
    attrs TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested', 'confirmed', 'rejected')),
    source TEXT NOT NULL CHECK (source IN ('simon', 'ai', 'strava', 'import')),
    confidence REAL, raw_text TEXT, note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


def test_first_version_annotations_with_old_source_migrate(tmp_path):
    conn = ann.connect(tmp_path / "health.db")
    conn.executescript(FIRST_VERSION)
    conn.execute("INSERT INTO annotations (kind, start, source, created_at, updated_at) "
                 "VALUES ('illness', '2026-01-02', 'simon', 'x', 'x')")
    conn.commit()
    ann.ensure_schema(conn)
    assert conn.execute("SELECT source FROM annotations").fetchall() == [("user",)]
    assert "AUTOINCREMENT" in conn.execute("SELECT sql FROM sqlite_master WHERE name = 'annotations'").fetchone()[0]
    conn.close()


# ------------------------------------------------- without a daily cache ----


@pytest.fixture
def no_cache(fixture_data, tmp_path, monkeypatch):
    """health.db only: the daily cache (google_health.db) hasn't been written yet."""
    shutil.copy2(fixture_data.health_db, tmp_path / "health.db")
    (tmp_path / "settings.json").write_text(
        f'{{"watch": "{fixture_data.watch}", "watch_start": "{fixture_data.start}"}}', encoding="utf-8")
    monkeypatch.setenv("HEALTH_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_HEALTH_MCP_DB_PATH", str(tmp_path / "missing" / "google_health.db"))
    return tmp_path


@pytest.mark.parametrize("url", ["/status", "/dashboard", "/sleep", "/recovery", "/data"])
def test_reads_answer_before_the_daily_cache_exists(no_cache, url):
    with TestClient(appmod.app) as client:
        r = client.get(url)
    assert r.status_code == 200, r.text
    assert not Path(os.environ["GOOGLE_HEALTH_MCP_DB_PATH"]).exists()  # reading doesn't create it


def test_browsing_a_table_that_vanished_answers_empty(fixture_data, monkeypatch):
    """A table dropped between listing and reading (a rebuild running) gives
    no rows instead of a crash."""
    monkeypatch.setattr(explore, "_tables", lambda conn: ["gone"])
    paths = Paths(data=Path(fixture_data.dir), mcp_db=fixture_data.mcp_db)
    monkeypatch.setattr(queries, "load_settings", lambda p: type("S", (), {
        "watch": fixture_data.watch, "watch_start": fixture_data.start})())
    with queries.Db(paths) as db:
        out = explore.browse(db, "health", "gone")
    assert out["total"] == 0 and out["rows"] == []


def test_db_closes_what_it_opened_when_a_database_fails(tmp_path, monkeypatch):
    opened = []
    real = queries._ro

    def ro(path):
        if path.name == "google_health.db":
            raise sqlite3.OperationalError("unable to open database file")
        conn = real(path)
        opened.append(conn)
        return conn

    monkeypatch.setattr(queries, "_ro", ro)
    monkeypatch.setattr(queries, "load_settings", lambda p: type("S", (), {
        "watch": "w", "watch_start": datetime(2026, 1, 1).date()})())
    sqlite3.connect(tmp_path / "health.db").close()
    with pytest.raises(sqlite3.OperationalError):
        queries.Db(Paths(data=tmp_path, mcp_db=tmp_path / "google_health.db"))
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):  # closed
            conn.execute("SELECT 1")
