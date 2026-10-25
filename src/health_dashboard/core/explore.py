"""Reads behind the web app's Activity, Training and Long-term tabs, the
intraday views, and the raw data browser (every table in both databases).

Intraday tables store UTC epoch seconds plus utc_offset; a sample's local date
is date(t + utc_offset). Range queries filter on t first (primary key) and
group by the local date afterwards.
"""

import bisect
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, time, timedelta

from . import annotations as ann
from .queries import _all, _annotations_by_date, _d, _one, _range, _round, short_id

LOCAL_DAY = "date({c} + coalesce(utc_offset, 0), 'unixepoch')"
EPOCH_COLS = ("t", "t_start", "t_end", "sleep_start", "sleep_end")
DATE_COLS = ("date", "night", "computed_on", "monday")
BIG_TEXT = 300  # browse view cuts strings longer than this (TCX, ECG waveforms, JSON blobs)


def _epoch(d):
    return int(datetime.combine(_d(d), time()).timestamp())


def _span(fr, to):
    """Epoch bounds that cover the local dates fr..to with a day of slack
    either side (offsets differ); callers group by local date and filter."""
    return _epoch(fr) - 86400, _epoch(_d(to) + timedelta(days=1)) + 86400


def _utc(t):
    """Naive datetime of an epoch in UTC; with the offset added first it reads as local clock time."""
    return datetime.fromtimestamp(t, UTC).replace(tzinfo=None)


def _local(t, off):
    if t is None:
        return None
    return _utc(t + (off or 0)).isoformat(timespec="seconds")


# ------------------------------------------------------------ activity ----

def activity(db, date_from=None, date_to=None):
    fr, to = _range(date_from, date_to)
    lo, hi = _span(fr, to)
    days = {}

    def put(d, **kw):
        if fr <= d <= to:
            days.setdefault(d, {"date": d}).update(kw)

    for r in _all(db.m, "SELECT date, steps, calories_out, floors, distance_km, sedentary_minutes "
                        "FROM activity WHERE date BETWEEN ? AND ?", (fr, to)):
        put(r["date"], google_steps=r["steps"], calories=r["calories_out"], floors=r["floors"],
            distance_km=r["distance_km"])
    for r in _all(db.m, "SELECT date, total_minutes, fat_burn_minutes, cardio_minutes, peak_minutes "
                        "FROM azm WHERE date BETWEEN ? AND ?", (fr, to)):
        put(r["date"], azm=r["total_minutes"], azm_fat_burn=r["fat_burn_minutes"],
            azm_cardio=r["cardio_minutes"], azm_peak=r["peak_minutes"])
    day = LOCAL_DAY.format(c="t_start")
    for r in _all(db.h, f"SELECT {day} d, sum(steps) n FROM steps_minute WHERE source = ? "
                        "AND t_start BETWEEN ? AND ? GROUP BY d", (db.watch, lo, hi)):
        put(r["d"], watch_steps=r["n"])
    for r in _all(db.h, f"SELECT {day} d, level, sum(minutes) n FROM active_minutes_minute WHERE source = ? "
                        "AND t_start BETWEEN ? AND ? GROUP BY d, level", (db.watch, lo, hi)):
        put(r["d"], **{f"active_{r['level'].lower()}": r["n"]})
    for r in _all(db.h, f"SELECT {day} d, sum(t_end - t_start) / 60.0 n FROM sedentary_periods "
                        "WHERE t_start BETWEEN ? AND ? GROUP BY d", (lo, hi)):
        put(r["d"], sedentary_min=round(r["n"]))
    out = []
    for d in sorted(days):
        r = days[d]
        r["steps"] = r.get("watch_steps") if r.get("watch_steps") is not None else r.get("google_steps")
        r["active_min"] = sum(r.get(k) or 0 for k in ("active_light", "active_moderate", "active_vigorous")) \
            if any(k in r for k in ("active_light", "active_moderate", "active_vigorous")) else None
        out.append(_round(r))
    anns, by = _annotations_by_date(db, fr, to)
    for r in out:
        r["annotation_ids"] = by.get(r["date"], [])
    return {"from": fr, "to": to, "days": out, "annotations": anns}


# ----------------------------------------------------------- one day ----

