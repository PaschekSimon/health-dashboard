"""Zone minutes and easy share (backend/health_core/zones.py): the numbers the
workout screen, the dashboard and the MCP server show."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from health_core import zones  # noqa: E402

GOOGLE = {"LIGHT": [101, 120], "MODERATE": [121, 148], "VIGOROUS": [149, 172], "PEAK": [173, 202]}
RUN = {"min_light": 1.0, "min_moderate": 20.0, "min_vigorous": 8.0, "min_peak": 1.0}


def by_zone(result):
    return {z["zone"]: z for z in result["zones"]}


def test_split_is_the_middle_of_vigorous_rounded_half_up():
    assert zones.split_bpm(GOOGLE) == 161                      # (149 + 173) / 2
    assert zones.split_bpm({"VIGOROUS": [150, 0], "PEAK": [173, 0]}) == 162  # 161.5 rounds up, like Math.round
    assert zones.split_bpm({"VIGOROUS": [150, 0]}) is None


def test_bounds_are_half_open():
    b = zones.zone_bounds(GOOGLE)
    assert b == {"Z1": (None, 121), "Z2": (121, 149), "Z3": (149, 161), "Z4": (161, 173), "Z5": (173, None)}
    assert zones.zone_bounds({}) == {}


def test_vigorous_minutes_split_by_hr_points():
    # 3 of 4 vigorous-zone points below 161; points outside the zone and before the start don't count
    hr = [{"s": 0, "bpm": 150}, {"s": 10, "bpm": 155}, {"s": 20, "bpm": 160}, {"s": 30, "bpm": 165},
          {"s": 40, "bpm": 140}, {"s": 50, "bpm": 180}, {"s": -5, "bpm": 170}]
    r = zones.zone_minutes(RUN, hr, GOOGLE)
    z = by_zone(r)
    assert r["vigorous_split_by_hr"] is True and r["split_bpm"] == 161
    assert z["Z3"]["minutes"] == 6.0 and z["Z4"]["minutes"] == 2.0
    assert z["Z2"]["minutes"] == 20.0 and z["Z2"]["pct"] == 66.7
    assert sum(x["pct"] for x in r["zones"]) == pytest.approx(100, abs=0.2)
    assert (z["Z4"]["from_bpm"], z["Z4"]["to_bpm"]) == (161, 173)


def test_without_hr_all_vigorous_time_is_z3():
    r = zones.zone_minutes(RUN, [], GOOGLE)
    z = by_zone(r)
    assert r["vigorous_split_by_hr"] is False
    assert z["Z3"]["minutes"] == 8.0 and z["Z4"]["minutes"] is None and z["Z4"]["pct"] is None


def test_missing_analysis_and_zones():
    r = zones.zone_minutes({}, [], {})
    assert [z["minutes"] for z in r["zones"]] == [None] * 5
    assert all(z["from_bpm"] is None and z["to_bpm"] is None for z in r["zones"])
    zero = zones.zone_minutes({"min_light": 0, "min_moderate": 0, "min_vigorous": 0, "min_peak": 0}, [], GOOGLE)
    assert [z["pct"] for z in zero["zones"]] == [0.0, 0.0, 0.0, None, 0.0]


def test_easy_pct_is_weighted_by_minutes():
    runs = [{"minutes": 30, "pct_below_vigorous": 80.0}, {"minutes": 10, "pct_below_vigorous": 20.0},
            {"minutes": 50, "pct_below_vigorous": None}]
    assert zones.easy_pct(runs) == 65.0                         # (24 + 2) / 40
    assert zones.easy_pct([{"minutes": 30, "pct_below_vigorous": None}]) is None
    assert zones.easy_pct([]) is None


def test_running_summary():
    runs = [{"km": 3.49, "minutes": 33.12, "trimp": 36.83, "pct_below_vigorous": 68.31},
            {"km": None, "minutes": 20.0, "trimp": None, "pct_below_vigorous": None}]
    assert zones.running_summary(runs) == {"runs": 2, "km": 3.49, "minutes": 53.1, "trimp": 36.8, "easy_pct": 68.3}
    assert zones.running_summary([]) == {"runs": 0, "km": 0, "minutes": 0, "trimp": 0, "easy_pct": None}


def test_workout_window():
    t0, t1 = zones.workout_window("2026-10-24T08:15:07Z", "2026-10-24T08:48:14.835Z")
    assert t1 - t0 == 1987
    t0, t1 = zones.workout_window("2026-10-24T08:15:07Z", None)
    assert t1 - t0 == 4 * 3600
