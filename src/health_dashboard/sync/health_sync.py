"""Sync intraday Google Health data into data/health.db, beside the MCP's own cache.

The google-health MCP keeps daily values and whole workouts. This script adds
the time series it drops: heart rate every few seconds, steps, distance, active
energy, activity level, active minutes, heart-rate zone and zone minutes per
minute, HRV and SpO2 samples, sedentary periods, altitude gain, swim lengths,
VO2 max estimates, breathing rate per sleep stage, and daily heart-rate zone
limits and calories per zone. It reuses the
MCP's login and paging (`list_google_data_points`; the google-health-mcp
package is a dependency of this one, and its login lives in
~/.config/google-health-mcp):

    python -m health_dashboard sync
    ... sync --since 2026-01-01     # backfill from a date
    ... sync --types heart-rate     # only some types

Each run resumes from the last synced day per type, re-reading the last two
days because the watch uploads late. Requests go out in parallel, gzipped and
spaced to stay inside Google's per-user quota; rows are still written a type
and a window at a time, in order. Nothing before the first watch day is
read, and each run deletes the phone's step days before it from the MCP's
cache (`google-health-mcp sync --since` would bring them back). Rows are keyed by time and source, so
re-reading never duplicates. Times are stored as UTC epoch seconds, with the
local UTC offset in seconds beside them for grouping by local day.
"""

import argparse
import gzip
import io
import sqlite3
import sys
import threading
import time
import urllib.request
import urllib.response
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from google_health_mcp.api import (
    GOOGLE_TYPES,
    HealthRateLimitError,
    daily_roll_up,
    list_google_data_points,
)
from google_health_mcp.api import _interval as _interval_type
from google_health_mcp.api import _sample as _sample_type

from ..config import get_paths
from ..settings import load_settings

