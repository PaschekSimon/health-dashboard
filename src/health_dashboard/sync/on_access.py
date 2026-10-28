"""Start the intraday sync in the background when the health data is opened.

Called from the Claude Code SessionStart hook (.claude/settings.json), so
health.db catches up whenever you look at your data instead of on a schedule
(the health-app MCP starts a full sync job itself when the data is stale). Returns at once: the sync runs in
a detached process, is skipped if one finished in the last few hours, and a
lock keeps two from running together. Output goes to data/logs/sync.log.

    python -m health_dashboard sync-on-access
"""

import argparse
import os
import subprocess
import time
from pathlib import Path

from .. import procs
from ..config import get_paths

MIN_INTERVAL = 3 * 3600  # seconds since the last finished sync
STALE_LOCK = 30 * 60  # a lock older than this is from a crashed run


def _age(path: Path) -> float:
    try:
        return time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return float("inf")


def start(paths) -> None:
    if _age(paths.last_sync) < MIN_INTERVAL:
        return
    if _age(paths.sync_lock) < STALE_LOCK:
        return
    paths.sync_lock.unlink(missing_ok=True)
    try:
        os.close(os.open(paths.sync_lock, os.O_CREAT | os.O_EXCL))
    except FileExistsError:
        return
    procs.spawn(procs.module_cmd("health_dashboard", "sync-on-access", "--run", background=True))


def run(paths) -> None:
    try:
        paths.logs.mkdir(parents=True, exist_ok=True)
        with open(paths.sync_log, "a", encoding="utf-8") as log:
            log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            log.flush()
            code = subprocess.call(procs.module_cmd("health_dashboard", "sync"),
                                   stdout=log, stderr=log, **procs.hidden())
            log.write(f"exit {code}\n")
        paths.last_sync.touch()
    finally:
        paths.sync_lock.unlink(missing_ok=True)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run", action="store_true", help="run the sync here (the detached child does this)")
    args = ap.parse_args(argv)
    paths = get_paths()
    run(paths) if args.run else start(paths)


if __name__ == "__main__":
    main()
