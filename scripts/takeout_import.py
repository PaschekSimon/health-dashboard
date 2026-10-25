"""Import the Google Health Takeout types that the API sync does not cover.

health_sync.py and the google-health MCP already store heart rate, HRV, SpO2,
steps, distance, energy, zones, sleep stages, daily summaries, workouts and
routes. The Takeout export adds sleep scores, Daily Readiness, stress (EDA and
body responses), cardio load, per-minute skin temperature, breathing quality,
running dynamics, total calories per minute, health assessments and personal
records. This loads those into health.db as takeout_* tables:

    python -I takeout_import.py                       # reads data/takeout
    python -I takeout_import.py "path/to/Takeout/Google Health"

The export is read as data only. Rows are keyed by time (or date) and source,
so importing a newer export again replaces rows instead of duplicating them.
Times are stored as UTC epoch seconds, with the local UTC offset in seconds
beside them. The export gives most times in UTC without an offset, so the offset
is taken from the nearest earlier heart-rate sample in hr_samples.
"""

import bisect
import csv
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir
import user_settings

DATA = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA / "health.db"
DEFAULT_ROOT = DATA / "takeout" / "Takeout" / "Google Health"
WATCH_START = user_settings.WATCH_START.isoformat()
PA = "Physical Activity_GoogleData"

SCHEMA = """
CREATE TABLE IF NOT EXISTS takeout_sleep_score (
    sleep_id TEXT NOT NULL PRIMARY KEY, t INTEGER NOT NULL, utc_offset INTEGER,
    algorithm TEXT, sleep_start INTEGER, sleep_end INTEGER, overall REAL, duration REAL, composition REAL, revitalization REAL,
    sleep_minutes INTEGER, deep_minutes INTEGER, rem_percent REAL, resting_hr INTEGER,
    sleep_goal_minutes INTEGER, long_wakes INTEGER, wake_minutes INTEGER,
    restlessness REAL, hr_below_resting REAL, source TEXT) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_daily_readiness (
    date TEXT NOT NULL PRIMARY KEY, score INTEGER, level TEXT, sleep_readiness TEXT,
    hrv_readiness TEXT, rhr_readiness TEXT, source TEXT) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_stress_score (
    date TEXT NOT NULL PRIMARY KEY, score INTEGER, sleep_points INTEGER,
    max_sleep_points INTEGER, responsiveness_points INTEGER,
    max_responsiveness_points INTEGER, exertion_points INTEGER,
    max_exertion_points INTEGER, status TEXT, calculation_failed INTEGER) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_eda_minute (
    t INTEGER NOT NULL, utc_offset INTEGER, eda_us REAL, slope_us_per_min REAL,
    scr_count INTEGER, leads_contact INTEGER, source TEXT NOT NULL,
    PRIMARY KEY (t, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_body_response_events (
    t_start INTEGER NOT NULL, t_end INTEGER, utc_offset INTEGER, duration_s INTEGER,
    score INTEGER, completed INTEGER, mood TEXT, source TEXT NOT NULL,
    PRIMARY KEY (t_start, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_cardio_load_minute (
    t INTEGER NOT NULL, utc_offset INTEGER, workout REAL, background REAL, total REAL,
    source TEXT NOT NULL, PRIMARY KEY (t, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_daily_cardio_load (
    date TEXT NOT NULL PRIMARY KEY, acwr REAL, acwr_label TEXT,
    min_observed_load REAL, max_observed_load REAL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_skin_temp_minute (
    t INTEGER NOT NULL, utc_offset INTEGER, celsius REAL NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (t, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_breathing_quality (
    t INTEGER NOT NULL, utc_offset INTEGER, desaturation_probability REAL,
    sleep_state TEXT, signal_quality TEXT, source TEXT NOT NULL,
    PRIMARY KEY (t, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_daily_breathing_quality (
    date TEXT NOT NULL PRIMARY KEY, sufficient_oxygen_fraction REAL, score TEXT,
    optimal_intervals INTEGER, strained_intervals INTEGER, source TEXT) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_run_samples (
    t INTEGER NOT NULL, utc_offset INTEGER, metric TEXT NOT NULL, value REAL NOT NULL,
    source TEXT NOT NULL, PRIMARY KEY (t, metric, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_calories_minute (
    t INTEGER NOT NULL, utc_offset INTEGER, kcal REAL NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (t, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_assessments (
    date TEXT NOT NULL, kind TEXT NOT NULL, classification TEXT, details TEXT,
    PRIMARY KEY (date, kind)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS takeout_personal_records (
    exercise_id TEXT NOT NULL, record TEXT NOT NULL, t INTEGER, state TEXT,
    value INTEGER, record_type TEXT, extent INTEGER,
    PRIMARY KEY (exercise_id, record)) WITHOUT ROWID;
"""


def epoch(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())


def offset_hhmm(value: str) -> int | None:
    m = re.fullmatch(r"([+-])(\d\d):(\d\d)", value or "")
    if not m:
        return None
    sign = -1 if m[1] == "-" else 1
    return sign * (int(m[2]) * 3600 + int(m[3]) * 60)


