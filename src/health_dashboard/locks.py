"""The lock file that keeps two syncs apart (data/.sync.lock), shared by the
backend jobs, sync-on-access and the Takeout import.

A lock is an empty file; its holder touches it while working, so one older
than `stale` seconds is from a crashed run and may be taken over. Several
processes can find the same stale lock at once: only one of them moves it
aside, and one that finds a fresh lock in its hands (taken by another in the
meantime) puts it back instead of deleting it.
"""

import os
import threading
import time
from pathlib import Path


def age(path: Path) -> float:
    """Seconds since the lock was last touched; infinite if there is none."""
    try:
        return time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return float("inf")


def _create(path: Path) -> bool:
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL))
        return True
    except (FileExistsError, PermissionError):  # Windows: one being removed right now
        return False


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:  # Windows: still open for a moment elsewhere; it's only a stray empty file
        pass


def take(path: Path, stale: float) -> bool:
    """Create the lock at `path`, taking over one older than `stale` seconds.
    True if we hold it now."""
    if _create(path):
        return True
    if age(path) < stale:
        return False
    aside = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}")
    try:
        os.replace(path, aside)  # atomic: of several takers only one moves this file
    except FileNotFoundError:
        return False  # another process took the stale lock over first
    except PermissionError:  # Windows: the holder is touching it right now, so it's alive
        return False
    if age(aside) < stale:  # what we moved was a fresh lock, taken since we looked
        try:
            os.rename(aside, path)
        except OSError:  # yet another one is there already
            _remove(aside)
        return False
    _remove(aside)
    return _create(path)
