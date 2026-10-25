"""Start health_sync.py in the background when the health data is opened.

Called from the Claude Code SessionStart hook (Health\\.claude\\settings.json) and the
google-health MCP's daily auto-sync, so health.db catches up whenever you
look at your data instead of on a schedule. Returns at once: the sync runs in
a detached process, is skipped if one finished in the last few hours, and a
lock keeps two from running together. Output goes to data/logs/sync.log.

    %APPDATA%\\uv\\tools\\google-health-mcp\\Scripts\\pythonw.exe sync_on_access.py
"""

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
STAMP = DATA / ".last_sync"
LOCK = DATA / ".sync.lock"
LOG = DATA / "logs" / "sync.log"
MIN_INTERVAL = 3 * 3600  # seconds since the last finished sync
STALE_LOCK = 30 * 60  # a lock older than this is from a crashed run


def _age(path: Path) -> float:
    try:
        return time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return float("inf")


def start() -> None:
    if _age(STAMP) < MIN_INTERVAL:
        return
    if _age(LOCK) < STALE_LOCK:
        return
    LOCK.unlink(missing_ok=True)
    try:
        os.close(os.open(LOCK, os.O_CREAT | os.O_EXCL))
    except FileExistsError:
        return
    # pythonw: a detached python.exe (a venv launcher) opens a console window
    python = Path(sys.executable).with_name("pythonw.exe")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([str(python), "-I", __file__, "--run"], cwd=HERE, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)


def run() -> None:
    try:
        with open(LOG, "a", encoding="utf-8") as log:
            log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            log.flush()
            code = subprocess.call([sys.executable, "-I", str(HERE / "health_sync.py")], cwd=HERE,
                                   stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
            log.write(f"exit {code}\n")
        STAMP.touch()
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    run() if "--run" in sys.argv else start()
