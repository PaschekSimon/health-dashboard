"""Sync and recompute jobs, recorded in the `sync_runs` table of health.db.

A sync job runs, in order:
  mcp_cache       google-health-mcp sync  (daily values, sleep, workouts in the MCP's cache)
  health_sync     health_sync.py          (intraday samples into health.db)
  daily_check, run_report, weekly_summary, run_scores   (derived tables)
all with the google-health MCP's Python, which holds the Google login.
A recompute job runs only the derived scripts (after a confirmed alcohol or
illness annotation changed).

Single flight: every job holds Health\\data\\.sync.lock, the same lock
sync_on_access.py (SessionStart hook, MCP auto-sync) uses, so at most one sync
runs at a time across the hook, the MCP and this backend. The job runs in a
detached child process (`python -m health_core.jobs <run id>`), so it survives
the API or MCP server stopping; the child keeps the lock fresh while it works
and removes it at the end.

Rules: reason "open" is skipped if the last successful sync finished under 15
minutes ago; "button" (and "mcp") always run. A sync started while another one
holds the lock is not run (status "busy", or the id of our own running job).
A recompute waits for the lock instead (status "queued").
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import paths

OPEN_MIN_INTERVAL = timedelta(minutes=15)
STALE_LOCK = 30 * 60          # same as sync_on_access.py
HEARTBEAT = 60                # seconds between lock touches
RECOMPUTE_WAIT = 45 * 60      # how long a recompute waits for a running sync
STEP_TIMEOUT = 60 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    reason TEXT NOT NULL,
    started TEXT NOT NULL,
    finished TEXT,
    status TEXT NOT NULL,
    error TEXT,
    origin TEXT,
    steps TEXT
);
"""