def day_detail(db, day):
    """Intraday view of one local date: HR in 5-minute means, steps per hour,
    sleep window, workouts, sedentary periods."""
    ds = _d(day).isoformat()
    lo, hi = _span(ds, ds)
    dexpr = LOCAL_DAY.format(c="t")
    # bucket on local time, so the bucket start is already a local clock time
    hr = [{"t": _utc(r["b"] * 300).isoformat(timespec="minutes"), "bpm": round(r["v"], 1),
           "min": r["mn"], "max": r["mx"]}
          for r in _all(db.h, f"SELECT (t + coalesce(utc_offset,0)) / 300 b, avg(bpm) v, min(bpm) mn, "
                              f"max(bpm) mx FROM hr_samples WHERE t BETWEEN ? AND ? AND {dexpr} = ? "
                              f"GROUP BY b ORDER BY b", (lo, hi, ds))]
    sexpr = LOCAL_DAY.format(c="t_start")
    hours = {h: 0 for h in range(24)}
    for r in _all(db.h, f"SELECT cast(strftime('%H', t_start + coalesce(utc_offset,0), 'unixepoch') AS int) h, "
                        f"sum(steps) n FROM steps_minute WHERE source = ? AND t_start BETWEEN ? AND ? "
                        f"AND {sexpr} = ? GROUP BY h", (db.watch, lo, hi, ds)):
        hours[r["h"]] = r["n"]
    sedentary = [{"start": _local(r["t_start"], r["utc_offset"]), "end": _local(r["t_end"], r["utc_offset"])}
                 for r in _all(db.h, f"SELECT t_start, t_end, utc_offset FROM sedentary_periods "
                                     f"WHERE t_start BETWEEN ? AND ? AND {sexpr} = ? ORDER BY t_start", (lo, hi, ds))]
    sleep = _one(db.m, "SELECT date, start_time, end_time, total_minutes FROM sleep WHERE date = ?", (ds,))
    workouts = [{**_round(w), "id": short_id(w["log_id"])}
                for w in _all(db.h, "SELECT log_id, type, name, minutes, km, avg_hr FROM analysis_workouts "
                                    "WHERE date = ? ORDER BY end_local_h", (ds,))]
    return {"date": ds, "hr_5min": hr, "steps_by_hour": [{"hour": h, "steps": n} for h, n in hours.items()],
            "sedentary_periods": sedentary, "sleep": sleep, "workouts": workouts,
            "annotations": [a for a in ann.list_(db.a, ds, ds) if a["status"] != "rejected"]}


# ------------------------------------------------------------ training ----

PR_LABELS = {"FARTHEST_RUN_NAME": "Longest run", "FASTEST_KILOMETRE_NAME": "Fastest 1 km",
             "FASTEST_MILE_NAME": "Fastest mile", "FASTEST_2_KILOMETRES_NAME": "Fastest 2 km",
             "FASTEST_5_KILOMETRES_NAME": "Fastest 5 km", "FASTEST_10_KILOMETRES_NAME": "Fastest 10 km"}


def training(db, date_from=None, date_to=None):
    fr, to = _range(date_from, date_to, default_days=90)
    strain = [_round(r) for r in _all(db.h, "SELECT date, strain, band, load, cardio_load, muscular_load, partial "
                                            "FROM score_strain_daily WHERE date BETWEEN ? AND ? ORDER BY date",
                                      (fr, to))]
    weeks = [_round(r) for r in _all(db.h, "SELECT * FROM analysis_weekly WHERE monday BETWEEN ? AND ? "
                                           "ORDER BY monday", ((_d(fr) - timedelta(days=6)).isoformat(), to))]
    acwr = [_round(r, 3) for r in _all(db.h, "SELECT date, acwr, acwr_label FROM takeout_daily_cardio_load "
                                             "WHERE date BETWEEN ? AND ? ORDER BY date", (fr, to))]
    # best current value per record (Takeout, ends at the export date)
    prs = {}
    for r in _all(db.h, "SELECT exercise_id, record, t, value, record_type, extent FROM takeout_personal_records "
                        "ORDER BY t"):
        longest = r["record_type"].endswith("LONGEST_DISTANCE")
        cur = prs.get(r["record"])
        if cur is None or (r["value"] > cur["value"] if longest else r["value"] < cur["value"]):
            prs[r["record"]] = r
    records = []
    for k, r in prs.items():
        longest = r["record_type"].endswith("LONGEST_DISTANCE")
        records.append({"record": PR_LABELS.get(k, k.replace("_NAME", "").replace("_", " ").title()),
                        "km": round(r["value"] / 1e6, 2) if longest else round(r["extent"] / 1e6, 2) if r["extent"]
                        else None,
                        "seconds": None if longest else round(r["value"] / 1000),
                        "date": datetime.fromtimestamp(r["t"]).date().isoformat(), "exercise_id": r["exercise_id"]})
    records.sort(key=lambda x: (x["seconds"] is not None, x["km"] or 0))
    anns, by = _annotations_by_date(db, fr, to)
    for r in strain:
        r["annotation_ids"] = by.get(r["date"], [])
    return {"from": fr, "to": to, "strain": strain, "weeks": weeks, "acwr": acwr, "records": records,
            "annotations": anns}


