"""Per-user settings, read from data/settings.json (not in git):

    {"watch": "Pixel Watch 3", "watch_start": "2026-08-19"}

watch        device name of the watch whose data is used (Google's data source
             displayName); phone steps and other devices are left out.
watch_start  first day of watch data. Missing: the first day of that watch's
             heart rate in data/health.db, or a year ago before the first sync.

Read when `load_settings()` is called, never at import.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .config import Paths, get_paths

DEFAULT_WATCH = "Pixel Watch 3"


@dataclass(frozen=True)
class UserSettings:
    watch: str
    watch_start: date


def _first_watch_day(health_db, watch):
    try:
        conn = sqlite3.connect(health_db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            t = conn.execute("SELECT min(t) FROM hr_samples WHERE source = ?", (watch,)).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        t = None
    return datetime.fromtimestamp(t).date() if t else date.today() - timedelta(days=365)


def load_settings(paths: Paths | None = None) -> UserSettings:
    paths = paths or get_paths()
    s = json.loads(paths.settings.read_text(encoding="utf-8")) if paths.settings.exists() else {}
    watch = s.get("watch", DEFAULT_WATCH)
    start = date.fromisoformat(s["watch_start"]) if s.get("watch_start") else _first_watch_day(paths.health_db, watch)
    return UserSettings(watch=watch, watch_start=start)
