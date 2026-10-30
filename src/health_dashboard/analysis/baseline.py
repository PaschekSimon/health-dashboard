"""Shared helpers for the analysis scripts: nightly metrics, robust baselines,
heart-rate zones and Banister TRIMP.

Data comes from two SQLite files:
  data/health.db                 - high-res samples written by health_sync.py
  the MCP cache google_health.db - daily values, sleep sessions, workouts

Times in health.db are UTC epoch seconds with the local UTC offset beside them.
A "night" is keyed by the local date you wake up on (same as the MCP's sleep
table), so night 2026-10-04 is the night from 3 to 4 October.

Only data of one watch is used (data/settings.json, see settings.py);
analyses start 6 days after its first day (Context.analysis_start) so the
first days of calibration don't skew things. Illness and alcohol nights
(confirmed annotations, see annotation_effects.py) are left out of every
baseline. Functions that need any of that take the run's Context (ctx).

    python -m health_dashboard baseline
prints the nightly table and the HR zones as a quick self-test.
"""

import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone

from ..context import Context
from ..naps import is_nap

BASELINE_MAX_NIGHTS = 42   # use up to this many eligible nights ...
BASELINE_MIN_NIGHTS = 28   # ... a baseline with fewer is marked provisional
BASELINE_FLOOR = 14        # below this, no baseline at all
BASELINE_SKIP = 2          # the last 2 nights never count towards the baseline

# Signals, and the sign that makes a positive z mean "worse than usual".
SIGNALS = {
    "rhr": +1,          # resting HR up = worse
    "ln_rmssd": -1,     # HRV down = worse
    "nrem_hr": +1,      # non-REM sleeping HR up = worse
    "breathing": +1,    # breathing rate up = worse
    "skin_temp": +1,    # skin temp above personal baseline = worse
    "sleep_min": -1,    # less sleep = worse
    "steps": -1,        # fewer steps the previous day = worse
}


def parse_time(s):
    """ISO time from the MCP ('2026-10-10T06:45:53Z') -> UTC epoch seconds."""
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def local_date(t, offset):
    return datetime.fromtimestamp(t + (offset or 0), tz=timezone.utc).date()


# ---------------------------------------------------------------- sleep ----

def sleep_sessions(ctx):
    """All watch sleep sessions (main sleep and naps) as dicts with
    start/end (epoch), offset, wake date, stages [(start, end, type)] and
    whether it is a nap (naps.is_nap)."""
    out = []
    for date_s, record in ctx.mcp.execute("SELECT date, record FROM sleep_sessions ORDER BY start_time"):
        rec = json.loads(record)
        if rec.get("dataSource", {}).get("device", {}).get("displayName") != ctx.watch:
            continue
        s = rec["sleep"]
        offset = int(s["interval"].get("startUtcOffset", "0s").rstrip("s"))
        stages = [(parse_time(x["startTime"]), parse_time(x["endTime"]), x["type"])
                  for x in s.get("stages", [])]
        out.append({
            "night": date.fromisoformat(date_s),
            "start": parse_time(s["interval"]["startTime"]),
            "end": parse_time(s["interval"]["endTime"]),
            "offset": offset,
            "stages": stages,
            "nap": is_nap(s),
        })
    return out


def main_sleep_by_night(ctx):
    """Longest sleep session per wake date, naps left out."""
    best = {}
    for s in sleep_sessions(ctx):
        if s["nap"]:
            continue
        cur = best.get(s["night"])
        if cur is None or s["end"] - s["start"] > cur["end"] - cur["start"]:
            best[s["night"]] = s
    return best


# -------------------------------------------------------- nightly table ----