OVERLAP_DAYS = 2
# Google allows an unverified app 2.5 requests a second per user, so request
# starts are spaced this far apart however many run at once.
MIN_REQUEST_GAP = 0.4
WORKERS = 6
# Seconds to wait before re-reading a window Google answered with 429.
RATE_LIMIT_WAITS = (5, 15, 30)
# MCP cache tables the phone fills on its own (steps, distance, calories)
PHONE_TABLES = ("activity",)

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
#: read it). Heart rate goes a day at a time (tens of thousands of points a day),
#: most per-minute types a week (a few thousand points, one or two pages),
#: sparse ones a month per request. Activity level and active minutes stay at a
#: day: measured, Google answers a multi-day request for them with different
#: minutes than the same days asked one by one (about 2000 of 9500 activity-level
#: rows a week differ), while the other types answer identically. The
#: calorie-zone family refuses `list`, so it is read as a daily rollup.
TYPES = {
    "heart-rate": ("hr_samples", rows_heart_rate, 4, 1, "list"),
    "steps": ("steps_minute", rows_steps, 5, 7, "list"),
    "distance": ("distance_minute", rows_distance, 5, 7, "list"),
    "activity-level": ("activity_level_minute", rows_activity_level, 5, 1, "list"),
    "heart-rate-variability": ("hrv_samples", rows_hrv, 6, 7, "list"),
    "oxygen-saturation": ("spo2_samples", rows_spo2, 4, 7, "list"),
    "active-energy-burned": ("active_energy_minute", rows_active_energy, 5, 7, "list"),
    "active-minutes": ("active_minutes_minute", rows_active_minutes, 6, 1, "list"),
    "active-zone-minutes": ("azm_minute", rows_azm, 6, 7, "list"),
    "time-in-heart-rate-zone": ("hr_zone_minute", rows_hr_zone, 5, 7, "list"),
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


class GoogleTransport(urllib.request.BaseHandler):
    """Every HTTPS request this process sends: asks for gzip (a page of heart
    rate is about 3 MB of JSON and 50 kB gzipped) and starts at most one
    request per `gap` seconds across all threads. Installed as urllib's opener,
    so the MCP's client, which calls urlopen, goes through it unchanged."""

    handler_order = 400  # before urllib's own: our User-Agent stays, error bodies arrive unzipped

    def __init__(self, gap: float = MIN_REQUEST_GAP):
        self.gap = gap
        self._lock = threading.Lock()
        self._next = 0.0

    def https_request(self, req):
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.gap
        if start > now:
            time.sleep(start - now)
        req.add_header("Accept-Encoding", "gzip")
        req.add_header("User-Agent", "health-dashboard (gzip)")  # Google wants "gzip" in it too
        return req

    def https_response(self, req, resp):
        if resp.headers.get("Content-Encoding") != "gzip":
            return resp
        try:
            body = gzip.decompress(resp.read())
        finally:
            resp.close()
        unzipped = urllib.response.addinfourl(io.BytesIO(body), resp.headers, resp.url, resp.code)
        unzipped.msg = resp.msg
        return unzipped


def windows(since: date, today: date, days: int):
    """(first, last) day pairs covering since..today, `days` at a time."""
    day = since
    while day <= today:
        last = min(day + timedelta(days=days - 1), today)
        yield day, last
        day = last + timedelta(days=1)


def start_day(conn, data_type: str, since: date | None, first_day: date) -> date:
    """Without `since`, resume after the last synced day minus the overlap, or
    start at `first_day` (the first watch day) on a first run."""
    if since is None:
        row = conn.execute(
            "SELECT synced_through FROM sync_state WHERE data_type = ?", (data_type,)
        ).fetchone()
        since = (date.fromisoformat(row[0]) - timedelta(days=OVERLAP_DAYS)) if row else first_day
    return max(since, first_day)


def fetch(data_type: str, first: date, last: date) -> list[dict]:
    """Every point of one type for the days first..last, waiting out a 429."""
    read = daily_roll_up if TYPES[data_type][4] == "rollup" else list_google_data_points
    for wait in (*RATE_LIMIT_WAITS, None):
        try:
            return read(data_type, first, last + timedelta(days=1))
        except HealthRateLimitError:
            if wait is None:
                raise
            time.sleep(wait)


def store(conn, data_type: str, results) -> int:
    """Write one type's windows in order, committing and moving sync_state on
    after each, so a failed window keeps the ones before it. `results` yields
    (last day, points); an error it raises stops this type there."""
    table, build, ncols = TYPES[data_type][:3]
    sql = f"INSERT OR REPLACE INTO {table} VALUES ({', '.join('?' * ncols)})"
    total = 0
    for last, points in results:
        conn.executemany(sql, build(points))
        conn.execute(
            "INSERT OR REPLACE INTO sync_state VALUES (?, ?, ?)",
            (data_type, last.isoformat(), datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        total += len(points)
    return total


def sync_types(conn, types, since: date | None, today: date, first_day: date,
               workers: int = WORKERS):
    """Read `types` into health.db. Every window of every type is fetched in
    parallel, and each type is stored in order as its windows arrive, on this
    thread (the only one touching the database). Yields (type, points, error)."""
    with ThreadPoolExecutor(workers) as pool:
        pending = {
            data_type: [
                (last, pool.submit(fetch, data_type, first, last))
                for first, last in windows(start_day(conn, data_type, since, first_day),
                                           today, TYPES[data_type][3])
            ]
            for data_type in types
        }
        for data_type in types:
            futures = pending[data_type]
            try:
                n, error = store(conn, data_type, ((last, f.result()) for last, f in futures)), None
            except Exception as e:  # keep going with the other types
                n, error = None, e
                for _, f in futures:
                    f.cancel()
            yield data_type, n, error


def prune_before_watch(mcp_db, first_day: date) -> int:
    """Delete the phone-only days before `first_day` from the MCP's cache."""
    if not mcp_db.exists():
        return 0
    conn = sqlite3.connect(mcp_db, timeout=30)
    try:
        n = 0
        for table in PHONE_TABLES:
            try:
                n += conn.execute(f"DELETE FROM {table} WHERE date < ?", (first_day.isoformat(),)).rowcount
            except sqlite3.OperationalError:  # table not built yet
                pass
        conn.commit()
        return n
    finally:
        conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--since", type=date.fromisoformat,
                        help="first day to (re)read; default resumes per type")
    parser.add_argument("--types", nargs="+", choices=sorted(TYPES), default=list(TYPES))
    args = parser.parse_args(argv)

    paths = get_paths()
    first_day = load_settings(paths).watch_start
    urllib.request.install_opener(urllib.request.build_opener(GoogleTransport()))
    conn = sqlite3.connect(paths.health_db)
    conn.executescript(SCHEMA)
    failed = False
    for data_type, n, error in sync_types(conn, args.types, args.since, date.today(), first_day):
        if error is None:
            print(f"{data_type:24} {n:>9} points", flush=True)
        else:
            failed = True
            print(f"{data_type:24} FAILED: {error}", file=sys.stderr, flush=True)
    conn.close()
    try:
        if n := prune_before_watch(paths.mcp_db, first_day):
            print(f"pruned {n} phone-only days before {first_day} from the MCP cache")
    except sqlite3.Error as e:
        print(f"pruning the MCP cache FAILED: {e}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
