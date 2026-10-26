"""Unit tests for the small pure functions the scores are built from."""

import math
import statistics
from datetime import date, timedelta

import pytest

from health_dashboard.analysis import annotation_effects as ae
from health_dashboard.analysis import baseline as bl
from health_dashboard.context import ANALYSIS_DELAY, Context
from health_dashboard.scores import common as sc
from health_dashboard.scores import healthspan, recovery, strain

D = date(2026, 3, 1)


def series(values, start=D):
    return {start + timedelta(days=i): v for i, v in enumerate(values)}


def ctx_with(illness=(), alcohol=(), analysis_start=D):
    """A Context with only exclusions and dates set (no databases)."""
    return Context(paths=None, watch="Pixel Watch 3", watch_start=analysis_start - ANALYSIS_DELAY,
                   exclusions=ae.Exclusions(list(illness), set(alcohol)), health=None, mcp=None)


# ---------------------------------------------------------- statistics ---

def test_robust_center_spread():
    assert sc.robust_center_spread([1, 2, 3, 4, 100]) == (3, pytest.approx(1.4826))
    # MAD 0 -> falls back to the standard deviation
    med, spread = sc.robust_center_spread([5, 5, 5, 5, 9])
    assert med == 5 and spread == pytest.approx(statistics.stdev([5, 5, 5, 5, 9]))


def test_percentile_interpolates():
    assert sc.percentile([], 50) is None
    assert sc.percentile([4, 1, 3, 2], 50) == 2.5
    assert sc.percentile([0, 10], 95) == pytest.approx(9.5)


def test_correlations():
    assert sc.pearson([1, 2], [1, 2]) == (None, 2)              # too few pairs
    assert sc.pearson([1, 2, 3, None], [2, 4, 6, 1]) == (pytest.approx(1.0), 3)
    assert sc.pearson([1, 1, 1], [1, 2, 3]) == (None, 3)         # no variance
    rho, n = sc.spearman([1, 2, 2, 3], [10, 20, 20, 30])         # ties share a rank
    assert (rho, n) == (pytest.approx(1.0), 4)


def test_prior_eligible_skips_excluded_and_missing():
    ctx = ctx_with(illness=[(D + timedelta(2), D + timedelta(3))], alcohol={D + timedelta(5)})
    s = series([10, 11, 12, 13, None, 15, 16, 17])
    assert sc.prior_eligible(ctx, s, D + timedelta(7)) == [16, 11, 10]
    assert sc.prior_eligible(ctx, s, D + timedelta(7), n=2) == [16, 11]


def test_the_two_robust_z_sign_conventions():
    """scores.common.robust_z: positive = better when `sign` says so (Recovery).
    baseline.robust_z: positive = worse (the analyses). Same data, opposite signs."""
    ctx = ctx_with()
    vals = [60, 62, 58, 61, 59, 60, 63, 57, 60, 61, 59, 62, 58, 60, 61, 59]
    last = D + timedelta(len(vals))
    rhr = series(vals + [66])          # a high resting HR on the last night
    z_rec, n = sc.robust_z(ctx, rhr, last, -1, floor=2.0)
    assert n == len(vals) and z_rec < 0                      # Recovery: worse
    nights = {d: {"rhr": v} for d, v in rhr.items()}
    z_bl, _, _ = bl.robust_z(ctx, nights, last, "rhr")
    assert z_bl > 0                                          # analyses: worse is positive
    # floor: a flat RHR baseline still gives a finite z
    flat = series([60] * 10 + [62])
    z, _ = sc.robust_z(ctx, flat, D + timedelta(10), -1, floor=2.0)
    assert z == pytest.approx(-1.0)
    assert sc.robust_z(ctx, series([60] * 5 + [62]), D + timedelta(5)) == (None, 5)   # < 7 nights


# ---------------------------------------------------------- HR and load ---

def test_max_hr_from_karvonen_zones():
    zones = {"MODERATE": (115, 142), "VIGOROUS": (143, 176), "PEAK": (177, 220)}
    reserve = (177 - 115) / 0.45
    assert bl.max_hr(zones) == round(115 - 0.4 * reserve + reserve)
    assert bl.zone_of(150, zones) == "VIGOROUS" and bl.zone_of(100, zones) == "LIGHT"


def test_banister_trimp():
    # 10 minutes at HR reserve 0.5, one sample a second
    samples = [(t, 125) for t in range(601)]
    expected = 10 * 0.5 * 0.64 * math.exp(1.92 * 0.5)
    assert bl.banister_trimp(samples, rest=60, hr_max=190) == pytest.approx(expected)
    assert bl.banister_trimp([(0, 50), (60, 50)], rest=60, hr_max=190) == 0      # below rest
    # gaps longer than max_gap count as max_gap only
    assert bl.banister_trimp([(0, 125), (600, 125)], 60, 190) == pytest.approx(10 / 60 * 0.5 * 0.64 * math.exp(0.96))


