"""Sync and recompute jobs, recorded in the `sync_runs` table of health.db.

A sync job runs, in order:
  mcp_cache       google-health-mcp sync  (daily values, sleep, workouts in the MCP's cache)
  health_sync     health_dashboard sync   (intraday samples into health.db)
  daily_check, run_report, weekly_summary, run_scores   (derived tables)
each as its own process of this environment's Python (the google-health-mcp
package, which holds the Google login, is installed in it). A recompute job
runs only the derived steps (after a confirmed alcohol or illness annotation
changed).

Single flight: every job holds data/.sync.lock, the same lock sync-on-access
(SessionStart hook, MCP auto-sync) uses, so at most one sync runs at a time
across the hook, the MCP and this backend. The job runs in a detached child
process (`python -m health_dashboard.core.jobs <run id>`), so it survives the
API or MCP server stopping; the child keeps the lock fresh while it works and
removes it at the end.

Paths come from the `paths` argument, or from the environment when it's left
out (config.get_paths); child processes get the same paths passed on.

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

from .. import procs
from ..config import child_env, get_paths

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

# step name -> arguments of `python -m health_dashboard`; mcp_cache runs the
# google-health-mcp command installed next to this Python.
STEP_ARGS = {
    "mcp_cache": None,
    "health_sync": ["sync"],
    "daily_check": ["daily-check"],
    "run_report": ["run-report"],
    "weekly_summary": ["weekly-summary"],
    "run_scores": ["scores"],
}
SYNC_STEPS = ["mcp_cache", "health_sync"]
DERIVED_STEPS = ["daily_check", "run_report", "weekly_summary", "run_scores"]
SYNC_REASONS = ("open", "button", "mcp")


def step_command(name):
    if STEP_ARGS[name] is None:
        return [procs.tool("google-health-mcp"), "sync"]
    return procs.module_cmd("health_dashboard", *STEP_ARGS[name])


def _now():
    return datetime.now().isoformat(timespec="seconds")


def connect(paths=None):
    paths = paths or get_paths()
    conn = sqlite3.connect(paths.health_db, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------- lock ----

def _lock_age(paths):
    try:
        return time.time() - paths.sync_lock.stat().st_mtime
    except FileNotFoundError:
        return float("inf")


def lock_held(paths=None):
    return _lock_age(paths or get_paths()) < STALE_LOCK


def try_lock(paths):
    """Take .sync.lock the way sync-on-access does. True if we got it."""
    if lock_held(paths):
        return False
    paths.sync_lock.unlink(missing_ok=True)
    try:
        os.close(os.open(paths.sync_lock, os.O_CREAT | os.O_EXCL))
        return True
    except FileExistsError:
        return False


def _heartbeat(paths, stop):
    while not stop.wait(HEARTBEAT):
        try:
            os.utime(paths.sync_lock)
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


def get_run(conn, run_id, paths=None):
    _expire(conn, paths)
    return _row(conn.execute("SELECT id, reason, started, finished, status, error, origin, steps "
                             "FROM sync_runs WHERE id = ?", (run_id,)).fetchone())


def recent_runs(conn, limit=10, paths=None):
    _expire(conn, paths)
    return [_row(r) for r in conn.execute("SELECT id, reason, started, finished, status, error, origin, "
                                          "steps FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,))]


def _expire(conn, paths=None):
    """A run still marked running/queued while no lock is held died with its process."""
    if lock_held(paths):
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


def last_intraday_sync(paths=None):
    """mtime of .last_sync: the last health_sync.py run, from the hook or a full sync."""
    stamp = (paths or get_paths()).last_sync
    return datetime.fromtimestamp(stamp.stat().st_mtime) if stamp.exists() else None


def _insert(conn, reason, status, origin, error=None, finished=None):
    cur = conn.execute("INSERT INTO sync_runs (reason, started, finished, status, error, origin, steps) "
                       "VALUES (?, ?, ?, ?, ?, ?, '[]')", (reason, _now(), finished, status, error, origin))
    conn.commit()
    return cur.lastrowid


def _spawn(paths, run_id):
    # Background child with no console (pythonw on Windows, see procs.py); the
    # steps below get hidden windows too.
    procs.spawn(procs.module_cmd("health_dashboard.core.jobs", run_id, background=True), env=child_env(paths))


# --------------------------------------------------------------- start ----

def start_sync(reason, origin="api", paths=None):
    """Start a sync job. Returns the sync_runs row (status running, skipped
    or busy)."""
    if reason not in SYNC_REASONS:
        raise ValueError(f"reason must be one of {', '.join(SYNC_REASONS)}")
    paths = paths or get_paths()
    conn = connect(paths)
    try:
        _expire(conn, paths)
        if reason == "open":
            last = last_successful_sync(conn)
            if last and datetime.now() - last < OPEN_MIN_INTERVAL:
                mins = (datetime.now() - last).total_seconds() / 60
                rid = _insert(conn, reason, "skipped", origin, finished=_now(),
                              error=f"last successful sync {mins:.0f} min ago (< 15 min)")
                return get_run(conn, rid, paths)
        if not try_lock(paths):
            running = conn.execute("SELECT id FROM sync_runs WHERE status = 'running' AND reason IN "
                                   f"({', '.join('?' * len(SYNC_REASONS))}) ORDER BY id DESC LIMIT 1",
                                   SYNC_REASONS).fetchone()
            if running:
                run = get_run(conn, running[0], paths)
                run["note"] = "a sync is already running; this is its id"
                return run
            rid = _insert(conn, reason, "busy", origin, finished=_now(),
                          error="another sync (SessionStart hook / MCP auto-sync / recompute) holds .sync.lock")
            return get_run(conn, rid, paths)
        try:
            rid = _insert(conn, reason, "running", origin)
            _spawn(paths, rid)
        except Exception:
            paths.sync_lock.unlink(missing_ok=True)
            raise
        return get_run(conn, rid, paths)
    finally:
        conn.close()


def start_recompute(origin="api", paths=None):
    """Queue a rerun of the derived scripts. Coalesces with one already queued."""
    paths = paths or get_paths()
    conn = connect(paths)
    try:
        _expire(conn, paths)
        queued = conn.execute("SELECT id FROM sync_runs WHERE reason='recompute' AND status='queued' "
                              "ORDER BY id DESC LIMIT 1").fetchone()
        if queued:
            return get_run(conn, queued[0], paths)
        rid = _insert(conn, "recompute", "queued", origin)
        _spawn(paths, rid)
        return get_run(conn, rid, paths)
    finally:
        conn.close()


# ----------------------------------------------------------------- run ----

def _log(paths, text):
    paths.logs.mkdir(parents=True, exist_ok=True)
    with open(paths.sync_log, "a", encoding="utf-8") as f:
        f.write(text)


def _run_steps(paths, conn, run_id, steps):
    results, failed = [], []
    env = child_env(paths)
    for name in steps:
        t0 = time.time()
        step = {"name": name, "started": _now(), "status": "running"}
        results.append(step)
        conn.execute("UPDATE sync_runs SET steps = ? WHERE id = ?", (json.dumps(results), run_id))
        conn.commit()
        try:
            p = subprocess.run(step_command(name), env=env, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=STEP_TIMEOUT, **procs.hidden())
            code, out = p.returncode, (p.stdout or "") + (p.stderr or "")
        except Exception as e:  # timeout, missing exe
            code, out = -1, f"{type(e).__name__}: {e}"
        _log(paths, f"--- {name} (exit {code}) ---\n{out if name in ('mcp_cache', 'health_sync') or code else ''}")
        step.update(finished=_now(), seconds=round(time.time() - t0, 1), exit=code,
                    status="ok" if code == 0 else "failed")
        if code != 0:
            step["output_tail"] = out[-1500:]
            failed.append(name)
        elif name == "health_sync":
            paths.last_sync.touch()  # sync-on-access's 3 h skip sees this run too
        conn.execute("UPDATE sync_runs SET steps = ? WHERE id = ?", (json.dumps(results), run_id))
        conn.commit()
    return failed


def run_job(run_id, paths=None):
    """Body of the detached child process."""
    paths = paths or get_paths()
    conn = connect(paths)
    run = get_run(conn, run_id, paths)
    if run is None:
        return
    if run["reason"] == "recompute":
        deadline = time.time() + RECOMPUTE_WAIT
        while not try_lock(paths):
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
    threading.Thread(target=_heartbeat, args=(paths, stop), daemon=True).start()
    try:
        _log(paths, f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} backend job #{run_id} ({run['reason']}, "
                    f"{run['origin']}) ===\n")
        failed = _run_steps(paths, conn, run_id, steps)
        if not failed:
            status, error = "ok", None
        elif len(failed) == len(steps):
            status, error = "failed", "all steps failed"
        else:
            status, error = "partial", "failed: " + ", ".join(failed)
        conn.execute("UPDATE sync_runs SET status=?, error=?, finished=? WHERE id=?",
                     (status, error, _now(), run_id))
        conn.commit()
        _log(paths, f"=== job #{run_id} {status}{': ' + error if error else ''} ===\n")
    except Exception as e:
        conn.execute("UPDATE sync_runs SET status='failed', error=?, finished=? WHERE id=?",
                     (f"{type(e).__name__}: {e}", _now(), run_id))
        conn.commit()
        raise
    finally:
        stop.set()
        paths.sync_lock.unlink(missing_ok=True)
        conn.close()


if __name__ == "__main__":
    run_job(int(sys.argv[1]))