def nightly_metrics(ctx):
    """{night: {signal: value}} for every night with watch data.

    rhr, nrem_hr, breathing, skin_temp and sleep_min come from the MCP's daily
    tables. ln_rmssd is computed here: ln of the mean of the 5-minute RMSSD
    samples inside the main sleep. steps = watch steps on the local day before
    the night (the day that leads into it).
    """
    health, mcp = ctx.health, ctx.mcp
    nights = {}

    def put(night, key, value):
        if value is not None:
            nights.setdefault(date.fromisoformat(night), {})[key] = value

    for d, v in mcp.execute("SELECT date, resting_hr FROM heart_rate"):
        put(d, "rhr", v)
    for d, v in mcp.execute("SELECT date, non_rem_hr FROM hrv"):
        put(d, "nrem_hr", v)
    for d, v in mcp.execute("SELECT date, breaths_per_min FROM breathing_rate"):
        put(d, "breathing", v)
    for d, v in mcp.execute("SELECT date, nightly_relative FROM skin_temperature"):
        put(d, "skin_temp", v)
    for d, v in mcp.execute("SELECT date, total_minutes FROM sleep"):
        put(d, "sleep_min", v)

    for night, s in main_sleep_by_night(ctx).items():
        vals = [r for (r,) in health.execute(
            "SELECT rmssd_ms FROM hrv_samples WHERE source=? AND t BETWEEN ? AND ? AND rmssd_ms > 0",
            (ctx.watch, s["start"], s["end"]))]
        if len(vals) >= 6:  # at least 30 minutes of HRV windows
            put(night.isoformat(), "ln_rmssd", math.log(statistics.mean(vals)))

    steps = {}
    for t, off, n in health.execute(
            "SELECT t_start, utc_offset, steps FROM steps_minute WHERE source=?", (ctx.watch,)):
        d = local_date(t, off)
        steps[d] = steps.get(d, 0) + n
    for d, n in steps.items():
        put((d + timedelta(days=1)).isoformat(), "steps", n)

    # drop "nights" that only have steps (e.g. tonight, not slept yet)
    return {k: nights[k] for k in sorted(nights)
            if k >= ctx.watch_start and set(nights[k]) - {"steps"}}


# ------------------------------------------------------ robust baseline ----

def mad(values, centre):
    return statistics.median(abs(v - centre) for v in values)


def baseline_nights(ctx, nights, night, key):
    """Values for the baseline of `night`: up to 42 most recent eligible
    nights before it, skipping the last 2 nights, illness/alcohol nights and
    anything before ctx.analysis_start."""
    cutoff = night - timedelta(days=BASELINE_SKIP)
    vals = []
    for d in sorted(nights, reverse=True):
        if d >= cutoff or d < ctx.analysis_start or ctx.is_excluded(d):
            continue
        v = nights[d].get(key)
        if v is not None:
            vals.append(v)
        if len(vals) == BASELINE_MAX_NIGHTS:
            break
    return vals


def robust_z(ctx, nights, night, key):
    """(z, median, n) with z = sign * (x - median) / (1.4826 * MAD); positive
    means worse. z is None if the night has no value or the baseline has fewer
    than BASELINE_FLOOR nights."""
    x = nights.get(night, {}).get(key)
    vals = baseline_nights(ctx, nights, night, key)
    if len(vals) < BASELINE_FLOOR:
        return None, None, len(vals)
    med = statistics.median(vals)
    if x is None:
        return None, med, len(vals)
    spread = 1.4826 * mad(vals, med)
    if spread == 0:
        return None, med, len(vals)
    return SIGNALS[key] * (x - med) / spread, med, len(vals)


# ------------------------------------------------------------- HR zones ----

def hr_zones(health, day):
    """Google's zone limits for a local day: {'LIGHT': (lo, hi), 'MODERATE':
    ..., 'VIGOROUS': ..., 'PEAK': ...}. Falls back to the nearest earlier day."""
    rows = health.execute(
        "SELECT zone, min_bpm, max_bpm FROM daily_hr_zones WHERE date = "
        "(SELECT max(date) FROM daily_hr_zones WHERE date <= ?)", (day.isoformat(),)).fetchall()
    if not rows:
        rows = health.execute(
            "SELECT zone, min_bpm, max_bpm FROM daily_hr_zones WHERE date = "
            "(SELECT min(date) FROM daily_hr_zones)").fetchall()
    return {z: (lo, hi) for z, lo, hi in rows}


def zone_of(bpm, zones):
    """Zone name for one HR value; below MODERATE counts as LIGHT."""
    for z in ("PEAK", "VIGOROUS", "MODERATE"):
        if bpm >= zones[z][0]:
            return z
    return "LIGHT"


def max_hr(zones):
    """Max HR implied by Google's zones. Since 8 Sep 2026 they are Karvonen
    zones: MODERATE starts at 40 %, VIGOROUS at 60 %, PEAK at 85 % of heart-rate
    reserve, so reserve = (PEAK - MODERATE) / 0.45 and max = rest + reserve.
    (The older zones before 8 Sep gave a lower, inconsistent value; callers
    should use the current zones.)"""
    reserve = (zones["PEAK"][0] - zones["MODERATE"][0]) / 0.45
    rest = zones["MODERATE"][0] - 0.40 * reserve
    return round(rest + reserve)