def test_bands():
    assert [strain.band(x) for x in (None, 9.99, 10, 14, 18)] == [None, "light", "moderate", "high", "all out"]
    assert [recovery.band(x) for x in (None, 33.9, 34, 66.9, 67)] == [None, "red", "yellow", "yellow", "green"]


# ---------------------------------------------------------------- sleep ---

def test_sri_is_100_for_identical_days():
    asleep = set()
    for day in range(5):  # asleep 00:00-07:00 UTC every day
        asleep.update(range(day * 1440, day * 1440 + 420))
    assert recovery.sri(asleep, 5 * 1440 * 60, 5) == 100
    shifted = asleep | set(range(4 * 1440 + 420, 4 * 1440 + 480))   # last night one hour longer
    assert recovery.sri(shifted, 5 * 1440 * 60, 5) == pytest.approx(100 * (1 - 60 / (4 * 1440)))


def test_asleep_minutes_and_naps():
    s = {"start": 0, "end": 3600, "offset": 3600, "stages": [(0, 600, "AWAKE"), (600, 3600, "LIGHT")]}
    assert recovery.asleep_minutes(s) == (50, 10)
    assert recovery.asleep_minutes(dict(s, stages=[])) == (60, 0)
    assert not recovery.is_nap(s)                                  # 01:00 local
    assert recovery.is_nap(dict(s, start=13 * 3600))               # 14:00 local


# ----------------------------------------------------------- healthspan ---

def test_healthspan_years_are_zero_at_the_referents():
    for metric in ("vo2max", "rhr", "steps", "moderate", "vigorous", "strength"):
        assert healthspan.years_for(metric, healthspan.REF[metric]) == pytest.approx(0, abs=1e-12)
    assert healthspan.years_for("sleep_h", 8) == 0 and healthspan.years_for("sri", 80) == 0
    assert healthspan.years_for("vo2max", 41) > 0 > healthspan.years_for("vo2max", 55)
    # capped: no extra years beyond VO2_CAP, none below the RHR floor
    assert healthspan.years_for("vo2max", 70) == healthspan.years_for("vo2max", healthspan.VO2_CAP)
    assert healthspan.years_for("rhr", 40) == healthspan.years_for("rhr", healthspan.RHR_FLOOR)
    assert healthspan.years_for("steps", None) is None


def test_healthspan_overlap_discount():
    x = {"vo2max": 41, "rhr": 66, "steps": 5000, "moderate": 60, "vigorous": 75,
         "strength": 40, "sleep_h": 7.5, "sri": 80}
    yrs, adj, age = healthspan.evaluate(20, x)
    for k in ("vo2max", "rhr", "steps", "moderate"):
        assert adj[k] == pytest.approx(0.7 * yrs[k])
    assert age == pytest.approx(20 + sum(adj.values()))


# ---------------------------------------------------------- annotations ---

@pytest.mark.parametrize("kind, start, end, target, nights", [
    ("illness", "2026-03-22", "2026-03-26", None, ("2026-03-22", "2026-03-26")),
    ("illness", "2026-03-22", None, None, ("2026-03-22", "2026-03-22")),
    ("alcohol", "2026-04-04T21:30:00", None, None, ("2026-04-05", "2026-04-05")),   # evening -> next night
    ("alcohol", "2026-04-04", None, None, ("2026-04-05", "2026-04-05")),
    ("alcohol", "2026-04-05T01:30:00", None, None, ("2026-04-05", "2026-04-05")),   # after midnight
    ("alcohol", "2026-04-04T21:30:00", None, "night:2026-04-06", ("2026-04-06", "2026-04-06")),
    ("travel", "2026-04-01", "2026-04-03", None, None),                             # no effect
])
def test_nights_of(kind, start, end, target, nights):
    span = ae.nights_of(kind, start, end, target)
    assert span == (tuple(date.fromisoformat(n) for n in nights) if nights else None)
    assert ae.affects_scores(kind) == (nights is not None)


def test_load_exclusions_from_the_fixture(fixture_data, tmp_path):
    ex = ae.load_exclusions(fixture_data.health_db)
    assert ex == ae.Exclusions([fixture_data.illness], {fixture_data.alcohol_night})
    assert ex.is_ill(fixture_data.illness[0]) and ex.is_excluded(fixture_data.alcohol_night)
    assert ae.load_exclusions(tmp_path / "missing.db") == ae.Exclusions()   # nothing excluded