def num(value: str, kind=float):
    """Blank, NaN and the export's -1 'not computed' marker become NULL."""
    if value in ("", "NaN", "N/A", None):
        return None
    v = kind(float(value))
    return None if v == -1 else v


def day(ts: str) -> str:
    return ts[:10]


def rows(path: Path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        yield from csv.DictReader(fh)


def files(root: Path, folder: str, prefix: str = ""):
    return sorted(p for p in (root / folder).glob(f"{prefix}*.csv"))


class Offsets:
    """Local UTC offset at a moment, read from hr_samples (which has the API's
    offsets). Stored as the points where the offset changes."""

    def __init__(self, conn):
        self.times, self.values = [], []
        for t, off in conn.execute(
            "SELECT t, utc_offset FROM hr_samples WHERE utc_offset IS NOT NULL ORDER BY t"
        ):
            if not self.values or off != self.values[-1]:
                self.times.append(t)
                self.values.append(off)

    def __call__(self, t: int) -> int | None:
        if not self.times:
            return None
        i = bisect.bisect_right(self.times, t) - 1
        return self.values[max(i, 0)]


def load(conn, table, ncols, row_iter):
    sql = f"INSERT OR REPLACE INTO {table} VALUES ({', '.join('?' * ncols)})"
    conn.executemany(sql, row_iter)


def timed_series(root, folder, prefix, off, build):
    """Rows from a per-timestamp CSV family, watch era only."""
    for f in files(root, folder, prefix):
        for r in rows(f):
            if r["timestamp"][:10] < WATCH_START:
                continue
            t = epoch(r["timestamp"])
            yield build(r, t, off(t))


def import_all(conn, root: Path, off: Offsets):
    hf = root / "Health Fitness Data_GoogleData"

    # Every night is scored twice, by sleep_session_v1 and v2 (the MCP's sleep
    # table matches v2), so each score carries its session's algorithm and times.
    sessions = {}
    for f in sorted(hf.glob("UserSleeps_*.csv")):
        for r in rows(f):
            sessions[r["sleep_id"]] = (r["algorithm_version"], epoch(r["sleep_start"]),
                                       epoch(r["sleep_end"]))

    def sleep_scores():
        for f in sorted(hf.glob("UserSleepScores_*.csv")):
            for r in rows(f):
                yield (r["sleep_id"], epoch(r["score_time"]), offset_hhmm(r["score_utc_offset"]),
                       *sessions.get(r["sleep_id"], (None, None, None)),
                       num(r["overall_score"]), num(r["duration_score"]),
                       num(r["composition_score"]), num(r["revitalization_score"]),
                       num(r["sleep_time_minutes"], int), num(r["deep_sleep_minutes"], int),
                       num(r["rem_sleep_percent"]), num(r["resting_heart_rate"], int),
                       num(r["sleep_goal_minutes"], int), num(r["waso_count_long_wakes"], int),
                       num(r["waso_count_all_wake_time"], int),
                       num(r["restlessness_normalized"]), num(r["hr_below_resting_hr"]),
                       r["data_source"])
    load(conn, "takeout_sleep_score", 20, sleep_scores())

    load(conn, "takeout_daily_readiness", 7, (
        (day(r["timestamp"]), num(r["score"], int), r["type"], r["sleep readiness"],
         r["heart rate variability readiness"], r["resting heart rate readiness"],
         r["data source"])
        for f in files(root, f"{PA}/Daily Readiness") for r in rows(f)))

    load(conn, "takeout_stress_score", 10, (
        (day(r["DATE"]), num(r["STRESS_SCORE"], int), num(r["SLEEP_POINTS"], int),
         num(r["MAX_SLEEP_POINTS"], int), num(r["RESPONSIVENESS_POINTS"], int),
         num(r["MAX_RESPONSIVENESS_POINTS"], int), num(r["EXERTION_POINTS"], int),
         num(r["MAX_EXERTION_POINTS"], int), r["STATUS"],
         int(r["CALCULATION_FAILED"] == "true"))
        for f in files(root, "Stress Score") for r in rows(f)))

    load(conn, "takeout_eda_minute", 7, timed_series(
        root, f"{PA}/Continuous Eda", "", off,
        lambda r, t, o: (t, o, num(r["eda level real micro siemens"]),
                         num(r["eda slope real micro siemens per minute"]),
                         num(r["scr detection count"], int), num(r["leads contact count"], int),
                         r["data source"])))

    # Body responses: score from the Physical Activity file, end time and mood
    # from the Stress Journal summary (same events, keyed by start time).
    moods = {}
    for f in files(root, "Stress Journal", "Stress Daily Summaries"):
        for r in rows(f):
            moods[epoch(r["body_response_start"])] = (
                epoch(r["body_response_end"]), r["logged_mood"])

    def body_responses():
        for f in files(root, f"{PA}/Body Response Event"):
            for r in rows(f):
                t = epoch(r["timestamp"])
                m = re.search(r"\d+", r["Duration"])
                dur = int(m[0]) if m else None
                end, mood = moods.get(t, (t + dur if dur else None, None))
                yield (t, end, off(t), dur, num(r["Body Response Score"], int),
                       int(r["Is Completed"] == "true"), mood, r["data source"])
    load(conn, "takeout_body_response_events", 8, body_responses())

    load(conn, "takeout_cardio_load_minute", 6, timed_series(
        root, f"{PA}/Cardio Load", "", off,
        lambda r, t, o: (t, o, num(r["workout"]), num(r["background"]), num(r["total"]),
                         r["data source"])))

    daily_load = {}
    for f in files(root, f"{PA}/Cardio Acute Chronic Workload Ratio"):
        for r in rows(f):
            daily_load.setdefault(day(r["timestamp"]), [None] * 4)[:2] = [
                num(r["ratio"]), r["label"]]
    for f in files(root, f"{PA}/Cardio Load Observed Interval"):
        for r in rows(f):
            daily_load.setdefault(day(r["timestamp"]), [None] * 4)[2:] = [
                num(r["min observed load"]), num(r["max observed load"])]
    load(conn, "takeout_daily_cardio_load", 5, ((d, *v) for d, v in daily_load.items()))

    load(conn, "takeout_skin_temp_minute", 4, timed_series(
        root, f"{PA}/Body Temperature", "", off,
        lambda r, t, o: (t, o, float(r["temperature celsius"]), r["data source"])))

    load(conn, "takeout_breathing_quality", 6, timed_series(
        root, f"{PA}/Breathing Quality", "", off,
        lambda r, t, o: (t, o, num(r["oxygen desaturation probability"]), r["sleep state"],
                         r["signal quality"], r["data source"])))

    load(conn, "takeout_daily_breathing_quality", 6, (
        (day(r["timestamp"]), num(r["sufficient oxygen percentage"]),
         r["breathing quality sleep score"],
         r["optimal breathing intervals"].count("start_time"),
         r["strained breathing intervals"].count("start_time"), r["data source"])
        for f in files(root, f"{PA}/Daily Sleep Breathing Quality") for r in rows(f)))

    # Running dynamics, one row per sample and metric. Units as exported:
    # cadence steps/min, ground contact ms, stride cm, vertical oscillation cm,
    # vertical ratio (x100 %), speed m/s, live pace steps / mm / mm per sample.
    run_metrics = [
        ("Cadence", {"steps per minute": "cadence_spm"}),
        ("Ground Contact Time", {"ground contact time": "ground_contact_ms"}),
        ("Stride Length", {"stride length": "stride_cm"}),
        ("Vertical Oscillation", {"vertical oscillation": "vertical_oscillation_cm"}),
        ("Vertical Ratio", {"vertical ratio": "vertical_ratio"}),
        ("Speed", {"speed": "speed_mps"}),
        ("Live Pace", {"steps": "pace_steps", "distance millimeters": "pace_distance_mm",
                       "altitude gain millimeters": "pace_altitude_gain_mm"}),
    ]
    for folder, cols in run_metrics:
        load(conn, "takeout_run_samples", 5, (
            (t, o, metric, num(r[col]), r["data source"])
            for f in files(root, f"{PA}/{folder}")
            for r in rows(f)
            for t in [epoch(r["timestamp"])] for o in [off(t)]
            for col, metric in cols.items() if num(r[col]) is not None))

    load(conn, "takeout_calories_minute", 4, timed_series(
        root, f"{PA}/Calories", "", off,
        lambda r, t, o: (t, o, float(r["calories"]), r["data source"])))

    def assessments():
        for folder, kind in (("general-BloodPressureAssessment", "blood_pressure"),
                             ("general-BreathingQualityAssessment", "breathing_quality"),
                             ("general-DailyInsulinResistance", "insulin_resistance")):
            for f in files(root, f"{PA}/{folder}"):
                for r in rows(f):
                    cls = r.pop("classification", None) or r.pop(
                        "Breathing Quality Classification", None)
                    d = r.pop("date")
                    details = "; ".join(f"{k}={v}" for k, v in r.items() if k != "data source")
                    yield d, kind, cls, details or None
    load(conn, "takeout_assessments", 4, assessments())

    load(conn, "takeout_personal_records", 7, (
        (r["exercise_id"], r["name_localization_id"], epoch(r["achieve_time"]), r["state"],
         num(r["record_value"], int), r["personal_record_type"], num(r["extent_value"], int))
        for r in rows(hf / "PersonalRecords.csv")))


def report(conn):
    tables = [n for (n,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'takeout_%' ORDER BY name")]
    for n in tables:
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({n})")]
        key = next(c for c in ("date", "t", "t_start") if c in cols)
        count, lo, hi = conn.execute(f"SELECT count(*), min({key}), max({key}) FROM {n}").fetchone()
        if key != "date" and lo is not None:
            lo, hi = (datetime.fromtimestamp(v).date().isoformat() for v in (lo, hi))
        print(f"{n:34} {count:>8}  {lo} .. {hi}")


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROOT
    if not root.is_dir():
        print(f"not found: {root}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    with conn:
        import_all(conn, root, Offsets(conn))
    report(conn)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