def _track(db, log_id):
    """TCX trackpoints of a workout as (epoch, lat, lon, metres so far)."""
    r = _one(db.m, "SELECT tcx FROM exercise_routes WHERE log_id = ?", (log_id,))
    if not r or not r["tcx"]:
        return []
    try:
        root = ET.fromstring(r["tcx"].encode("utf-8"))
    except ET.ParseError:
        return []
    pts = []
    for tp in root.iter():
        if not tp.tag.endswith("Trackpoint"):
            continue
        t = lat = lon = dist = None
        for e in tp.iter():
            tag = e.tag.rsplit("}", 1)[-1]
            if tag == "Time":
                t = datetime.fromisoformat(e.text.replace("Z", "+00:00")).timestamp()
            elif tag == "LatitudeDegrees":
                lat = float(e.text)
            elif tag == "LongitudeDegrees":
                lon = float(e.text)
            elif tag == "DistanceMeters":
                dist = float(e.text)
        if t is not None:
            pts.append((t, lat, lon, dist))
    if pts and any(p[3] is None for p in pts):  # no recorded distance: add up the GPS legs
        total, prev, out = 0.0, None, []
        for t, lat, lon, _ in pts:
            if lat is None:
                continue
            if prev:
                total += _haversine(prev, (lat, lon))
            prev = (lat, lon)
            out.append((t, lat, lon, total))
        pts = out
    return pts


