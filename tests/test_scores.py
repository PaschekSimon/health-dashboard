"""What the scores must do on the synthetic data, whatever the exact numbers:
ranges, exclusions, partial days, the watch filter, frozen calibration."""

import sqlite3
from datetime import date, timedelta

import pytest

from conftest import run_scores
from fixture_db import make_fixture, midnight


def rows(scored, table):
    return scored["tables"][table]


def by_date(scored, table):
    return {date.fromisoformat(r["date"]): r for r in rows(scored, table)}


def test_every_table_is_filled(scored, fixture_data):
    fx = fixture_data
    assert len(rows(scored, "score_strain_daily")) == (fx.today - fx.start).days + 1
    assert len(rows(scored, "score_sleep_daily")) == len(fx.nights)
    assert len(rows(scored, "score_recovery_daily")) == len(fx.nights)
    assert {r["variant"] for r in rows(scored, "score_healthspan")} == {
        "all", "excl_illness", "last30", "last30_excl_illness"}


def test_scores_stay_in_range(scored):
    for r in rows(scored, "score_strain_daily") + rows(scored, "score_strain_workout"):
        assert 0 <= r["strain"] <= 21
    for r in rows(scored, "score_sleep_daily"):
        assert 0 <= r["sleep_performance"] <= 100
    rec = [r["recovery"] for r in rows(scored, "score_recovery_daily") if r["recovery"] is not None]
    assert len(rec) > 25 and all(1 <= x <= 99 for x in rec)


def test_recovery_needs_a_baseline_first(scored, fixture_data):
    """No Recovery until 7 eligible nights exist; provisional below 14."""
    rec = rows(scored, "score_recovery_daily")
    assert all(r["recovery"] is None for r in rec[:7])
    assert rec[7]["recovery"] is not None
    assert all(r["provisional"] == (1 if r["baseline_n"] < 14 else 0) for r in rec)


def test_confirmed_annotations_are_excluded(scored, fixture_data):
    fx = fixture_data
    excluded = {d for d, r in by_date(scored, "score_recovery_daily").items() if r["excluded_event"]}
    ill = {fx.illness[0] + timedelta(days=i) for i in range((fx.illness[1] - fx.illness[0]).days + 1)}
    # the suggested alcohol annotation and the confirmed note change nothing
    assert excluded == ill | {fx.alcohol_night}


def test_bad_nights_score_red(scored, fixture_data):
    fx = fixture_data
    rec = by_date(scored, "score_recovery_daily")
    assert rec[fx.alcohol_night]["band"] == "red"
    assert rec[fx.illness[0]]["band"] == "red"
    assert "skin temp" in rec[fx.illness[0]]["flags"]
    # excluded nights don't count towards the next nights' baselines
    after = fx.illness[1] + timedelta(days=1)
    assert rec[after]["baseline_n"] == rec[fx.illness[0]]["baseline_n"]


def test_partial_days(scored, fixture_data):
    """Today and a day with the watch off most of the time don't count as full days."""
    partial = {d for d, r in by_date(scored, "score_strain_daily").items() if r["partial"]}
    assert partial == {fixture_data.today, fixture_data.start + timedelta(days=30)}


def test_only_the_watch_workouts_are_scored(scored):
    ids = [w["log_id"] for w in rows(scored, "score_strain_workout")]
    assert ids and all(i.startswith("fx-") for i in ids)  # no Strava copies
    strength = [w for w in rows(scored, "score_strain_workout") if w["log_type"] == "STRENGTH_TRAINING"]
    assert strength and all(w["muscular_load"] == pytest.approx(w["minutes"] * 0.5, abs=0.05) for w in strength)


def test_google_hrv_missing_falls_back_to_deep_sleep(scored, fixture_data):
    rec = by_date(scored, "score_recovery_daily")
    sources = {r["hrv_source"] for r in rec.values()}
    assert rec[fixture_data.start + timedelta(days=18)]["hrv_source"] in ("last_deep", "deep_median", "sleep_median")
    assert sources - {"google_night"}


