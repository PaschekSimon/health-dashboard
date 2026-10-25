"""Heart-rate zone minutes of a workout and the easy share of runs, worked out
here once so the web app, the API and the MCP server show the same numbers.

Google reports four zones (light, moderate, vigorous, peak). The app shows
five: Z3 and Z4 split Google's vigorous zone at its middle (about 72 % of
heart-rate reserve), and Z5 is Google's peak. The minutes per zone come from
the run analysis (analysis_workouts); the vigorous minutes are shared out
between Z3 and Z4 by how the workout's 10-second HR means fall either side of
the split.

The pure functions take plain values; the two readers take a connection to
health.db, so nothing here reads files or settings at import time.
"""

import math
import sqlite3
from datetime import datetime

ZONE_NAMES = ("Z1", "Z2", "Z3", "Z4", "Z5")


def _rows(conn, sql, args=()):
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:  # table not built yet
        return []


def _epoch(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def workout_window(start_time, end_time):
    """(t0, t1) epoch seconds of a workout from Google's ISO times; without an
    end time it is cut off after 4 hours."""
    t0 = _epoch(start_time)
    return t0, _epoch(end_time) if end_time else t0 + 4 * 3600


def hr_10s(conn, t0, t1):
    """10-second HR means between t0 and t1 as [{s: seconds since t0, bpm}]."""
    return [{"s": b * 10 - t0, "bpm": round(v)}
            for b, v in _rows(conn, "SELECT t / 10 b, avg(bpm) v FROM hr_samples WHERE t BETWEEN ? AND ? "
                                    "GROUP BY b ORDER BY b", (t0, t1))]


def day_zones(conn, day):
    """Google's HR zone limits for a date, {zone: [min_bpm, max_bpm]}: that
    day's, else the latest before it, else the earliest there is."""
    return {z: [lo, hi] for z, lo, hi in _rows(
        conn, "SELECT zone, min_bpm, max_bpm FROM daily_hr_zones WHERE date = coalesce("
              "(SELECT max(date) FROM daily_hr_zones WHERE date <= ?), (SELECT min(date) FROM daily_hr_zones))",
        (day,))}


def _lower(google, zone):
    z = google.get(zone)
    return z[0] if z and z[0] is not None else None


def split_bpm(google):
    """The bpm where Z4 starts: the middle of Google's vigorous zone, rounded
    half up. None without vigorous and peak limits."""
    vig, peak = _lower(google, "VIGOROUS"), _lower(google, "PEAK")
    return math.floor((vig + peak) / 2 + 0.5) if vig is not None and peak is not None else None


def zone_bounds(google):
    """{Z1..Z5: (from_bpm, to_bpm)}, from inclusive, to exclusive, None for an
    open end; a zone is left out when Google's limits for it are missing."""
    mod, vig, peak = (_lower(google, z) for z in ("MODERATE", "VIGOROUS", "PEAK"))
    split = split_bpm(google)
    out = {}
    if "LIGHT" in google and mod is not None:
        out["Z1"] = (None, mod)
    if mod is not None and vig is not None:
        out["Z2"] = (mod, vig)
    if split is not None:
        out["Z3"] = (vig, split)
        out["Z4"] = (split, peak)
    if peak is not None:
        out["Z5"] = (peak, None)
    return out


def low_vigorous_share(hr, google):
    """Share of the vigorous-zone HR points (s >= 0) that sit below the split,
    or None when there are none to count."""
    split = split_bpm(google)
    if split is None:
        return None
    vig, peak = _lower(google, "VIGOROUS"), _lower(google, "PEAK")
    inside = [p["bpm"] for p in hr if p["s"] >= 0 and vig <= p["bpm"] < peak]
    return sum(1 for b in inside if b < split) / len(inside) if inside else None


def zone_minutes(analysis, hr, google):
    """Minutes and share of workout time per zone Z1..Z5.

    analysis: the analysis_workouts row (min_light, min_moderate, min_vigorous,
    min_peak). hr: hr_10s() of the workout. google: day_zones() for its day.
    Without HR samples in the vigorous zone all vigorous minutes count as Z3."""
    low = low_vigorous_share(hr, google)
    vig = analysis.get("min_vigorous")
    if vig is None:
        z3 = z4 = None
    elif low is None:
        z3, z4 = vig, None
    else:
        z3, z4 = vig * low, vig * (1 - low)
    minutes = dict(zip(ZONE_NAMES, (analysis.get("min_light"), analysis.get("min_moderate"), z3, z4,
                                    analysis.get("min_peak"))))
    total = sum(v or 0 for v in minutes.values())
    bounds = zone_bounds(google)
    zones = []
    for name, v in minutes.items():
        lo, hi = bounds.get(name, (None, None))
        zones.append({"zone": name, "minutes": round(v, 2) if v is not None else None,
                      "pct": (round(v / total * 100, 1) if total else 0.0) if v is not None else None,
                      "from_bpm": lo, "to_bpm": hi})
    return {"zones": zones, "split_bpm": split_bpm(google), "vigorous_split_by_hr": low is not None}


def easy_pct(runs):
    """Share of run time at or below the top of Z2 (pct_below_vigorous), in %,
    weighted by each run's minutes. Runs without the figure are left out; None
    when no run has it."""
    counted = [r for r in runs if r.get("pct_below_vigorous") is not None]
    total = sum(r.get("minutes") or 0 for r in counted)
    if not total:
        return None
    easy = sum((r.get("minutes") or 0) * r["pct_below_vigorous"] / 100 for r in counted)
    return round(easy / total * 100, 1)


def running_summary(runs):
    """Count, km, minutes, TRIMP and easy share of a list of runs."""
    return {"runs": len(runs),
            "km": round(sum(r.get("km") or 0 for r in runs), 2),
            "minutes": round(sum(r.get("minutes") or 0 for r in runs), 1),
            "trimp": round(sum(r.get("trimp") or 0 for r in runs), 1),
            "easy_pct": easy_pct(runs)}
