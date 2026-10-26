"""Incremental cache of the raw-data aggregates the scores need, in health.db.

Re-reading every heart-rate sample (one every ~2 s since the watch's first
day) was most of run_scores' time, and it grows with every day of data. This
keeps three aggregates per UTC day and rebuilds only the days whose raw rows
changed:

  score_minute_hr      per-minute median HR (>= 10 samples) + UTC offset of its
                       first sample, exactly what scores_common.minute_hr built
  score_hr_day_counts  watch HR samples per (UTC day, local date)
  score_steps_day      watch steps per (UTC day, local date)

SQLite triggers on hr_samples and steps_minute put the UTC day of every
inserted, updated or deleted row into score_dirty_days, whoever writes it
(health_sync.py, a backfill, a manual fix). refresh() rebuilds those days and
clears the marks. Everything is rebuilt from scratch when the cache is new,
its version or the watch changed, a trigger is missing, or with full=True.

The scores themselves still run over the whole history from these
aggregates: rolling baselines, the sleep-debt chain and a few whole-history
values (the Recovery fallback spread, the first nights' calm thresholds) let
one changed day move later days, and that pass takes well under a second.
So the results are the same as a full recompute by construction.
"""

import statistics

import baseline as bl

VERSION = "1"
DAY = 86400

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS score_cache_meta (key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS score_dirty_days (tbl TEXT NOT NULL, day INTEGER NOT NULL, "
    "PRIMARY KEY (tbl, day)) WITHOUT ROWID",
    # bpm has no declared type, so a median keeps its Python type (int or float)
    "CREATE TABLE IF NOT EXISTS score_minute_hr (minute INTEGER PRIMARY KEY, bpm, utc_offset)",
    "CREATE TABLE IF NOT EXISTS score_hr_day_counts (utc_day INTEGER NOT NULL, local_date TEXT, "
    "n INTEGER NOT NULL, PRIMARY KEY (utc_day, local_date))",
    "CREATE TABLE IF NOT EXISTS score_steps_day (utc_day INTEGER NOT NULL, local_date TEXT NOT NULL, "
    "steps INTEGER NOT NULL, PRIMARY KEY (utc_day, local_date)) WITHOUT ROWID",
]

# (raw table, time column, dirty-mark name)
SOURCES = (("hr_samples", "t", "hr"), ("steps_minute", "t_start", "steps"))


def _triggers(table, col, name):
    """CREATE TRIGGER statements that mark the UTC day of every changed row."""
    def mark(row):
        return f"INSERT OR IGNORE INTO score_dirty_days VALUES ('{name}', {row}.{col} / {DAY});"
    return [f"CREATE TRIGGER IF NOT EXISTS score_dirty_{name}_ins AFTER INSERT ON {table} BEGIN {mark('NEW')} END",
            f"CREATE TRIGGER IF NOT EXISTS score_dirty_{name}_del AFTER DELETE ON {table} BEGIN {mark('OLD')} END",
            f"CREATE TRIGGER IF NOT EXISTS score_dirty_{name}_upd AFTER UPDATE ON {table} "
            f"BEGIN {mark('NEW')} {mark('OLD')} END"]


TRIGGERS = [f"score_dirty_{n}_{k}" for _, _, n in SOURCES for k in ("ins", "del", "upd")]


def _meta(health):
    return dict(health.execute("SELECT key, value FROM score_cache_meta"))


def _needs_rebuild(health):
    have = {r[0] for r in health.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    m = _meta(health)
    return (m.get("version") != VERSION or m.get("watch") != bl.WATCH or m.get("complete") != "1"
            or not set(TRIGGERS) <= have)


def _raw_tables_exist(health):
    names = {r[0] for r in health.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return all(t in names for t, _, _ in SOURCES)


def _rebuild_hr_day(health, day):
    t0, t1 = day * DAY, (day + 1) * DAY
    health.execute("DELETE FROM score_minute_hr WHERE minute >= ? AND minute < ?", (t0 // 60, t1 // 60))
    health.execute("DELETE FROM score_hr_day_counts WHERE utc_day = ?", (day,))
    # same grouping as the old full scan of minute_hr: consecutive samples of
    # one minute, in time order; a UTC day holds whole minutes
    rows, cur, vals, off = [], None, [], 0
    for t, o, bpm in health.execute("SELECT t, utc_offset, bpm FROM hr_samples WHERE source = ? "
                                    "AND t >= ? AND t < ? ORDER BY t", (bl.WATCH, t0, t1)):
        m = t // 60
        if m != cur:
            if cur is not None and len(vals) >= 10:
                rows.append((cur, statistics.median(vals), off))
            cur, vals, off = m, [], o
        vals.append(bpm)
    if cur is not None and len(vals) >= 10:
        rows.append((cur, statistics.median(vals), off))
    health.executemany("INSERT INTO score_minute_hr VALUES (?, ?, ?)", rows)
    health.execute("INSERT INTO score_hr_day_counts SELECT ?, date(t + utc_offset, 'unixepoch'), count(*) "
                   "FROM hr_samples WHERE source = ? AND t >= ? AND t < ? GROUP BY 2", (day, bl.WATCH, t0, t1))


def _rebuild_steps_day(health, day):
    t0, t1 = day * DAY, (day + 1) * DAY
    health.execute("DELETE FROM score_steps_day WHERE utc_day = ?", (day,))
    steps = {}
    for t, off, n in health.execute("SELECT t_start, utc_offset, steps FROM steps_minute WHERE source = ? "
                                    "AND t_start >= ? AND t_start < ?", (bl.WATCH, t0, t1)):
        d = bl.local_date(t, off)
        steps[d] = steps.get(d, 0) + n
    health.executemany("INSERT INTO score_steps_day VALUES (?, ?, ?)",
                       [(day, d.isoformat(), n) for d, n in steps.items()])


REBUILD = {"hr": _rebuild_hr_day, "steps": _rebuild_steps_day}


def refresh(health, full=False):
    """Bring the cache up to date. Returns {"rebuilt": "full" | "incremental",
    "days": {name: n}} for the log. One IMMEDIATE transaction, so no writer
    can slip rows in between the rebuild and the trigger install."""
    health.commit()
    health.execute("BEGIN IMMEDIATE")
    try:
        if not _raw_tables_exist(health):  # no sync yet: nothing to cache
            health.rollback()
            return {"rebuilt": None, "days": {}}
        for stmt in SCHEMA:
            health.execute(stmt)
        rebuild = full or _needs_rebuild(health)
        if rebuild:
            for t in ("score_minute_hr", "score_hr_day_counts", "score_steps_day", "score_dirty_days"):
                health.execute(f"DELETE FROM {t}")
            for table, col, name in SOURCES:
                for stmt in _triggers(table, col, name):
                    health.execute(stmt)
                days = [d for (d,) in health.execute(f"SELECT DISTINCT {col} / {DAY} FROM {table} WHERE source = ?",
                                                     (bl.WATCH,))]
                health.executemany("INSERT OR IGNORE INTO score_dirty_days VALUES (?, ?)",
                                   [(name, d) for d in days])
        done = {}
        for _, _, name in SOURCES:
            days = [d for (d,) in health.execute("SELECT day FROM score_dirty_days WHERE tbl = ? ORDER BY day",
                                                 (name,))]
            for d in days:
                REBUILD[name](health, d)
            health.execute("DELETE FROM score_dirty_days WHERE tbl = ?", (name,))
            done[name] = len(days)
        health.executemany("INSERT OR REPLACE INTO score_cache_meta VALUES (?, ?)",
                           [("version", VERSION), ("watch", bl.WATCH), ("complete", "1")])
        health.commit()
    except BaseException:
        health.rollback()
        raise
    return {"rebuilt": "full" if rebuild else "incremental", "days": done}


def minute_hr(health):
    """{utc_minute: (median_bpm, utc_offset)}, in minute order."""
    return {m: (b, o) for m, b, o in health.execute("SELECT minute, bpm, utc_offset FROM score_minute_hr "
                                                    "ORDER BY minute")}


def hr_sample_counts(health):
    """{local date ISO (or None): watch HR samples that day}."""
    return dict(health.execute("SELECT local_date, sum(n) FROM score_hr_day_counts GROUP BY local_date"))


def watch_steps_by_day(health):
    """{local date: watch steps}."""
    from datetime import date
    out = {}
    for d, n in health.execute("SELECT local_date, steps FROM score_steps_day ORDER BY utc_day, local_date"):
        d = date.fromisoformat(d)
        out[d] = out.get(d, 0) + n
    return out
