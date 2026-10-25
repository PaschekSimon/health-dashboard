"""Sync intraday Google Health data into data/health.db, beside the MCP's own cache.

The google-health MCP keeps daily values and whole workouts. This script adds
the time series it drops: heart rate every few seconds, steps, distance, active
energy, activity level, active minutes, heart-rate zone and zone minutes per
minute, HRV and SpO2 samples, sedentary periods, altitude gain, swim lengths,
VO2 max estimates, breathing rate per sleep stage, and daily heart-rate zone
limits and calories per zone. It reuses the
MCP's login and paging (`list_google_data_points`), so run it with the MCP's
Python:

    %APPDATA%\\uv\\tools\\google-health-mcp\\Scripts\\python.exe health_sync.py
    ... health_sync.py --since 2026-01-01     # backfill from a date
    ... health_sync.py --types heart-rate     # only some types

Each run resumes from the last synced day per type, re-reading the last two
days because the watch uploads late. Rows are keyed by time and source, so
re-reading never duplicates. Times are stored as UTC epoch seconds, with the
local UTC offset in seconds beside them for grouping by local day.
"""

import argparse
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir
import user_settings
from google_health_mcp.api import GOOGLE_TYPES, daily_roll_up, list_google_data_points
from google_health_mcp.api import _interval as _interval_type, _sample as _sample_type

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "health.db"
DEFAULT_SINCE = user_settings.WATCH_START  # first run only; later runs resume per type
OVERLAP_DAYS = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS hr_samples (
    t INTEGER NOT NULL, utc_offset INTEGER, bpm INTEGER NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (t, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS steps_minute (
    t_start INTEGER NOT NULL, t_end INTEGER, utc_offset INTEGER, steps INTEGER NOT NULL,
    source TEXT NOT NULL, PRIMARY KEY (t_start, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS distance_minute (
    t_start INTEGER NOT NULL, t_end INTEGER, utc_offset INTEGER, meters REAL NOT NULL,
    source TEXT NOT NULL, PRIMARY KEY (t_start, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS activity_level_minute (
    t_start INTEGER NOT NULL, t_end INTEGER, utc_offset INTEGER, level TEXT NOT NULL,
    source TEXT NOT NULL, PRIMARY KEY (t_start, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS hrv_samples (
    t INTEGER NOT NULL, utc_offset INTEGER, rmssd_ms REAL, hf_power REAL, lf_power REAL,
    source TEXT NOT NULL, PRIMARY KEY (t, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS spo2_samples (
    t INTEGER NOT NULL, utc_offset INTEGER, percent REAL NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (t, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS active_energy_minute (
    t_start INTEGER NOT NULL, t_end INTEGER, utc_offset INTEGER, kcal REAL NOT NULL,
    source TEXT NOT NULL, PRIMARY KEY (t_start, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS active_minutes_minute (
    t_start INTEGER NOT NULL, t_end INTEGER, utc_offset INTEGER, level TEXT NOT NULL,
    minutes INTEGER NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (t_start, level, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS azm_minute (
    t_start INTEGER NOT NULL, t_end INTEGER, utc_offset INTEGER, zone TEXT NOT NULL,
    minutes INTEGER NOT NULL, source TEXT NOT NULL, PRIMARY KEY (t_start, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS hr_zone_minute (
    t_start INTEGER NOT NULL, t_end INTEGER, utc_offset INTEGER, zone TEXT NOT NULL,
    source TEXT NOT NULL, PRIMARY KEY (t_start, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS sedentary_periods (
    t_start INTEGER NOT NULL, t_end INTEGER NOT NULL, utc_offset INTEGER,
    source TEXT NOT NULL, PRIMARY KEY (t_start, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS altitude_minute (
    t_start INTEGER NOT NULL, t_end INTEGER, utc_offset INTEGER, gain_m REAL NOT NULL,
    source TEXT NOT NULL, PRIMARY KEY (t_start, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS swim_lengths (
    t_start INTEGER NOT NULL, t_end INTEGER, utc_offset INTEGER, stroke_count INTEGER,
    source TEXT NOT NULL, PRIMARY KEY (t_start, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS vo2max_samples (
    t INTEGER NOT NULL, utc_offset INTEGER, kind TEXT NOT NULL, vo2max REAL NOT NULL,
    source TEXT NOT NULL, PRIMARY KEY (t, kind, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS sleep_respiratory_rate (
    t INTEGER NOT NULL, utc_offset INTEGER, stage TEXT NOT NULL, breaths_per_min REAL,
    std_dev REAL, signal_to_noise REAL, source TEXT NOT NULL,
    PRIMARY KEY (t, stage, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS daily_hr_zones (
    date TEXT NOT NULL, zone TEXT NOT NULL, min_bpm INTEGER, max_bpm INTEGER,
    source TEXT NOT NULL, PRIMARY KEY (date, zone, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS daily_calories_in_hr_zone (
    date TEXT NOT NULL, zone TEXT NOT NULL, kcal REAL NOT NULL,
    PRIMARY KEY (date, zone)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS sync_state (
    data_type TEXT PRIMARY KEY, synced_through TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


def epoch(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def offset(value: str | None) -> int | None:
    return int(value.rstrip("s")) if value else None


def source(point: dict) -> str:
    """Which device or app recorded a point. Watch and phone both report steps
    and distance, so summing across sources double counts."""
    ds = point.get("dataSource") or {}
    device = ds.get("device") or {}
    return (
        device.get("displayName")
        or (ds.get("application") or {}).get("packageName")
        or device.get("formFactor")
        or ds.get("platform")
        or "unknown"
    )


def _sample(point, field):
    body = point[field]
    st = body["sampleTime"]
    return body, epoch(st["physicalTime"]), offset(st.get("utcOffset")), source(point)


def _interval(point, field):
    body = point[field]
    iv = body["interval"]
    return (body, epoch(iv["startTime"]), epoch(iv["endTime"]),
            offset(iv.get("startUtcOffset")), source(point))


def rows_heart_rate(points):
    for p in points:
        b, t, off, src = _sample(p, "heartRate")
        yield t, off, int(b["beatsPerMinute"]), src


def rows_hrv(points):
    for p in points:
        b, t, off, src = _sample(p, "heartRateVariability")
        meta = b.get("metadata") or {}
        yield (t, off, b.get("rootMeanSquareOfSuccessiveDifferencesMilliseconds"),
               meta.get("highFrequencyPower"), meta.get("lowFrequencyPower"), src)


def rows_spo2(points):
    for p in points:
        b, t, off, src = _sample(p, "oxygenSaturation")
        yield t, off, b["percentage"], src


def rows_steps(points):
    for p in points:
        b, start, end, off, src = _interval(p, "steps")
        yield start, end, off, int(b["count"]), src


def rows_distance(points):
    for p in points:
        b, start, end, off, src = _interval(p, "distance")
        yield start, end, off, int(b["millimeters"]) / 1000, src


def rows_activity_level(points):
    for p in points:
        b, start, end, off, src = _interval(p, "activityLevel")
        yield start, end, off, b["activityLevelType"], src


def rows_active_energy(points):
    for p in points:
        b, start, end, off, src = _interval(p, "activeEnergyBurned")
        yield start, end, off, b["kcal"], src


def rows_active_minutes(points):
    # One point can split its minute across levels, so a row per level.
    for p in points:
        b, start, end, off, src = _interval(p, "activeMinutes")
        for lv in b.get("activeMinutesByActivityLevel", []):
            yield start, end, off, lv["activityLevel"], int(lv["activeMinutes"]), src


def rows_azm(points):
    for p in points:
        b, start, end, off, src = _interval(p, "activeZoneMinutes")
        yield start, end, off, b["heartRateZone"], int(b["activeZoneMinutes"]), src


def rows_hr_zone(points):
    for p in points:
        b, start, end, off, src = _interval(p, "timeInHeartRateZone")
        yield start, end, off, b["heartRateZoneType"], src


def rows_sedentary(points):
    for p in points:
        b, start, end, off, src = _interval(p, "sedentaryPeriod")
        yield start, end, off, src


def rows_altitude(points):
    for p in points:
        b, start, end, off, src = _interval(p, "altitude")
        yield start, end, off, int(b["gainMillimeters"]) / 1000, src


def rows_swim_lengths(points):
    for p in points:
        b, start, end, off, src = _interval(p, "swimLengthsData")
        count = b.get("strokeCount")
        yield start, end, off, int(count) if count is not None else None, src


def _rows_vo2(field, kind):
    def build(points):
        for p in points:
            b, t, off, src = _sample(p, field)
            yield t, off, kind, b[field], src
    return build


def rows_sleep_respiratory(points):
    for p in points:
        b, t, off, src = _sample(p, "respiratoryRateSleepSummary")
        for key, stats in b.items():
            if key.endswith("SleepStats"):
                yield (t, off, key.removesuffix("SleepStats"), stats.get("breathsPerMinute"),
                       stats.get("standardDeviation"), stats.get("signalToNoise"), src)


def _civil_date(d: dict) -> str:
    return date(d["year"], d["month"], d["day"]).isoformat()


def rows_daily_hr_zones(points):
    for p in points:
        b = p["dailyHeartRateZones"]
        for z in b.get("heartRateZones", []):
            yield (_civil_date(b["date"]), z["heartRateZoneType"], int(z["minBeatsPerMinute"]),
                   int(z["maxBeatsPerMinute"]), source(p))


def rows_calories_in_hr_zone(points):
    for p in points:
        day = _civil_date(p["civilStartTime"]["date"])
        for z in p["caloriesInHeartRateZone"].get("caloriesInHeartRateZones", []):
            yield day, z["heartRateZone"], z["kcal"]


# Types the MCP's catalogue lacks; registered here so its pager can list them.
for _spec in (
    _interval_type("altitude", "altitude"),
    _interval_type("swim-lengths-data", "swimLengthsData"),
    _sample_type("run-vo2-max", "runVo2Max"),
    _sample_type("respiratory-rate-sleep-summary", "respiratoryRateSleepSummary"),
):
    GOOGLE_TYPES.setdefault(_spec.path, _spec)

#: API data type -> (table, row builder, column count, days per request, how to
#: read it). Dense types go a day at a time; sparse ones take a month per request.
#: The calorie-zone family refuses `list`, so it is read as a daily rollup.
TYPES = {
    "heart-rate": ("hr_samples", rows_heart_rate, 4, 1, "list"),
    "steps": ("steps_minute", rows_steps, 5, 1, "list"),
    "distance": ("distance_minute", rows_distance, 5, 1, "list"),
    "activity-level": ("activity_level_minute", rows_activity_level, 5, 1, "list"),
    "heart-rate-variability": ("hrv_samples", rows_hrv, 6, 1, "list"),
    "oxygen-saturation": ("spo2_samples", rows_spo2, 4, 1, "list"),
    "active-energy-burned": ("active_energy_minute", rows_active_energy, 5, 1, "list"),
    "active-minutes": ("active_minutes_minute", rows_active_minutes, 6, 1, "list"),
    "active-zone-minutes": ("azm_minute", rows_azm, 6, 1, "list"),
    "time-in-heart-rate-zone": ("hr_zone_minute", rows_hr_zone, 5, 1, "list"),
    "sedentary-period": ("sedentary_periods", rows_sedentary, 4, 7, "list"),
    "altitude": ("altitude_minute", rows_altitude, 5, 7, "list"),
    "swim-lengths-data": ("swim_lengths", rows_swim_lengths, 5, 7, "list"),
    "vo2-max": ("vo2max_samples", _rows_vo2("vo2Max", "vo2-max"), 5, 30, "list"),
    "run-vo2-max": ("vo2max_samples", _rows_vo2("runVo2Max", "run-vo2-max"), 5, 30, "list"),
    "respiratory-rate-sleep-summary":
        ("sleep_respiratory_rate", rows_sleep_respiratory, 7, 30, "list"),
    "daily-heart-rate-zones": ("daily_hr_zones", rows_daily_hr_zones, 5, 30, "list"),
    "calories-in-heart-rate-zone":
        ("daily_calories_in_hr_zone", rows_calories_in_hr_zone, 3, 14, "rollup"),
}


def sync_type(conn, data_type: str, since: date | None, today: date) -> int:
    table, build, ncols, window, method = TYPES[data_type]
    fetch = daily_roll_up if method == "rollup" else list_google_data_points
    if since is None:
        row = conn.execute(
            "SELECT synced_through FROM sync_state WHERE data_type = ?", (data_type,)
        ).fetchone()
        since = (date.fromisoformat(row[0]) - timedelta(days=OVERLAP_DAYS)) if row else DEFAULT_SINCE
    sql = f"INSERT OR REPLACE INTO {table} VALUES ({', '.join('?' * ncols)})"
    total = 0
    day = since
    # A day per request for dense types keeps a failed run's loss to one day and
    # the heart-rate pages (tens of thousands of points a day) bounded.
    while day <= today:
        last = min(day + timedelta(days=window - 1), today)
        points = fetch(data_type, day, last + timedelta(days=1))
        conn.executemany(sql, build(points))
        conn.execute(
            "INSERT OR REPLACE INTO sync_state VALUES (?, ?, ?)",
            (data_type, last.isoformat(), datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        total += len(points)
        day = last + timedelta(days=1)
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--since", type=date.fromisoformat,
                        help="first day to (re)read; default resumes per type")
    parser.add_argument("--types", nargs="+", choices=sorted(TYPES), default=list(TYPES))
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    today = date.today()
    failed = False
    for data_type in args.types:
        try:
            n = sync_type(conn, data_type, args.since, today)
            print(f"{data_type:24} {n:>9} points")
        except Exception as e:  # keep going with the other types
            failed = True
            print(f"{data_type:24} FAILED: {e}", file=sys.stderr)
    conn.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
