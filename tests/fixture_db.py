"""Synthetic test data: a made-up watch history written as data/health.db and
the daily cache (google_health.db), shaped like the real ones but with no personal
data in it.

    fx = make_fixture(tmp_path, days=42, seed=1)
    fx.health_db, fx.mcp_db     # the two SQLite files
    fx.today                    # the frozen "today" the scores should run on

What's in it, per day: heart rate every 2-3 s (24 samples a minute, so every
full day counts as worn), per-minute steps, 5-minute HRV during sleep, a main
sleep with stages, Google's daily values (resting HR, night HRV, breathing,
skin temperature, SpO2, VO2 max, zone minutes), daily HR zone limits and a
Google Readiness score. Runs on Mon/Wed/Sat and strength on Thu, Sunday naps,
a Strava copy of each long run, a night split into two sessions, a short night
every so often, one night without Google HRV, a two-hour charging gap and one
mostly unworn day. Local time is UTC+1 and switches to UTC+2 on 29 March, like
Central Europe.

Annotations: an illness (5 nights, with raised HR, lower HRV and a warm skin
temperature), a confirmed alcohol night, a suggested one that must not count
and a note that changes nothing.

Each day draws from its own random generator, seeded by (seed, day), and only
uses arithmetic that is exact on every platform (no gauss, no exp/log), so the
same seed gives the same database on Windows and Linux, and a longer history
starts with the same days as a shorter one.
"""

import json
import random
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from health_dashboard.sync import takeout_import

