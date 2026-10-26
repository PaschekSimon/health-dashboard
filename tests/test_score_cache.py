"""The raw-data cache (scripts/score_cache.py) must give exactly the scores of a
full recompute, after any kind of change to the raw data: new days, the
re-read of the last 48 h with changed values, a deleted gap, an UPDATE,
moved timestamps, changed steps and a new annotation."""

import dataclasses
import shutil
import sqlite3
import statistics
from datetime import timedelta

import pytest

import baseline as bl
import score_cache
from conftest import run_scores, scoring_env
from fixture_db import make_fixture, midnight

CACHE_TABLES = ("score_cache_meta", "score_dirty_days", "score_minute_hr", "score_hr_day_counts", "score_steps_day")


def copy_fixture(fx, directory):
    directory.mkdir()
    for p in (fx.health_db, fx.mcp_db):
        shutil.copy(p, directory / p.name)
    return dataclasses.replace(fx, dir=directory, health_db=directory / fx.health_db.name,
                               mcp_db=directory / fx.mcp_db.name)


def drop_cache(db):
    conn = sqlite3.connect(db)
    for t in CACHE_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
        conn.execute(f"DROP TRIGGER {name}")
    conn.commit()
    conn.close()


def dirty_days(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT tbl, day FROM score_dirty_days ORDER BY 1, 2").fetchall()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def full_history(tmp_path_factory):
    """44 days; the first 42 are the same as a 42-day fixture's."""
    return make_fixture(tmp_path_factory.mktemp("full"), days=44)


def test_cache_matches_a_full_scan(tmp_path, full_history):
    """Per-minute HR, sample counts and steps: the same values (and types) as
    the old full scans of hr_samples and steps_minute."""
    fx = copy_fixture(full_history, tmp_path / "fx")
    with pytest.MonkeyPatch.context() as mp:
        scoring_env(mp, fx, tmp_path / "cfg.json")
        h = sqlite3.connect(fx.health_db)
        assert score_cache.refresh(h)["rebuilt"] == "full"

        # the old scores_common.minute_hr
        ref, cur, vals, off = {}, None, [], 0
        for t, o, bpm in h.execute("SELECT t, utc_offset, bpm FROM hr_samples WHERE source=? ORDER BY t",
                                   (fx.watch,)):
            m = t // 60
            if m != cur:
                if cur is not None and len(vals) >= 10:
                    ref[cur] = (statistics.median(vals), off)
                cur, vals, off = m, [], o
            vals.append(bpm)
        if cur is not None and len(vals) >= 10:
            ref[cur] = (statistics.median(vals), off)
        got = score_cache.minute_hr(h)
        assert list(got) == list(ref)
        assert [(repr(b), o) for b, o in got.values()] == [(repr(b), o) for b, o in ref.values()]

        # the old healthspan queries
        counts = dict(h.execute("SELECT date(t + utc_offset, 'unixepoch'), count(*) FROM hr_samples "
                                "WHERE source=? GROUP BY 1", (fx.watch,)))
        assert score_cache.hr_sample_counts(h) == counts
        steps = {}
        for t, o, n in h.execute("SELECT t_start, utc_offset, steps FROM steps_minute WHERE source=?",
                                 (fx.watch,)):
            d = bl.local_date(t, o)
            steps[d] = steps.get(d, 0) + n
        assert score_cache.watch_steps_by_day(h) == steps
        h.close()


def mutate(fx, full):
    """A sync and some edits on fx, with `full` holding the complete history."""
    conn = sqlite3.connect(fx.health_db)
    conn.execute("ATTACH ? AS f", (str(full.health_db),))
    last = midnight(fx.today - timedelta(days=2))
    w = fx.watch
    # the next sync: new rows plus the re-read of the last 48 h, some values changed
    conn.execute("INSERT OR REPLACE INTO hr_samples SELECT * FROM f.hr_samples WHERE t >= ?", (last - 86400,))
    conn.execute("INSERT OR REPLACE INTO steps_minute SELECT * FROM f.steps_minute WHERE t_start >= ?",
                 (last - 86400,))
    conn.execute("INSERT OR REPLACE INTO hrv_samples SELECT * FROM f.hrv_samples WHERE t >= ?", (last,))
    conn.execute("UPDATE hr_samples SET bpm = bpm + 30 WHERE source = ? AND t >= ? AND t < ? AND t % 3 = 0",
                 (w, last - 86400 + 17 * 3600, last - 86400 + 18 * 3600))
    # older days: a deleted gap, an edited hour, moved timestamps, changed steps
    d10 = midnight(fx.start + timedelta(days=10))
    conn.execute("DELETE FROM hr_samples WHERE source = ? AND t >= ? AND t < ?", (w, d10 + 8 * 3600, d10 + 10 * 3600))
    d15 = midnight(fx.start + timedelta(days=15))
    conn.execute("UPDATE hr_samples SET bpm = bpm + 25 WHERE source = ? AND t >= ? AND t < ?",
                 (w, d15 + 17 * 3600, d15 + 18 * 3600))
    conn.execute("UPDATE OR REPLACE hr_samples SET t = t + 2 * 86400 WHERE source = ? AND t >= ? AND t < ?",
                 (w, d10 + 3 * 3600, d10 + 3 * 3600 + 300))
    conn.execute("UPDATE steps_minute SET steps = steps + 40 WHERE source = ? AND t_start >= ? AND t_start < ?",
                 (w, d15, d15 + 86400))
    conn.execute("DELETE FROM steps_minute WHERE source = ? AND t_start >= ? AND t_start < ?",
                 (w, d10 + 12 * 3600, d10 + 14 * 3600))
    # a new confirmed drinking night
    night = (fx.start + timedelta(days=8)).isoformat()
    conn.execute("INSERT INTO annotations (kind, start, status, source, created_at, updated_at) "
                 "VALUES ('alcohol', ?, 'confirmed', 'user', ?, ?)", (night + "T21:00", night, night))
    conn.commit()
    conn.close()


def test_incremental_equals_full_recompute(tmp_path, full_history):
    full = full_history
    fx = copy_fixture(full, tmp_path / "inc")
    # the state before the last sync: the last 1.5 days not seen yet
    cut = midnight(fx.today - timedelta(days=2)) + 12 * 3600
    conn = sqlite3.connect(fx.health_db)
    for table, col in (("hr_samples", "t"), ("steps_minute", "t_start"), ("hrv_samples", "t")):
        conn.execute(f"DELETE FROM {table} WHERE {col} >= ?", (cut,))
    conn.commit()
    conn.close()

    cfg = tmp_path / "scores_config.json"
    run_scores(fx, cfg)                       # builds the cache
    assert dirty_days(fx.health_db) == []

    mutate(fx, full)
    marked = dirty_days(fx.health_db)
    day = lambda d: midnight(d) // 86400  # noqa: E731  (UTC day of a local date's start)
    assert {d for t, d in marked if t == "hr"} <= {day(fx.start + timedelta(days=i)) + k
                                                   for i in (10, 12, 15, 41, 42, 43, 44) for k in (-1, 0, 1)}
    assert len(marked) < 15                   # only the touched days, not the whole history
    incremental = run_scores(fx, cfg)
    assert dirty_days(fx.health_db) == []

    # the same database without any cache, scored from scratch with the same calibration
    ref = copy_fixture(fx, tmp_path / "ref")
    drop_cache(ref.health_db)
    shutil.copy(cfg, tmp_path / "ref_config.json")
    recomputed = run_scores(ref, tmp_path / "ref_config.json")

    for table, rows in recomputed["tables"].items():
        got = incremental["tables"][table]
        assert len(got) == len(rows), table
        for a, b in zip(got, rows):
            assert {k: repr(v) for k, v in a.items()} == {k: repr(v) for k, v in b.items()}, table
    assert incremental["config"] == recomputed["config"]


def test_rebuilds_when_the_cache_cannot_be_trusted(tmp_path, full_history):
    fx = copy_fixture(full_history, tmp_path / "fx")
    with pytest.MonkeyPatch.context() as mp:
        scoring_env(mp, fx, tmp_path / "cfg.json")
        h = sqlite3.connect(fx.health_db)
        assert score_cache.refresh(h)["rebuilt"] == "full"
        assert score_cache.refresh(h) == {"rebuilt": "incremental", "days": {"hr": 0, "steps": 0}}
        assert score_cache.refresh(h, full=True)["rebuilt"] == "full"
        h.execute("DROP TRIGGER score_dirty_hr_upd")      # a writer could slip past it
        h.commit()
        assert score_cache.refresh(h)["rebuilt"] == "full"
        mp.setattr(bl, "WATCH", "Other Watch")             # settings.json changed watch
        assert score_cache.refresh(h)["rebuilt"] == "full"
        assert score_cache.minute_hr(h) == {}
        h.close()