def test_sleep_need_adds_naps_and_debt(scored, fixture_data):
    sleep = by_date(scored, "score_sleep_daily")
    after_nap = [d for d in sleep if (d - timedelta(days=1)).weekday() == 6]
    assert after_nap and all(sleep[d]["nap_prev_day_min"] > 0 for d in after_nap)
    assert any(r["need_debt_min"] > 0 for r in sleep.values())


def test_calibration_is_frozen(scored, fixture_data):
    """A second run with the saved scores_config.json reproduces the first one."""
    assert run_scores(fixture_data) == scored


def test_fixture_is_deterministic(fixture_data, tmp_path):
    """Same seed, same data; a longer history starts with the same days."""
    def day_rows(db, d):
        conn = sqlite3.connect(db)
        try:
            return conn.execute("SELECT * FROM hr_samples WHERE t >= ? AND t < ? ORDER BY t",
                                (midnight(d), midnight(d + timedelta(days=1)))).fetchall()
        finally:
            conn.close()

    days = (fixture_data.today - fixture_data.start).days
    longer = make_fixture(tmp_path, days=days + 5, seed=fixture_data.seed)
    d = fixture_data.start + timedelta(days=3)
    assert len(day_rows(fixture_data.health_db, d)) > 30000
    assert day_rows(longer.health_db, d) == day_rows(fixture_data.health_db, d)


def test_healthspan_history_is_backfilled(fixture_data, tmp_path):
    """Past days without a run get the value a run on that day would have
    written; a second backfill has nothing left to do."""
    import shutil
    from dataclasses import replace

    from conftest import fixture_context
    from health_dashboard.scores import healthspan
    for p in (fixture_data.health_db, fixture_data.mcp_db):
        shutil.copy(p, tmp_path / p.name)
    fx = replace(fixture_data, dir=tmp_path, health_db=tmp_path / fixture_data.health_db.name,
                 mcp_db=tmp_path / fixture_data.mcp_db.name)
    with fixture_context(fx) as ctx:
        healthspan.compute(ctx, verbose=False)
        n = healthspan.backfill(ctx)
        again = healthspan.backfill(ctx)
        first = ctx.analysis_start + timedelta(healthspan.HISTORY_MIN_DAYS)
        hist = dict(ctx.health.execute("SELECT computed_on, value FROM score_healthspan "
                                       "WHERE metric = 'healthspan_age' AND variant = 'all'").fetchall())
        day = first + timedelta(3)
        res, _, _ = healthspan.compute(ctx, verbose=False, on=day)
    assert n == (fx.today - first).days and again == 0
    assert sorted(hist) == [(first + timedelta(i)).isoformat() for i in range(n + 1)]
    assert hist[day.isoformat()] == pytest.approx(res["all"]["age"])
    assert len(set(round(v, 6) for v in hist.values())) > 1


def test_calm_survives_nights_without_hr_or_hrv(fixture_data, tmp_path):
    """Weeks without the watch's HR and HRV (left on the charger) leave the
    next nights without a Calm baseline: those nights get no Calm, and
    scoring goes on."""
    import shutil
    from dataclasses import replace
    for p in (fixture_data.health_db, fixture_data.mcp_db):
        shutil.copy(p, tmp_path / p.name)
    fx = replace(fixture_data, dir=tmp_path, health_db=tmp_path / fixture_data.health_db.name,
                 mcp_db=tmp_path / fixture_data.mcp_db.name)
    gap_end = fx.start + timedelta(days=25)
    with sqlite3.connect(fx.health_db) as conn:
        conn.execute("DELETE FROM hr_samples WHERE t < ?", (midnight(gap_end),))
        conn.execute("DELETE FROM hrv_samples WHERE t < ?", (midnight(gap_end),))
    sleep = by_date(run_scores(fx), "score_sleep_daily")
    assert len(sleep) == len(fx.nights)
    in_gap = [d for d in sleep if d < gap_end - timedelta(days=1)]
    after = [d for d in sleep if d > gap_end]
    assert in_gap and all(sleep[d]["calm"] is None for d in in_gap)  # no data, no Calm (not a calm 100)
    assert after and all(sleep[d]["calm"] is not None for d in after)  # baselined on the nights with data