def _haversine(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


PACE_STEP = 10     # seconds between pace points
PACE_WINDOW = 30   # seconds of distance behind each pace point, centred
MIN_SPEED = 0.5    # m/s; anything slower is standing still and gets no pace
WALK_SPM = 135     # cadence below this is a walking break (a jog is typically ~150-170, a walk ~100-125)


def _pace(dist):
    """Pace in s/km every PACE_STEP s from cumulative distance [(s, metres)]:
    the distance covered in a centred PACE_WINDOW over the time it took, which
    is how Strava turns GPS distance into a pace stream."""
    if len(dist) < 2:
        return []
    xs, ds = [p[0] for p in dist], [p[1] for p in dist]

    def at(s):  # distance at second s, linear between samples
        i = bisect.bisect_left(xs, s)
        if i <= 0:
            return ds[0]
        if i >= len(xs):
            return ds[-1]
        return ds[i - 1] + (ds[i] - ds[i - 1]) * (s - xs[i - 1]) / ((xs[i] - xs[i - 1]) or 1)

    out = []
    for s in range(int(xs[0]) // PACE_STEP * PACE_STEP, int(xs[-1]) + 1, PACE_STEP):
        a, b = max(xs[0], s - PACE_WINDOW / 2), min(xs[-1], s + PACE_WINDOW / 2)
        if b - a < PACE_WINDOW / 2:
            continue
        v = (at(b) - at(a)) / (b - a)
        out.append({"s": s, "v": round(1000 / v) if v >= MIN_SPEED else None})
    return out


def _walks(cadence, res):
    """Walking breaks as [{from, to}] seconds: runs of cadence points under WALK_SPM."""
    out = []
    for p in cadence:
        if p["v"] >= WALK_SPM:
            continue
        a, b = p["s"] - res / 2, p["s"] + res / 2
        if out and a <= out[-1]["to"] + 1:
            out[-1]["to"] = b
        else:
            out.append({"from": a, "to": b})
    return out


def run_series(db, run_id):
    """Time series for one workout, in seconds since its start: HR (10 s
    means), pace, cadence, walking breaks, Takeout running dynamics and the
    GPS route shape, plus Google's HR zone limits for that day.

    Pace comes from the watch's own per-second distance (Takeout, matches
    Google's total) and otherwise from the TCX track. Cadence comes from the
    watch's per-second cadence (Takeout) and otherwise from its steps per
    minute, which is coarser but covers every run."""
    ex = _one(db.m, "SELECT log_id, start_time, end_time, distance_km FROM exercises WHERE log_id = ? "
                    "OR log_id LIKE ?", (run_id, f"%/{run_id}"))
    if not ex or not ex["start_time"]:
        return None
    t0 = int(datetime.fromisoformat(ex["start_time"].replace("Z", "+00:00")).timestamp())
    t1 = int(datetime.fromisoformat(ex["end_time"].replace("Z", "+00:00")).timestamp()) if ex["end_time"]         else t0 + 4 * 3600
    hr = [{"s": r["b"] * 10 - t0, "bpm": round(r["v"])}
          for r in _all(db.h, "SELECT t / 10 b, avg(bpm) v FROM hr_samples WHERE t BETWEEN ? AND ? GROUP BY b "
                              "ORDER BY b", (t0, t1))]
    dyn = {}
    for r in _all(db.h, "SELECT t, metric, value FROM takeout_run_samples WHERE t BETWEEN ? AND ? "
                        "AND metric IN ('cadence_spm', 'pace_distance_mm', 'stride_cm', 'ground_contact_ms', "
                        "'vertical_oscillation_cm') ORDER BY t", (t0, t1)):
        dyn.setdefault(r["metric"], []).append({"s": r["t"] - t0, "v": round(r["value"], 2)})
    track = _track(db, ex["log_id"])

    dist, pace_src = [], None
    if len(dyn.get("pace_distance_mm", [])) >= 60:
        steps = dyn.pop("pace_distance_mm")
        dist, total = [(steps[0]["s"] - 1, 0.0)], 0.0
        for p in steps:
            total += p["v"] / 1000
            dist.append((p["s"], total))
        pace_src = "watch"
    elif track and track[-1][3]:
        # the raw track runs a few % short of Google's distance; scale it so
        # pace agrees with the totals and splits (shape still from the GPS)
        k = ex["distance_km"] * 1000 / track[-1][3] if ex["distance_km"] else 1
        k = k if 0.85 < k < 1.15 else 1
        dist, pace_src = [(t - t0, d * k) for t, _, _, d in track if d is not None], "gps"
    dyn.pop("pace_distance_mm", None)

    if dyn.get("cadence_spm"):
        acc = {}
        for p in dyn["cadence_spm"]:
            acc.setdefault(p["s"] // 30 * 30 + 15, []).append(p["v"])
        cadence, cad_res, cad_src = [{"s": s, "v": round(sum(v) / len(v))} for s, v in sorted(acc.items())], 30,             "watch"
    else:
        cadence, cad_res, cad_src = [], 60, None
        for r in _all(db.h, "SELECT t_start, t_end, steps FROM steps_minute WHERE source = ? AND t_end > ? "
                            "AND t_start < ? ORDER BY t_start", (db.watch, t0, t1)):
            inside = min(r["t_end"], t1) - max(r["t_start"], t0)
            if inside >= 30 and r["t_end"] > r["t_start"]:
                cadence.append({"s": (r["t_start"] + r["t_end"]) // 2 - t0,
                                "v": round(r["steps"] * 60 / (r["t_end"] - r["t_start"]))})
        cad_src = "steps per minute" if cadence else None

    day = ex["start_time"][:10]
    zones = {r["zone"]: [r["min_bpm"], r["max_bpm"]] for r in _all(
        db.h, "SELECT zone, min_bpm, max_bpm FROM daily_hr_zones WHERE date = coalesce("
              "(SELECT max(date) FROM daily_hr_zones WHERE date <= ?), (SELECT min(date) FROM daily_hr_zones))", (day,))}

    pts = [[round(lat, 6), round(lon, 6)] for _, lat, lon, _ in track if lat is not None]
    return {"id": short_id(ex["log_id"]), "start": ex["start_time"], "duration": t1 - t0, "hr": hr,
            "pace": _pace(dist), "pace_source": pace_src,
            "cadence": cadence, "cadence_source": cad_src, "walks": _walks(cadence, cad_res),
            "zones": zones, "dynamics": dyn, "route": pts[::max(1, len(pts) // 400)] or None}


# ----------------------------------------------------------- long-term ----

MONTHLY = {  # metric -> SQL returning (date, value) rows, averaged per month
    "recovery": ("h", "SELECT date, recovery FROM score_recovery_daily"),
    "hrv_ms": ("m", "SELECT date, daily_rmssd FROM hrv"),  # Google's night average, as in the app
    "rhr": ("m", "SELECT date, resting_hr FROM heart_rate"),
    "sleep_h": ("h", "SELECT date, asleep_min / 60.0 FROM score_sleep_daily"),
    "sleep_performance": ("h", "SELECT date, sleep_performance FROM score_sleep_daily"),
    "strain": ("h", "SELECT date, strain FROM score_strain_daily"),
    "steps": ("m", "SELECT date, steps FROM activity"),
    "vo2max": ("m", "SELECT date, vo2_max FROM cardio_fitness"),
    "weight": ("m", "SELECT date, weight_kg FROM weight"),
}


def longterm(db):
    latest = _one(db.h, "SELECT max(computed_on) d FROM score_healthspan")
    hs = {}
    if latest and latest["d"]:
        for r in _all(db.h, "SELECT * FROM score_healthspan WHERE computed_on = ? ORDER BY variant, metric",
                      (latest["d"],)):
            hs.setdefault(r["variant"], []).append(_round(r, 3))
    hs_history = [_round(r, 2) for r in _all(db.h, "SELECT computed_on, variant, value FROM score_healthspan "
                                                   "WHERE metric = 'healthspan_age' ORDER BY computed_on")]
    monthly = {}
    for metric, (src, sql) in MONTHLY.items():
        rows = _all(getattr(db, src), sql)
        acc = {}
        for r in rows:
            d, v = list(r.values())[:2]
            if d and v is not None:
                acc.setdefault(d[:7], []).append(v)
        monthly[metric] = [{"month": m, "mean": round(sum(v) / len(v), 2), "n": len(v)} for m, v in sorted(acc.items())]
    vo2 = [_round(r, 2) for r in _all(db.m, "SELECT date, vo2_max, cardio_fitness_level FROM cardio_fitness "
                                            "ORDER BY date")]
    run_vo2 = [{"date": _utc(r["t"] + (r["utc_offset"] or 0)).date().isoformat(),
                "vo2max": round(r["vo2max"], 2)}
               for r in _all(db.h, "SELECT t, utc_offset, vo2max FROM vo2max_samples WHERE kind = 'run-vo2-max' "
                                   "ORDER BY t")]
    weight = _all(db.m, "SELECT date, weight_kg, bmi, fat_pct FROM weight ORDER BY date")
    height = _one(db.m, "SELECT height_mm FROM height ORDER BY date DESC LIMIT 1")
    ecg = _all(db.m, "SELECT reading_id, date, start_time, avg_bpm, classification, duration_sec "
                     "FROM ecg ORDER BY start_time DESC")
    assessments = _all(db.h, "SELECT * FROM takeout_assessments ORDER BY date")
    return {"healthspan": {"computed_on": latest["d"] if latest else None, "variants": hs, "history": hs_history},
            "monthly": monthly, "vo2max": vo2, "run_vo2max": run_vo2, "weight": weight,
            "height_cm": round(height["height_mm"] / 10, 1) if height and height["height_mm"] else None,
            "ecg": ecg, "assessments": assessments,
            "annotations": ann.list_(db.a)}


def ecg_waveform(db, reading_id):
    r = _one(db.m, "SELECT reading_id, date, start_time, avg_bpm, classification, duration_sec, sampling_hz, "
                   "scaling_factor, waveform FROM ecg WHERE reading_id = ? OR reading_id LIKE ?",
             (reading_id, f"%/{reading_id}"))
    if not r:
        return None
    try:
        r["waveform"] = json.loads(r["waveform"] or "[]")
    except ValueError:
        r["waveform"] = []
    return r


# ------------------------------------------------------- data browser ----

GROUPS = [  # (name prefix, group label); first match wins
    ("score_", "Scores (computed)"), ("analysis_", "Analysis (computed)"), ("takeout_", "Google Takeout"),
    ("annotations", "Events"), ("sync_", "Sync bookkeeping"),
]
INTRADAY = ("_minute", "_samples", "_periods", "_lengths", "_rate")
HIDDEN = {"sqlite_sequence", "authorisation", "account"}


def _group(source, table):
    if source == "google":
        return "Sync bookkeeping" if table == "sync_log" else "Google Health daily (MCP cache)"
    for prefix, label in GROUPS:
        if table.startswith(prefix):
            return label
    return "Watch samples (intraday)" if table.endswith(INTRADAY) else "Watch daily (health_sync)"


def _tables(conn):
    return [r["name"] for r in _all(conn, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
            if r["name"] not in HIDDEN]


def _cols(conn, table):
    return [r["name"] for r in _all(conn, f"PRAGMA table_info('{table}')")]


def _date_col(cols):
    for c in DATE_COLS + EPOCH_COLS:
        if c in cols:
            return c
    if "datetime" in cols:
        return "datetime"
    return None


def catalog(db):
    out = []
    for src, conn in (("health", db.h), ("google", db.m)):
        for t in _tables(conn):
            cols = _cols(conn, t)
            dc = _date_col(cols)
            n = _one(conn, f"SELECT count(*) n FROM '{t}'")["n"]
            lo = hi = None
            if dc and n:
                r = _one(conn, f"SELECT min({dc}) lo, max({dc}) hi FROM '{t}'")
                lo, hi = r["lo"], r["hi"]
                if dc in EPOCH_COLS and lo is not None:
                    lo, hi = (datetime.fromtimestamp(x).date().isoformat() for x in (lo, hi))
                else:
                    lo, hi = str(lo)[:10], str(hi)[:10]
            out.append({"source": src, "table": t, "group": _group(src, t), "rows": n, "columns": cols, "date_column": dc,
                        "first": lo, "last": hi})
    return {"tables": out}


def browse(db, source, table, date_from=None, date_to=None, limit=200, offset=0, desc=True):
    conn = {"health": db.h, "google": db.m}.get(source)
    if conn is None:
        raise ValueError("source must be health or google")
    if table not in _tables(conn) or not re.fullmatch(r"\w+", table):
        raise ValueError("no such table")
    cols = _cols(conn, table)
    dc = _date_col(cols)
    where, args = [], []
    if dc and (date_from or date_to):
        fr, to = (date_from or "0001-01-01"), (date_to or "9999-12-31")
        fr, to = str(fr), str(to)
        if dc in EPOCH_COLS:
            where.append(f"{dc} >= ? AND {dc} < ?")
            args += [_epoch(fr) if fr > "0001" else 0, _epoch(_d(to) + timedelta(days=1)) if to < "9999" else 2**40]
        else:
            where.append(f"substr({dc}, 1, 10) BETWEEN ? AND ?")
            args += [fr, to]
    sql = f"SELECT * FROM '{table}'" + (" WHERE " + " AND ".join(where) if where else "")
    total = _one(conn, sql.replace("SELECT *", "SELECT count(*) n", 1), args)["n"]
    if dc:
        sql += f" ORDER BY {dc} {'DESC' if desc else 'ASC'}"
    limit = max(1, min(int(limit), 2000))
    rows = _all(conn, sql + " LIMIT ? OFFSET ?", args + [limit, max(0, int(offset))])
    out_cols = list(cols)
    has_off = "utc_offset" in cols
    for r in rows:
        for c in EPOCH_COLS:
            if c in r and isinstance(r[c], int):
                r[c + "_local"] = _local(r[c], r.get("utc_offset") if has_off else None) if has_off \
                    else datetime.fromtimestamp(r[c]).isoformat(timespec="seconds")
        for k, v in r.items():
            if isinstance(v, str) and len(v) > BIG_TEXT:
                r[k] = v[:BIG_TEXT] + f"... ({len(v):,} chars)"
            elif isinstance(v, bytes):
                r[k] = f"<{len(v):,} bytes>"
    for c in EPOCH_COLS:
        if c in cols:
            out_cols.insert(out_cols.index(c) + 1, c + "_local")
    return {"source": source, "table": table, "columns": out_cols, "date_column": dc, "total": total,
            "limit": limit, "offset": offset, "rows": [_round(r, 3) for r in rows]}