WATCH = "Pixel Watch 3"
START = date(2026, 3, 2)          # a Monday
DST = date(2026, 3, 29)           # local UTC+1 before this day, UTC+2 from it
SAMPLE_SECONDS = [i * 5 // 2 for i in range(24)]   # 0, 2, 5, 7, ... 57

# Day numbers (from the first day) of the events.
ILLNESS_NIGHTS = (20, 24)         # wake dates, inclusive
ALCOHOL_EVENING = 33              # drinks that evening -> the next night counts
SUGGESTED_ALCOHOL = 12            # suggested only: must not change anything
SPLIT_NIGHT = 15                  # main sleep in two sessions
NO_GOOGLE_HRV_NIGHT = 18          # hrv.daily_rmssd missing -> deep-sleep fallback
CHARGING_DAY = 10                 # watch off 19:00-21:00
UNWORN_DAY = 30                   # watch off 08:00-18:00 (not a full day)

# Google's HR zone limits; before day 7 the older, inconsistent ones.
ZONES = {"LIGHT": (99, 114), "MODERATE": (115, 142), "VIGOROUS": (143, 176), "PEAK": (177, 220)}
OLD_ZONES = {"LIGHT": (95, 109), "MODERATE": (110, 134), "VIGOROUS": (135, 164), "PEAK": (165, 220)}

# The raw tables of health.db the scores read (a copy of the DDL in
# sync/health_sync.py, so the fixture doesn't depend on the sync code).
HEALTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS hr_samples (
    t INTEGER NOT NULL, utc_offset INTEGER, bpm INTEGER NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (t, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS steps_minute (
    t_start INTEGER NOT NULL, t_end INTEGER, utc_offset INTEGER, steps INTEGER NOT NULL,
    source TEXT NOT NULL, PRIMARY KEY (t_start, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS hrv_samples (
    t INTEGER NOT NULL, utc_offset INTEGER, rmssd_ms REAL, hf_power REAL, lf_power REAL,
    source TEXT NOT NULL, PRIMARY KEY (t, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS daily_hr_zones (
    date TEXT NOT NULL, zone TEXT NOT NULL, min_bpm INTEGER, max_bpm INTEGER,
    source TEXT NOT NULL, PRIMARY KEY (date, zone, source)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, start TEXT NOT NULL, "end" TEXT,
    target TEXT, attrs TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'suggested',
    source TEXT NOT NULL, confidence REAL, raw_text TEXT, note TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""

# The daily cache tables the scores read (columns as in google/cache.py).
MCP_SCHEMA = """
CREATE TABLE heart_rate (date TEXT PRIMARY KEY, resting_hr INTEGER, zones TEXT, provider TEXT,
    calculation_method TEXT, data_source TEXT);
CREATE TABLE exercises (log_id TEXT PRIMARY KEY, date TEXT NOT NULL, name TEXT, duration_min INTEGER,
    calories INTEGER, avg_hr INTEGER, steps INTEGER, distance_km REAL, distance_unit TEXT, start_time TEXT,
    source TEXT, log_type TEXT, provider TEXT, end_time TEXT, start_utc_offset TEXT, end_utc_offset TEXT,
    active_seconds REAL, notes TEXT, create_time TEXT, update_time TEXT, metrics_summary TEXT,
    exercise_metadata TEXT, exercise_events TEXT, splits TEXT, split_summaries TEXT, data_source TEXT);
CREATE TABLE sleep (date TEXT PRIMARY KEY, total_minutes INTEGER, efficiency INTEGER, start_time TEXT,
    end_time TEXT, deep_minutes INTEGER, light_minutes INTEGER, rem_minutes INTEGER, wake_minutes INTEGER,
    sessions INTEGER, sleep_period_minutes INTEGER, provider TEXT);
CREATE TABLE spo2 (date TEXT PRIMARY KEY, avg REAL, min REAL, max REAL, avg_ci_low REAL, avg_ci_high REAL,
    provider TEXT, std_dev REAL, data_source TEXT);
CREATE TABLE hrv (date TEXT PRIMARY KEY, daily_rmssd REAL, deep_rmssd REAL, provider TEXT, entropy REAL,
    non_rem_hr INTEGER, data_source TEXT);
CREATE TABLE azm (date TEXT PRIMARY KEY, total_minutes INTEGER, fat_burn_minutes INTEGER,
    cardio_minutes INTEGER, peak_minutes INTEGER, provider TEXT);
CREATE TABLE breathing_rate (date TEXT PRIMARY KEY, breaths_per_min REAL, provider TEXT, data_source TEXT);
CREATE TABLE skin_temperature (date TEXT PRIMARY KEY, nightly_relative REAL, log_type TEXT,
    nightly_absolute REAL, baseline REAL, provider TEXT, nightly_stddev_30d REAL, data_source TEXT);
CREATE TABLE cardio_fitness (date TEXT PRIMARY KEY, vo2_max_low REAL, vo2_max_high REAL, vo2_max REAL,
    provider TEXT, cardio_fitness_level TEXT, estimated INTEGER, vo2_max_covariance REAL, data_source TEXT);
CREATE TABLE account (resource TEXT PRIMARY KEY, body TEXT, fetched_at TEXT, provider TEXT);
CREATE TABLE sleep_sessions (session_id TEXT PRIMARY KEY, date TEXT NOT NULL, start_time TEXT,
    end_time TEXT, record TEXT, provider TEXT);
"""

# minute codes
REST, WALK, WORKOUT, LIGHT, DEEP, REM, AWAKE, OFF = range(8)
STAGE_CODE = {"LIGHT": LIGHT, "DEEP": DEEP, "REM": REM, "AWAKE": AWAKE}
STAGE_HR = {LIGHT: 0, DEEP: -3, REM: 5, AWAKE: 10}


@dataclass
class Fixture:
    dir: Path
    health_db: Path
    mcp_db: Path
    start: date                   # first day of watch data (settings.json watch_start)
    today: date                   # the frozen "today": data stops at 09:00 that morning
    seed: int
    watch: str = WATCH
    illness: tuple = None         # (first night, last night)
    alcohol_night: date = None
    nights: list = field(default_factory=list)


def utc_offset(d):
    return 3600 if d < DST else 7200


def midnight(d):
    """UTC epoch of local midnight starting day d."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) - utc_offset(d)


def iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------- plans -----

def plan_night(rng, d, i, hard_day_before):
    """Physiology and sleep stages of the night that ends on the morning of d."""
    ill = ILLNESS_NIGHTS[0] <= i <= ILLNESS_NIGHTS[1]
    alcohol = i == ALCOHOL_EVENING + 1
    nrem = 54 + rng.randint(-2, 2) + (1 if hard_day_before else 0)
    rmssd = 60 + rng.uniform(-7, 7) - (6 if hard_day_before else 0)
    if ill:
        nrem += 7
        rmssd *= 0.65
    if alcohol:
        nrem += 9
        rmssd *= 0.55

    m0 = midnight(d)
    bed = m0 - 60 * (60 - rng.randint(-30, 50))      # around 23:00
    wake = m0 + 60 * (7 * 60 + rng.randint(-25, 50))  # around 07:00
    if hard_day_before:
        wake += 60 * 20                                # sleeps a bit longer after a hard day
    if alcohol:
        bed = m0 + 60 * (70 + rng.randint(0, 20))      # home after 01:00
    elif rng.random() < 0.125:
        wake = bed + 60 * (330 + rng.randint(0, 30))   # short night

    stages, t, c = [], bed, 0
    stages.append((t, t + 60 * rng.randint(8, 15), "AWAKE"))
    t = stages[-1][1]
    while t < wake:
        for kind, minutes in (("LIGHT", rng.randint(25, 40)), ("DEEP", max(5, 35 - 8 * c + rng.randint(-5, 5))),
                              ("LIGHT", rng.randint(10, 15)), ("REM", min(35, 10 + 5 * c + rng.randint(0, 6)))):
            end = min(t + 60 * minutes, wake)
            if end > t:
                stages.append((t, end, kind))
            t = end
        if t < wake and rng.random() < 0.5:
            end = min(t + 60 * rng.randint(2, 6), wake)
            stages.append((t, end, "AWAKE"))
            t = end
        c += 1
    return {"night": d, "i": i, "ill": ill, "alcohol": alcohol, "nrem": nrem, "rmssd": rmssd,
            "bed": bed, "wake": wake, "stages": stages}


def plan_workouts(rng, d, i):
    """The day's watch workouts: [(kind, t_start, t_end)]."""
    if ILLNESS_NIGHTS[0] - 1 <= i <= ILLNESS_NIGHTS[1] or i == UNWORN_DAY:
        return []
    m0 = midnight(d)
    kind = {0: "easy", 2: "intervals", 3: "strength", 5: "long"}.get(d.weekday())
    if kind is None:
        return []
    at, minutes = {"easy": (18 * 60, 45), "intervals": (18 * 60, 40),
                   "strength": (17 * 60 + 30, 45), "long": (10 * 60, 75)}[kind]
    t0 = m0 + 60 * (at + rng.randint(-20, 20)) + rng.randint(0, 59)
    return [(kind, t0, t0 + 60 * (minutes + rng.randint(-5, 5)) + rng.randint(0, 59))]


def workout_hr(kind, k, rng):
    """Target HR in minute k of a workout."""
    if kind == "strength":
        return 105 + rng.randint(-8, 8) + (20 if k % 6 == 5 else 0)
    if k < 5:
        return 100 + 9 * k
    if kind == "intervals":
        return 172 + rng.randint(-3, 4) if (k - 5) % 7 < 4 else 138 + rng.randint(-4, 4)
    return (146 if kind == "easy" else 143) + rng.randint(-4, 5) + (k // 20)


# ------------------------------------------------------------- build -----

def make_fixture(directory, days=42, seed=1, start=START):
    """Write health.db and google_health.db into `directory` and return a
    Fixture. `days` full days from `start`, then a partial last day (today)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    today = start + timedelta(days=days)
    all_days = [start + timedelta(days=i) for i in range(days + 1)]
    fx = Fixture(directory, directory / "health.db", directory / "google_health.db", start, today, seed)
    for p in (fx.health_db, fx.mcp_db):
        if p.exists():
            p.unlink()

    plan = {d: random.Random(f"{seed}:plan:{d.isoformat()}") for d in all_days}
    workouts = {d: plan_workouts(plan[d], d, i) for i, d in enumerate(all_days)}
    nights = {}
    for i, d in enumerate(all_days[1:], start=1):
        prev = workouts[all_days[i - 1]]
        nights[d] = plan_night(plan[d], d, i, any(k in ("intervals", "long") for k, _, _ in prev))
    fx.nights = sorted(nights)
    if days > ILLNESS_NIGHTS[1]:
        fx.illness = (start + timedelta(ILLNESS_NIGHTS[0]), start + timedelta(ILLNESS_NIGHTS[1]))
    if days > ALCOHOL_EVENING + 1:
        fx.alcohol_night = start + timedelta(ALCOHOL_EVENING + 1)

    # per-minute state, filled in order of precedence (later wins)
    M0 = midnight(start) // 60
    M1 = midnight(today) // 60 + 9 * 60
    code = [REST] * (M1 - M0)
    tgt = [0] * (M1 - M0)
    wk_of = {}

    def fill(t0, t1, c, hr_of):
        for m in range(max(t0 // 60, M0), min(t1 // 60, M1)):
            code[m - M0] = c
            tgt[m - M0] = hr_of(m)

    for d in all_days:
        rng = plan[d]
        for _ in range(rng.randint(1, 2) if nights.get(d, {}).get("ill") else rng.randint(3, 5)):
            t0 = midnight(d) + 60 * rng.randint(8 * 60, 20 * 60)
            fill(t0, t0 + 60 * rng.randint(10, 30), WALK, lambda m, r=rng: 95 + r.randint(-3, 6))
    for d in all_days:
        for kind, t0, t1 in workouts[d]:
            fill(t0, t1, WORKOUT, lambda m, k=kind, a=t0 // 60, r=plan[d]: workout_hr(k, m - a, r))
            for m in range(t0 // 60, t1 // 60):
                wk_of[m] = (d, kind)
    sessions = []
    for d, n in nights.items():
        for b, e, s in n["stages"]:
            fill(b, e, STAGE_CODE[s], lambda m, c=STAGE_CODE[s], n=n: n["nrem"] + STAGE_HR[c])
        if n["i"] == SPLIT_NIGHT:
            cut = n["stages"][len(n["stages"]) // 2][0]
            gap = cut + 40 * 60
            fill(cut, gap, REST, lambda m: 72)
            first = [s for s in n["stages"] if s[1] <= cut]
            second = [(max(b, gap), e, s) for b, e, s in n["stages"] if e > gap]
            sessions += [(d, first), (d, second)]
        else:
            sessions.append((d, n["stages"]))
        if d.weekday() == 6:  # Sunday nap
            t0 = midnight(d) + 60 * (14 * 60 + 30)
            nap = [(t0, t0 + 22 * 60, "LIGHT"), (t0 + 22 * 60, t0 + 26 * 60, "AWAKE")]
            fill(t0, t0 + 26 * 60, LIGHT, lambda m, n=n: n["nrem"] + 4)
            sessions.append((d, nap))
    for i, d in enumerate(all_days):
        m0 = midnight(d)
        if i == CHARGING_DAY:
            fill(m0 + 19 * 3600, m0 + 21 * 3600, OFF, lambda m: 0)
        if i == UNWORN_DAY:
            fill(m0 + 8 * 3600, m0 + 18 * 3600, OFF, lambda m: 0)

    # ---------------------------------------------------- health.db ------
    h = sqlite3.connect(fx.health_db)
    h.executescript(HEALTH_SCHEMA + takeout_import.SCHEMA)
    hr_rows, step_rows, azm, wk_bpm = [], [], {}, {}
    for d in all_days:
        rng = random.Random(f"{seed}:samples:{d.isoformat()}")
        off = utc_offset(d)
        zones = ZONES if (d - start).days >= 7 else OLD_ZONES
        nxt = d + timedelta(days=1)
        ill = bool(fx.illness) and fx.illness[0] - timedelta(1) <= d <= fx.illness[1]
        rest = 68 + (8 if ill else 0)
        for m in range(midnight(d) // 60, min(midnight(nxt) // 60, M1)):
            c = code[m - M0]
            if c == OFF:
                continue
            target = tgt[m - M0] if c != REST else rest + rng.randint(-4, 6)
            for s in SAMPLE_SECONDS:
                hr_rows.append((m * 60 + s, off, target + rng.randint(-3, 3), WATCH))
            if c == WORKOUT:
                wk_bpm.setdefault(wk_of[m], []).append(target)
            steps = (rng.randint(100, 115) if c == WALK else
                     rng.randint(0, 10) if c == WORKOUT and wk_of[m][1] == "strength" else
                     rng.randint(160, 175) if c == WORKOUT else
                     rng.randint(1, 20) if c == REST and rng.random() < 0.25 else 0)
            if steps:
                step_rows.append((m * 60, m * 60 + 60, off, steps, WATCH))
            z = azm.setdefault(d, [0, 0, 0])
            if target >= zones["PEAK"][0]:
                z[2] += 1
            elif target >= zones["VIGOROUS"][0]:
                z[1] += 1
            elif target >= zones["MODERATE"][0]:
                z[0] += 1
        h.executemany("INSERT INTO daily_hr_zones VALUES (?, ?, ?, ?, ?)",
                      [(d.isoformat(), z, lo, hi, WATCH) for z, (lo, hi) in zones.items()])
    h.executemany("INSERT INTO hr_samples VALUES (?, ?, ?, ?)", hr_rows)
    h.executemany("INSERT INTO steps_minute VALUES (?, ?, ?, ?, ?)", step_rows)

    hrv_rows, night_hrv = [], {}
    for d, n in nights.items():
        rng = random.Random(f"{seed}:hrv:{d.isoformat()}")
        vals, deep = [], []
        t = n["bed"] + 300
        while t + 300 <= n["wake"]:
            c = code[t // 60 - M0]
            if c in STAGE_HR and rng.random() > 0.1:
                f = {DEEP: 1.15, REM: 0.9, LIGHT: 1.0, AWAKE: 0.8}[c] * rng.uniform(0.8, 1.2)
                r = round(n["rmssd"] * f, 1)
                hrv_rows.append((t, utc_offset(d), r, None, None, WATCH))
                vals.append(r)
                if c == DEEP:
                    deep.append(r)
            t += 300
        night_hrv[d] = (round(sum(vals) / len(vals), 1), round(sum(deep) / len(deep), 1) if deep else None)
    h.executemany("INSERT INTO hrv_samples VALUES (?, ?, ?, ?, ?, ?)", hrv_rows)

    readiness = []
    for d, n in nights.items():
        if d > today - timedelta(days=3):
            continue  # Takeout lags a few days behind
        rng = random.Random(f"{seed}:readiness:{d.isoformat()}")
        s = round(65 + (n["rmssd"] - 58) - 2.5 * (n["nrem"] - 55)) + rng.randint(-8, 8)
        readiness.append((d.isoformat(), max(1, min(100, s)), None, None, None, None, WATCH))
    h.executemany("INSERT INTO takeout_daily_readiness VALUES (?, ?, ?, ?, ?, ?, ?)", readiness)

    stamp = f"{start.isoformat()}T12:00:00"
    ann = []
    if fx.illness:
        ann.append(("illness", fx.illness[0].isoformat(), fx.illness[1].isoformat(), None, "confirmed", "user"))
    if fx.alcohol_night:
        ann.append(("alcohol", f"{start + timedelta(ALCOHOL_EVENING)}T21:30:00", None, None, "confirmed", "user"))
    if days > SUGGESTED_ALCOHOL + 1:
        ann.append(("alcohol", f"{start + timedelta(SUGGESTED_ALCOHOL)}", None, None, "suggested", "ai"))
        ann.append(("note", f"{start + timedelta(SUGGESTED_ALCOHOL)}", None, None, "confirmed", "user"))
    h.executemany("INSERT INTO annotations (kind, start, \"end\", target, status, source, created_at, updated_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [a + (stamp, stamp) for a in ann])
    h.commit()
    h.close()

    # ---------------------------------------------- google_health.db -----
    g = sqlite3.connect(fx.mcp_db)
    g.executescript(MCP_SCHEMA)
    g.execute("INSERT INTO account VALUES ('profile', ?, ?, 'google')", (json.dumps({"age": 25}), stamp))
    for idx, (d, stages) in enumerate(sessions):
        off = utc_offset(d)
        rec = {"dataSource": {"device": {"displayName": WATCH}},
               "sleep": {"interval": {"startTime": iso(stages[0][0]), "endTime": iso(stages[-1][1]),
                                      "startUtcOffset": f"{off}s", "endUtcOffset": f"{off}s"},
                         "stages": [{"startTime": iso(b), "endTime": iso(e), "type": s} for b, e, s in stages]}}
        g.execute("INSERT INTO sleep_sessions VALUES (?, ?, ?, ?, ?, 'google')",
                  (f"sleep-{idx:03d}", d.isoformat(), iso(stages[0][0]), iso(stages[-1][1]), json.dumps(rec)))
    for i, d in enumerate(all_days):
        rng = random.Random(f"{seed}:daily:{d.isoformat()}")
        n = nights.get(d)
        key = d.isoformat()
        rhr = (n["nrem"] if n else 54) + 5 + rng.randint(-1, 1)
        g.execute("INSERT INTO heart_rate (date, resting_hr, provider) VALUES (?, ?, 'google')", (key, rhr))
        g.execute("INSERT INTO cardio_fitness (date, vo2_max, provider) VALUES (?, ?, 'google')",
                  (key, round(41.0 + 0.04 * i + rng.uniform(-0.3, 0.3), 1)))
        z = azm.get(d, [0, 0, 0])
        g.execute("INSERT INTO azm VALUES (?, ?, ?, ?, ?, 'google')", (key, z[0] + 2 * (z[1] + z[2]), *z))
        if not n:
            continue
        ill_early = n["ill"] and n["i"] < ILLNESS_NIGHTS[0] + 3
        daily_rmssd, deep_rmssd = night_hrv[d]
        g.execute("INSERT INTO hrv (date, daily_rmssd, deep_rmssd, non_rem_hr, provider) VALUES (?, ?, ?, ?, 'google')",
                  (key, None if n["i"] == NO_GOOGLE_HRV_NIGHT else daily_rmssd, deep_rmssd, n["nrem"]))
        g.execute("INSERT INTO breathing_rate (date, breaths_per_min, provider) VALUES (?, ?, 'google')",
                  (key, round(14.0 + 0.2 * rng.randint(-2, 2) + (1.2 if n["ill"] else 0) + (0.6 if n["alcohol"] else 0), 1)))
        g.execute("INSERT INTO skin_temperature (date, nightly_relative, provider) VALUES (?, ?, 'google')",
                  (key, round(rng.uniform(-0.25, 0.25) + (0.8 if ill_early else 0) + (0.4 if n["alcohol"] else 0), 2)))
        g.execute("INSERT INTO spo2 (date, avg, provider) VALUES (?, ?, 'google')",
                  (key, 93.8 if n["i"] == ILLNESS_NIGHTS[0] + 1 else round(96.5 + rng.uniform(-0.6, 0.6), 1)))
        main = [(b, e, s) for dd, st in sessions if dd == d for b, e, s in st if st[0][0] < midnight(d) + 10 * 3600]
        asleep = sum(e - b for b, e, s in main if s != "AWAKE") // 60
        g.execute("INSERT INTO sleep (date, total_minutes, start_time, end_time, sessions, provider) "
                  "VALUES (?, ?, ?, ?, ?, 'google')", (key, asleep, iso(n["bed"]), iso(n["wake"]),
                                                       2 if n["i"] == SPLIT_NIGHT else 1))
    for d in all_days:
        off = utc_offset(d)
        for kind, t0, t1 in workouts[d]:
            bpm = wk_bpm.get((d, kind), [0])
            run = kind != "strength"
            minutes = (t1 - t0) / 60
            row = {"log_id": f"fx-{d.isoformat()}-{kind}", "date": d.isoformat(),
                   "name": "Run" if run else "Strength training", "duration_min": round(minutes),
                   "avg_hr": round(sum(bpm) / len(bpm)), "distance_km": round(minutes / 6, 2) if run else None,
                   "start_time": iso(t0), "end_time": iso(t1), "source": WATCH,
                   "log_type": "RUNNING" if run else "STRENGTH_TRAINING", "provider": "google",
                   "start_utc_offset": f"{off}s", "end_utc_offset": f"{off}s",
                   "metrics_summary": "{}", "exercise_events": "[]", "splits": "[]"}
            rows = [row]
            if kind == "long":  # Strava copy: same times, no device source
                rows.append(dict(row, log_id=f"strava-{d.isoformat()}", name="Morning Run", source=None,
                                 provider="strava"))
            for r in rows:
                g.execute(f"INSERT INTO exercises ({', '.join(r)}) VALUES ({', '.join('?' for _ in r)})",
                          list(r.values()))
    g.commit()
    g.close()
    return fx


if __name__ == "__main__":  # python tests/fixture_db.py OUT_DIR  (to look at the data)
    f = make_fixture(Path(sys.argv[1] if len(sys.argv) > 1 else "fixture_out"))
    print(f"wrote {f.health_db} and {f.mcp_db}, {f.start} .. {f.today}")
