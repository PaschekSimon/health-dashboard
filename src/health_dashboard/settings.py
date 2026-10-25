"""Per-user settings, read from data/settings.json (not in git):

    {"watch": "Pixel Watch 3", "watch_start": "2026-08-19"}

watch        device name of the watch whose data is used (Google's data source
             displayName); phone steps and other devices are left out.
watch_start  first day of watch data. Missing: the first day of that watch's
             heart rate in data/health.db, or a year ago before the first sync.
"""

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
SETTINGS = DATA / "settings.json"

_s = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
WATCH = _s.get("watch", "Pixel Watch 3")


def _first_watch_day():
    try:
        conn = sqlite3.connect((DATA / "health.db").resolve().as_uri() + "?mode=ro", uri=True)
        try:
            t = conn.execute("SELECT min(t) FROM hr_samples WHERE source = ?", (WATCH,)).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        t = None
    return datetime.fromtimestamp(t).date() if t else date.today() - timedelta(days=365)


WATCH_START = date.fromisoformat(_s["watch_start"]) if _s.get("watch_start") else _first_watch_day()