def current_max_hr(health):
    latest = health.execute("SELECT max(date) FROM daily_hr_zones").fetchone()[0]
    return max_hr(hr_zones(health, date.fromisoformat(latest)))


# ---------------------------------------------------------------- TRIMP ----

def resting_hr_for(mcp, day):
    """That day's resting HR, else the closest earlier one."""
    row = mcp.execute("SELECT resting_hr FROM heart_rate WHERE date <= ? AND resting_hr IS NOT NULL "
                      "ORDER BY date DESC LIMIT 1", (day.isoformat(),)).fetchone()
    return row[0] if row else 60


def banister_trimp(samples, rest, hr_max, max_gap=10):
    """Banister TRIMP (male weighting) from [(t, bpm), ...]:
    sum of minutes x HRr x 0.64 x e^(1.92 HRr), HRr = (HR - rest)/(max - rest).
    Each sample counts until the next one, at most `max_gap` seconds."""
    total = 0.0
    for (t0, b0), (t1, _) in zip(samples, samples[1:]):
        dt_min = min(t1 - t0, max_gap) / 60
        hrr = (b0 - rest) / (hr_max - rest)
        if hrr <= 0:
            continue
        hrr = min(hrr, 1.0)
        total += dt_min * hrr * 0.64 * math.exp(1.92 * hrr)
    return total


def hr_series(ctx, t_start, t_end):
    return ctx.health.execute("SELECT t, bpm FROM hr_samples WHERE source=? AND t BETWEEN ? AND ? ORDER BY t",
                              (ctx.watch, t_start, t_end)).fetchall()


def watch_workouts(ctx):
    """Watch-recorded workouts (the MCP also stores Strava duplicates with no
    source; those are skipped)."""
    cols = ("log_id", "date", "name", "log_type", "start_time", "end_time", "avg_hr",
            "distance_km", "metrics_summary", "exercise_events", "splits", "start_utc_offset")
    rows = ctx.mcp.execute(f"SELECT {', '.join(cols)} FROM exercises WHERE source=? ORDER BY start_time",
                           (ctx.watch,))
    out = []
    for r in rows:
        w = dict(zip(cols, r))
        w["t_start"], w["t_end"] = parse_time(w["start_time"]), parse_time(w["end_time"])
        w["offset"] = int((w["start_utc_offset"] or "0s").rstrip("s"))
        w["day"] = date.fromisoformat(w["date"])
        out.append(w)
    return out


def workout_trimp(ctx, w, hr_max):
    return banister_trimp(hr_series(ctx, w["t_start"], w["t_end"]), resting_hr_for(ctx.mcp, w["day"]), hr_max)


def daily_trimp(ctx, hr_max=None):
    """{local date: summed TRIMP of that day's watch workouts}."""
    hr_max = hr_max or current_max_hr(ctx.health)
    out = {}
    for w in watch_workouts(ctx):
        out[w["day"]] = out.get(w["day"], 0.0) + workout_trimp(ctx, w, hr_max)
    return out


# ----------------------------------------------------------- statistics ----

T95 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 6: 2.45, 7: 2.36, 8: 2.31, 9: 2.26, 10: 2.23,
       12: 2.18, 15: 2.13, 20: 2.09, 25: 2.06, 30: 2.04, 40: 2.02, 60: 2.00}


def t95(df):
    """Two-sided 95 % t quantile (table lookup, rounded down to a listed df)."""
    keys = [k for k in T95 if k <= df]
    return T95[max(keys)] if keys else None


def mean_diff_ci(a, b):
    """Welch mean difference a - b with a 95 % CI. Returns (diff, lo, hi);
    lo/hi are None when either group has fewer than 2 values."""
    if not a or not b:
        return None, None, None
    diff = statistics.mean(a) - statistics.mean(b)
    if len(a) < 2 or len(b) < 2:
        return diff, None, None
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    se = math.sqrt(va + vb)
    df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    q = t95(int(df))
    return diff, diff - q * se, diff + q * se


def main(argv=None):
    with Context.open() as ctx:
        nights = nightly_metrics(ctx)
        print("night       " + " ".join(f"{k:>9}" for k in SIGNALS))
        for d, v in list(nights.items())[-10:]:
            print(d, " ".join(f"{v.get(k, float('nan')):9.2f}" for k in SIGNALS))
        zones = hr_zones(ctx.health, ctx.today)
        print("zones today:", zones, "-> max HR", max_hr(zones))


if __name__ == "__main__":
    main()