SYNC_STEPS = [
    ("mcp_cache", [str(paths.MCP_CLI), "sync"]),
    ("health_sync", [str(paths.MCP_PYTHON), "-I", "health_sync.py"]),
]
DERIVED_STEPS = [
    ("daily_check", [str(paths.MCP_PYTHON), "-I", "daily_check.py"]),
    ("run_report", [str(paths.MCP_PYTHON), "-I", "run_report.py"]),
    ("weekly_summary", [str(paths.MCP_PYTHON), "-I", "weekly_summary.py"]),
    ("run_scores", [str(paths.MCP_PYTHON), "-I", "run_scores.py"]),
]
SYNC_REASONS = ("open", "button", "mcp")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(paths.HEALTH_DB, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------- lock ----

def _lock_age():
    try:
        return time.time() - paths.SYNC_LOCK.stat().st_mtime
    except FileNotFoundError:
        return float("inf")


def lock_held():
    return _lock_age() < STALE_LOCK


def try_lock():
    """Take .sync.lock the way sync_on_access.py does. True if we got it."""
    if lock_held():
        return False
    paths.SYNC_LOCK.unlink(missing_ok=True)
    try:
        os.close(os.open(paths.SYNC_LOCK, os.O_CREAT | os.O_EXCL))
        return True
    except FileExistsError:
        return False


def _heartbeat(stop):
    while not stop.wait(HEARTBEAT):
        try:
            os.utime(paths.SYNC_LOCK)
        except FileNotFoundError:
            pass


# ------------------------------------------------------------- records ----

def _row(r):
    if r is None:
        return None
    keys = ("id", "reason", "started", "finished", "status", "error", "origin", "steps")
    d = dict(zip(keys, r))
    d["steps"] = json.loads(d["steps"] or "[]")
    return d


def get_run(conn, run_id):
    _expire(conn)
    return _row(conn.execute("SELECT id, reason, started, finished, status, error, origin, steps "
                             "FROM sync_runs WHERE id = ?", (run_id,)).fetchone())


def recent_runs(conn, limit=10):
    _expire(conn)
    return [_row(r) for r in conn.execute("SELECT id, reason, started, finished, status, error, origin, "
                                          "steps FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,))]


def _expire(conn):
    """A run still marked running/queued while no lock is held died with its process."""
    if lock_held():
        return
    conn.execute("UPDATE sync_runs SET status='interrupted', finished=?, error='process ended without "
                 "finishing' WHERE status = 'running'", (_now(),))
    conn.execute("UPDATE sync_runs SET status='interrupted', finished=?, error='never got the lock' "
                 "WHERE status = 'queued' AND started < ?",
                 (_now(), (datetime.now() - timedelta(seconds=RECOMPUTE_WAIT + 300)).isoformat()))
    conn.commit()


def last_successful_sync(conn):
    """Latest finish of a successful full sync (our sync_runs). The
    SessionStart hook's .last_sync stamp doesn't count: that run only does
    health_sync.py, so the MCP cache and the scores would stay stale."""
    row = conn.execute(f"SELECT max(finished) FROM sync_runs WHERE status IN ('ok', 'partial') AND reason IN "
                       f"({', '.join('?' * len(SYNC_REASONS))})", SYNC_REASONS).fetchone()
    return datetime.fromisoformat(row[0]) if row and row[0] else None


def last_intraday_sync():
    """mtime of .last_sync: the last health_sync.py run, from the hook or a full sync."""
    return datetime.fromtimestamp(paths.LAST_SYNC.stat().st_mtime) if paths.LAST_SYNC.exists() else None


def _insert(conn, reason, status, origin, error=None, finished=None):
    cur = conn.execute("INSERT INTO sync_runs (reason, started, finished, status, error, origin, steps) "
                       "VALUES (?, ?, ?, ?, ?, ?, '[]')", (reason, _now(), finished, status, error, origin))
    conn.commit()
    return cur.lastrowid


def _spawn(run_id):
    # pythonw + no DETACHED_PROCESS: the venv's python.exe is a launcher that
    # starts the real interpreter as a child, and a detached console child
    # gets a fresh console window (Windows Terminal pops up). pythonw has no
    # console, and the steps below get hidden ones via CREATE_NO_WINDOW.
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen([str(pythonw if pythonw.exists() else sys.executable), "-m", "health_core.jobs", str(run_id)],
                     cwd=paths.BACKEND,
                     creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True)


# --------------------------------------------------------------- start ----

def start_sync(reason, origin="api"):
    """Start a sync job. Returns the sync_runs row (status running, skipped
    or busy)."""
    if reason not in SYNC_REASONS:
        raise ValueError(f"reason must be one of {', '.join(SYNC_REASONS)}")
    conn = connect()
    try:
        _expire(conn)
        if reason == "open":
            last = last_successful_sync(conn)
            if last and datetime.now() - last < OPEN_MIN_INTERVAL:
                mins = (datetime.now() - last).total_seconds() / 60
                rid = _insert(conn, reason, "skipped", origin, finished=_now(),
                              error=f"last successful sync {mins:.0f} min ago (< 15 min)")
                return get_run(conn, rid)
        if not try_lock():
            running = conn.execute("SELECT id FROM sync_runs WHERE status = 'running' AND reason IN "
                                   f"({', '.join('?' * len(SYNC_REASONS))}) ORDER BY id DESC LIMIT 1",
                                   SYNC_REASONS).fetchone()
            if running:
                run = get_run(conn, running[0])
                run["note"] = "a sync is already running; this is its id"
                return run
            rid = _insert(conn, reason, "busy", origin, finished=_now(),
                          error="another sync (SessionStart hook / MCP auto-sync / recompute) holds .sync.lock")
            return get_run(conn, rid)
        try:
            rid = _insert(conn, reason, "running", origin)
            _spawn(rid)
        except Exception:
            paths.SYNC_LOCK.unlink(missing_ok=True)
            raise
        return get_run(conn, rid)
    finally:
        conn.close()


def start_recompute(origin="api"):
    """Queue a rerun of the derived scripts. Coalesces with one already queued."""
    conn = connect()
    try:
        _expire(conn)
        queued = conn.execute("SELECT id FROM sync_runs WHERE reason='recompute' AND status='queued' "
                              "ORDER BY id DESC LIMIT 1").fetchone()
        if queued:
            return get_run(conn, queued[0])
        rid = _insert(conn, "recompute", "queued", origin)
        _spawn(rid)
        return get_run(conn, rid)
    finally:
        conn.close()


# ----------------------------------------------------------------- run ----

def _log(text):
    with open(paths.SYNC_LOG, "a", encoding="utf-8") as f:
        f.write(text)


def _run_steps(conn, run_id, steps):
    results, failed = [], []
    for name, cmd in steps:
        t0 = time.time()
        step = {"name": name, "started": _now(), "status": "running"}
        results.append(step)
        conn.execute("UPDATE sync_runs SET steps = ? WHERE id = ?", (json.dumps(results), run_id))
        conn.commit()
        try:
            p = subprocess.run(cmd, cwd=paths.SCRIPTS, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=STEP_TIMEOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            code, out = p.returncode, (p.stdout or "") + (p.stderr or "")
        except Exception as e:  # timeout, missing exe
            code, out = -1, f"{type(e).__name__}: {e}"
        _log(f"--- {name} (exit {code}) ---\n{out if name in ('mcp_cache', 'health_sync') or code else ''}")
        step.update(finished=_now(), seconds=round(time.time() - t0, 1), exit=code,
                    status="ok" if code == 0 else "failed")
        if code != 0:
            step["output_tail"] = out[-1500:]
            failed.append(name)
        elif name == "health_sync":
            paths.LAST_SYNC.touch()  # sync_on_access.py's 3 h skip sees this run too
        conn.execute("UPDATE sync_runs SET steps = ? WHERE id = ?", (json.dumps(results), run_id))
        conn.commit()
    return failed


def run_job(run_id):
    """Body of the detached child process."""
    conn = connect()
    run = get_run(conn, run_id)
    if run is None:
        return
    if run["reason"] == "recompute":
        deadline = time.time() + RECOMPUTE_WAIT
        while not try_lock():
            if time.time() > deadline:
                conn.execute("UPDATE sync_runs SET status='failed', finished=?, error=? WHERE id=?",
                             (_now(), "timed out waiting for .sync.lock", run_id))
                conn.commit()
                return
            time.sleep(5)
        conn.execute("UPDATE sync_runs SET status='running', started=? WHERE id=?", (_now(), run_id))
        conn.commit()
        steps = DERIVED_STEPS
    else:
        steps = SYNC_STEPS + DERIVED_STEPS
    stop = threading.Event()
    threading.Thread(target=_heartbeat, args=(stop,), daemon=True).start()
    try:
        _log(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} backend job #{run_id} ({run['reason']}, "
             f"{run['origin']}) ===\n")
        failed = _run_steps(conn, run_id, steps)
        if not failed:
            status, error = "ok", None
        elif len(failed) == len(steps):
            status, error = "failed", "all steps failed"
        else:
            status, error = "partial", "failed: " + ", ".join(failed)
        conn.execute("UPDATE sync_runs SET status=?, error=?, finished=? WHERE id=?",
                     (status, error, _now(), run_id))
        conn.commit()
        _log(f"=== job #{run_id} {status}{': ' + error if error else ''} ===\n")
    except Exception as e:
        conn.execute("UPDATE sync_runs SET status='failed', error=?, finished=? WHERE id=?",
                     (f"{type(e).__name__}: {e}", _now(), run_id))
        conn.commit()
        raise
    finally:
        stop.set()
        paths.SYNC_LOCK.unlink(missing_ok=True)
        conn.close()


if __name__ == "__main__":
    run_job(int(sys.argv[1]))
