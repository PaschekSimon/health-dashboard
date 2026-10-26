"""Days before the first watch day (the phone counts steps on its own) stay out
of the ranges the app reads, and each sync deletes them from the MCP's cache."""

import shutil
import sqlite3
from datetime import timedelta

from health_dashboard.config import Paths
from health_dashboard.core import explore, queries
from health_dashboard.sync.health_sync import prune_before_watch


def _copy_with_phone_days(fx, tmp):
    """A data folder with the fixture and an MCP `activity` table holding a
    phone day before the watch and a watch day."""
    shutil.copy2(fx.health_db, tmp / "health.db")
    shutil.copy2(fx.mcp_db, tmp / "mcp.db")
    (tmp / "settings.json").write_text(f'{{"watch": "{fx.watch}", "watch_start": "{fx.start}"}}',
                                       encoding="utf-8")
    phone, watch = (fx.start - timedelta(days=20)).isoformat(), fx.start.isoformat()
    with sqlite3.connect(tmp / "mcp.db") as mcp:
        mcp.execute("CREATE TABLE activity (date TEXT PRIMARY KEY, steps INTEGER, calories_out REAL, "
                    "floors INTEGER, distance_km REAL, sedentary_minutes INTEGER)")
        mcp.executemany("INSERT INTO activity (date, steps) VALUES (?, ?)", [(phone, 60), (watch, 5000)])
    mcp.close()
    return Paths(data=tmp, mcp_db=tmp / "mcp.db"), phone, watch


def test_ranges_start_at_the_first_watch_day(fixture_data, tmp_path):
    paths, phone, watch = _copy_with_phone_days(fixture_data, tmp_path)
    with queries.Db(paths) as db:
        days = explore.activity(db, phone, watch)["days"]
        steps = queries.trend(db, "steps", phone, watch)["points"]
        monthly = explore.longterm(db)["monthly"]["steps"]
    assert [d["date"] for d in days] == [watch]
    assert [p["date"] for p in steps] == [watch]
    assert sum(m["n"] for m in monthly) == 1


def test_sync_prunes_phone_days(fixture_data, tmp_path):
    paths, phone, watch = _copy_with_phone_days(fixture_data, tmp_path)
    assert prune_before_watch(paths.mcp_db, fixture_data.start) == 1
    with sqlite3.connect(paths.mcp_db) as mcp:
        assert [r[0] for r in mcp.execute("SELECT date FROM activity")] == [watch]
    mcp.close()
