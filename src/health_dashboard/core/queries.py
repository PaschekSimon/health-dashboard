"""Read queries for the app and the MCP server. Everything returned is plain
JSON-able dicts; each answer carries the annotations that touch it.

Sources: health.db (score_* and analysis_* tables written by the scripts,
intraday samples, annotations, sync_runs) and the MCP's cache
google_health.db (daily values, sleep, workouts), both opened read-only.
Dates are local dates; a night is keyed by its wake date.
"""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from ..config import get_paths
from ..settings import load_settings
from . import annotations as ann
from . import jobs, zones

# Strain target by Recovery band. Direction from HRV-guided training studies
# (Kiviniemi 2007, Vesterinen 2016, Javaloyes 2019: hard when recovered, low
# intensity - not rest - when not); bands from WHOOP's published Strain
# categories (light 0-9, moderate 10-13, high 14-17, all out 18-21). Red goes to
# 12 so an easy Z1/Z2 run (day Strain ~9-13) stays allowed.
STRAIN_TARGET = {"green": (14.0, 18.0, "hard training possible"),
                 "yellow": (10.0, 14.0, "moderate"),
                 "red": (0.0, 12.0, "easy only (Z1/Z2)")}


def _ro(path):
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


class Db:
    """Read-only connections to both databases, plus a writable one for
    annotations (which uses its own), and the settings queries need (paths,
    watch name, first watch day). Paths default to the environment
    (config.get_paths)."""

    def __init__(self, paths=None):
        self.paths = paths or get_paths()
        settings = load_settings(self.paths)
        self.watch = settings.watch
        self.watch_start = settings.watch_start.isoformat()
        self.h = _ro(self.paths.health_db)
        self.m = _ro(self.paths.mcp_db)
        self.a = ann.connect(self.paths.health_db)

    def close(self):
        for c in (self.h, self.m, self.a):
            c.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _d(s):
    return date.fromisoformat(s) if isinstance(s, str) else s


def _one(conn, sql, args=()):
    try:
        r = conn.execute(sql, args).fetchone()
    except sqlite3.OperationalError:  # table not built yet
        return None
    return dict(r) if r else None


def _all(conn, sql, args=()):
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    except sqlite3.OperationalError:
        return []


def _round(d, nd=2):
    return {k: round(v, nd) if isinstance(v, float) else v for k, v in d.items()} if d else d


def _num(v):
    """A number Google sends as a string ("145") as a number; anything else unchanged."""
    if isinstance(v, str):
        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:
                return v
    return v


def short_id(log_id):
    return log_id.rsplit("/", 1)[-1]


def _range(date_from, date_to, default_days=30):
    to = _d(date_to) if date_to else date.today()
    fr = _d(date_from) if date_from else to - timedelta(days=default_days - 1)
    if fr > to:
        raise ValueError("from is after to")
    return fr.isoformat(), to.isoformat()


def _from_watch(db, fr):
    """`fr`, moved up to the first watch day: the phone's step days before the
    watch are left out of every range."""
    return max(fr, db.watch_start)


def _annotations_by_date(db, fr, to):
    """(all annotations touching fr..to, {date: [ids]})."""
    anns = ann.list_(db.a, fr, to)
    by = {}
    for a in anns:
        if a["status"] == "rejected":
            continue
        lo, hi = a["start"][:10], (a["end"] or a["start"])[:10]
        if a["target"] and a["target"].split(":")[0] in ("night", "day"):
            t = a["target"].split(":", 1)[1][:10]
            lo, hi = min(lo, t), max(hi, t)
        d, last = max(_d(lo), _d(fr)), min(_d(hi), _d(to))
        while d <= last:
            by.setdefault(d.isoformat(), []).append(a["id"])
            d += timedelta(days=1)
    return anns, by


# ------------------------------------------------------------- status ----

def status(db):
    """Last sync, newest data per type, whether a sync is running."""
    newest_health = {}
    for table, col, kind in [
        ("hr_samples", "t", "epoch"), ("steps_minute", "t_start", "epoch"),
        ("distance_minute", "t_start", "epoch"), ("hrv_samples", "t", "epoch"),
        ("spo2_samples", "t", "epoch"), ("active_energy_minute", "t_start", "epoch"),
        ("azm_minute", "t_start", "epoch"), ("hr_zone_minute", "t_start", "epoch"),
        ("sleep_respiratory_rate", "t", "epoch"), ("daily_hr_zones", "date", "date"),
    ]:
        r = _one(db.h, f"SELECT max({col}) v FROM {table}")
        v = r["v"] if r else None
        if v is not None and kind == "epoch":
            v = datetime.fromtimestamp(v, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
        newest_health[table] = v
    newest_mcp = {}
    for table in ("sleep", "sleep_sessions", "heart_rate", "hrv", "activity", "breathing_rate",
                  "skin_temperature", "spo2", "exercises", "cardio_fitness", "weight"):
        r = _one(db.m, f"SELECT max(date) v FROM {table}")
        newest_mcp[table] = r["v"] if r else None
    sync_state = {r["data_type"]: r["synced_through"] for r in _all(db.h, "SELECT * FROM sync_state")}
    jc = jobs.connect(db.paths)
    try:
        last = jobs.last_successful_sync(jc)
        runs = jobs.recent_runs(jc, 5, db.paths)
    finally:
        jc.close()
    intraday = jobs.last_intraday_sync(db.paths)
    return {
        "now": datetime.now().isoformat(timespec="seconds"),
        "sync_running": jobs.lock_held(db.paths),
        "last_successful_sync": last.isoformat(timespec="seconds") if last else None,
        "last_intraday_sync": intraday.isoformat(timespec="seconds") if intraday else None,
        "recent_runs": [{k: r[k] for k in ("id", "reason", "status", "started", "finished", "error", "origin")}
                        for r in runs],
        "newest_data": {"health_db": newest_health, "mcp_cache": newest_mcp},
        "health_sync_watermarks": sync_state,
    }


# ------------------------------------------------------------ recovery ----

RECOVERY_COLS = ("date, recovery, band, composite, hrv_ms, google_rmssd, deep_hrv_ms, rhr, breathing, skin_rel, spo2, z_hrv, z_rhr, "
                 "z_sleep, z_resp, penalty, flags, baseline_n, provisional, excluded_event, sleep_performance")


def recovery(db, date_from=None, date_to=None):
    fr, to = _range(date_from, date_to)
    rows = _all(db.h, f"SELECT {RECOVERY_COLS} FROM score_recovery_daily WHERE date BETWEEN ? AND ? "
                      "ORDER BY date", (fr, to))
    checks = {r["night"]: r for r in _all(db.h, "SELECT night, flag, hrv_status FROM analysis_daily "
                                                "WHERE night BETWEEN ? AND ?", (fr, to))}
    anns, by = _annotations_by_date(db, fr, to)
    for r in rows:
        c = checks.get(r["date"], {})
        r["daily_check_flag"], r["hrv_status"] = c.get("flag"), c.get("hrv_status")
        r["annotation_ids"] = by.get(r["date"], [])
    return {"from": fr, "to": to, "days": [_round(r) for r in rows], "annotations": anns}


# --------------------------------------------------------------- sleep ----

def _sleep_row(db, night):
    s = _one(db.m, "SELECT date, total_minutes, efficiency, start_time, end_time, deep_minutes, "
                   "light_minutes, rem_minutes, wake_minutes, sessions FROM sleep WHERE date = ?", (night,))
    p = _one(db.h, "SELECT * FROM score_sleep_daily WHERE date = ?", (night,))
    return s, _round(p, 1)


def hypnogram(db, night):
    """The night's main sleep as stage segments (UTC ISO times), plus the
    brief wake-ups Google lists apart from the stages. Naps are left out."""
    best = None
    for (rec,) in db.m.execute("SELECT record FROM sleep_sessions WHERE date = ?", (night,)):
        s = json.loads(rec).get("sleep") or {}
        if s.get("metadata", {}).get("nap") or not s.get("stages"):
            continue
        mins = int(s.get("summary", {}).get("minutesInSleepPeriod") or 0)
        if best is None or mins > best[0]:
            best = (mins, s)
    if not best:
        return None
    s = best[1]
    return {"start": s["interval"]["startTime"], "end": s["interval"]["endTime"],
            "stages": [{"start": x["startTime"], "end": x["endTime"], "stage": x["type"].lower()} for x in s["stages"]],
            "brief": [{"start": x["startTime"], "end": x["endTime"]} for x in s.get("shortAwakenings", [])]}


def sleep(db, date_from=None, date_to=None):
    fr, to = _range(date_from, date_to)
    google = {r["date"]: r for r in _all(db.m, "SELECT date, total_minutes, efficiency, start_time, end_time, "
                                               "deep_minutes, light_minutes, rem_minutes, wake_minutes "
                                               "FROM sleep WHERE date BETWEEN ? AND ?", (fr, to))}
    perf = {r["date"]: r for r in _all(db.h, "SELECT * FROM score_sleep_daily WHERE date BETWEEN ? AND ?",
                                       (fr, to))}
    anns, by = _annotations_by_date(db, fr, to)
    nights = []
    for n in sorted(set(google) | set(perf)):
        nights.append({"night": n, "google": google.get(n), "performance": _round(perf.get(n), 1),
                       "annotation_ids": by.get(n, [])})
    return {"from": fr, "to": to, "nights": nights, "annotations": anns}


# ---------------------------------------------------------------- runs ----

RUN_COLS = ("log_id, date, type, name, minutes, km, avg_hr, trimp, min_light, min_moderate, min_vigorous, "
            "min_peak, pct_below_first, pct_below_vigorous, ef, decoupling_pct, hrr60, hrr120, steady, notes")


def _strain_of(db, log_id):
    r = _one(db.h, "SELECT strain FROM score_strain_workout WHERE log_id = ?", (log_id,))
    return r["strain"] if r else None


def runs(db, date_from=None, date_to=None, all_types=False):
    fr, to = _range(date_from, date_to, default_days=90)
    sql = f"SELECT {RUN_COLS} FROM analysis_workouts WHERE date BETWEEN ? AND ?"
    if not all_types:
        sql += " AND type = 'RUNNING'"
    rows = _all(db.h, sql + " ORDER BY date", (fr, to))
    anns, by = _annotations_by_date(db, fr, to)
    for r in rows:
        r["id"] = short_id(r["log_id"])
        r["strain"] = _strain_of(db, r["log_id"])
        r["annotation_ids"] = sorted(set(by.get(r["date"], [])) | {
            a["id"] for a in anns if a["target"] in (f"workout:{r['id']}", f"workout:{r['log_id']}")})
    return {"from": fr, "to": to, "runs": [_round(r) for r in rows],
            "running": zones.running_summary([r for r in rows if r["type"] == "RUNNING"]), "annotations": anns}


def run_detail(db, run_id):
    w = _one(db.h, "SELECT * FROM analysis_workouts WHERE log_id = ? OR log_id LIKE ?", (run_id, f"%/{run_id}"))
    if not w:
        return None
    ex = _one(db.m, "SELECT name, duration_min, calories, avg_hr, steps, distance_km, start_time, end_time, "
                    "active_seconds, metrics_summary, splits FROM exercises WHERE log_id = ?", (w["log_id"],))
    if ex:
        ex["metrics_summary"] = json.loads(ex["metrics_summary"] or "{}")
        splits = json.loads(ex.pop("splits") or "[]")
        ex["km_splits"] = [{"km": i + 1,
                            "pace_s_per_km": round(s["metricsSummary"]["averagePaceSecondsPerMeter"] * 1000)
                            if s.get("metricsSummary", {}).get("averagePaceSecondsPerMeter") else None,
                            "avg_hr": _num(s.get("metricsSummary", {}).get("averageHeartRateBeatsPerMinute"))}
                           for i, s in enumerate(splits)]
    sid = short_id(w["log_id"])
    hr, day = [], w["date"]
    if ex and ex["start_time"]:
        hr = zones.hr_10s(db.h, *zones.workout_window(ex["start_time"], ex["end_time"]))
        day = ex["start_time"][:10]  # as the workout series does
    anns = [a for a in ann.list_(db.a, w["date"], w["date"])] + \
        ann.for_target(db.a, f"workout:{sid}") + ann.for_target(db.a, f"workout:{w['log_id']}")
    seen, uniq = set(), []
    for a in anns:
        if a["id"] not in seen:
            seen.add(a["id"])
            uniq.append(a)
    return {"id": sid, "analysis": _round(w), "strain": _strain_of(db, w["log_id"]), "google": ex,
            "hr_zones": zones.zone_minutes(w, hr, zones.day_zones(db.h, day)), "annotations": uniq}


# ----------------------------------------------------------- dashboard ----

def strain_target(recovery_band):
    t = STRAIN_TARGET.get(recovery_band)
    if not t:
        return None
    return {"low": t[0], "high": t[1], "label": t[2],
            "basis": f"recovery {recovery_band}; HRV-guided training direction, WHOOP Strain bands"}


def dashboard(db, day=None):
    d = _d(day) if day else date.today()
    ds = d.isoformat()
    rec = _one(db.h, f"SELECT {RECOVERY_COLS} FROM score_recovery_daily WHERE date = ?", (ds,))
    sleep_g, sleep_p = _sleep_row(db, ds)
    strain = _one(db.h, "SELECT date, strain, band, load, minutes_counted, partial FROM score_strain_daily "
                        "WHERE date = ?", (ds,))
    act = _one(db.m, "SELECT steps, distance_km, active_minutes, floors FROM activity WHERE date = ?", (ds,))
    # watch steps from the intraday table: fresher than the MCP's daily value
    watch_steps = _one(db.h, "SELECT sum(steps) n FROM steps_minute WHERE source = ? AND "
                             "date(t_start + coalesce(utc_offset, 0), 'unixepoch') = ?", (db.watch, ds))
    monday = d - timedelta(days=d.weekday())
    week_runs = _all(db.h, "SELECT log_id, date, km, minutes, trimp, avg_hr, pct_below_vigorous FROM analysis_workouts "
                           "WHERE type='RUNNING' AND date BETWEEN ? AND ? ORDER BY date",
                     (monday.isoformat(), (monday + timedelta(days=6)).isoformat()))
    check = _one(db.h, "SELECT * FROM analysis_daily WHERE night = ?", (ds,))
    warning = None
    if check and check["flag"] in ("AMBER", "RED"):
        high = [k[2:] for k, v in check.items() if k.startswith("z_") and v is not None and v > 1.5]
        warning = f"{check['flag']}: signals worse than baseline: {', '.join(high) or 'several'}"
    band = rec["band"] if rec else None
    return {
        "date": ds,
        "recovery": _round(rec),
        "sleep": {"night": ds, "google": sleep_g, "performance": sleep_p},
        "strain": {"today": _round(strain), "target": strain_target(band)},
        "steps": watch_steps["n"] if watch_steps and watch_steps["n"] is not None else (act or {}).get("steps"),
        "google_daily_activity": act,
        "running_week": {
            "monday": monday.isoformat(), **zones.running_summary(week_runs),
            "list": [{**_round(r), "id": short_id(r["log_id"])} for r in week_runs],
        },
        "daily_check": {"flag": check["flag"], "warning": warning, "hrv_status": check["hrv_status"],
                        "z": {k[2:]: round(v, 2) for k, v in check.items() if k.startswith("z_") and v is not None},
                        "excluded_event": check["excluded_event"]} if check else None,
        "annotations": [a for a in ann.list_(db.a, ds, ds) if a["status"] != "rejected"],
    }


# --------------------------------------------------------------- trend ----

# metric -> (db attr, table, date column, value column)
METRICS = {
    "recovery": ("h", "score_recovery_daily", "date", "recovery"),
    "hrv_ms": ("h", "score_recovery_daily", "date", "hrv_ms"),
    "rhr": ("m", "heart_rate", "date", "resting_hr"),
    "sleep_performance": ("h", "score_sleep_daily", "date", "sleep_performance"),
    "asleep_min": ("h", "score_sleep_daily", "date", "asleep_min"),
    "sleep_min": ("m", "sleep", "date", "total_minutes"),
    "strain": ("h", "score_strain_daily", "date", "strain"),
    "steps": ("m", "activity", "date", "steps"),
    "google_hrv_rmssd": ("m", "hrv", "date", "daily_rmssd"),
    "nrem_hr": ("m", "hrv", "date", "non_rem_hr"),
    "breathing": ("m", "breathing_rate", "date", "breaths_per_min"),
    "skin_temp": ("m", "skin_temperature", "date", "nightly_relative"),
    "spo2": ("m", "spo2", "date", "avg"),
    "vo2max": ("m", "cardio_fitness", "date", "vo2_max"),
    "weight": ("m", "weight", "date", "weight_kg"),
    "trimp": ("h", "(SELECT date, sum(trimp) trimp FROM analysis_workouts GROUP BY date)", "date", "trimp"),
    "google_readiness": ("h", "takeout_daily_readiness", "date", "score"),
    "google_deep_rmssd": ("m", "hrv", "date", "deep_rmssd"),
    "spo2_min": ("m", "spo2", "date", "min"),
    "floors": ("m", "activity", "date", "floors"),
    "distance_km": ("m", "activity", "date", "distance_km"),
    "calories": ("m", "activity", "date", "calories_out"),
    "azm": ("m", "azm", "date", "total_minutes"),
    "deep_min": ("m", "sleep", "date", "deep_minutes"),
    "rem_min": ("m", "sleep", "date", "rem_minutes"),
    "sleep_need_min": ("h", "score_sleep_daily", "date", "need_min"),
    "recovery_composite": ("h", "score_recovery_daily", "date", "composite"),
    "google_sleep_score": ("h", "(SELECT date(sleep_end + coalesce(utc_offset, 0), 'unixepoch') date, overall "
                                "FROM takeout_sleep_score)", "date", "overall"),
    "google_stress": ("h", "takeout_stress_score", "date", "score"),
    "acwr": ("h", "takeout_daily_cardio_load", "date", "acwr"),
    "oxygen_fraction": ("h", "takeout_daily_breathing_quality", "date", "sufficient_oxygen_fraction"),
}


def trend(db, metric, date_from=None, date_to=None):
    if metric not in METRICS:
        raise ValueError(f"metric must be one of {', '.join(METRICS)}")
    src, table, dcol, vcol = METRICS[metric]
    fr, to = _range(date_from, date_to)
    fr = _from_watch(db, fr)
    pts = [{"date": r["d"], "value": round(r["v"], 3) if isinstance(r["v"], float) else r["v"]}
           for r in _all(getattr(db, src), f"SELECT {dcol} d, {vcol} v FROM {table} WHERE {dcol} BETWEEN ? AND ? "
                                           f"AND {vcol} IS NOT NULL ORDER BY {dcol}", (fr, to))]
    vals = [p["value"] for p in pts]
    anns, by = _annotations_by_date(db, fr, to)
    for p in pts:
        p["annotation_ids"] = by.get(p["date"], [])
    summary = None
    if vals:
        summary = {"n": len(vals), "mean": round(sum(vals) / len(vals), 2), "min": min(vals), "max": max(vals),
                   "first": vals[0], "last": vals[-1]}
    return {"metric": metric, "from": fr, "to": to, "points": pts, "summary": summary, "annotations": anns}


# ------------------------------------------------------- explain score ----

def _config(db):
    try:
        return json.loads(db.paths.scores_config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def explain_score(db, day, score):
    ds = _d(day).isoformat()
    cfg = _config(db)
    anns = [a for a in ann.list_(db.a, ds, ds) if a["status"] != "rejected"]
    if score == "recovery":
        r = _one(db.h, "SELECT * FROM score_recovery_daily WHERE date = ?", (ds,))
        if not r:
            return {"date": ds, "score": score, "error": "no recovery score for this date"}
        w = cfg.get("recovery", {}).get("weights", {"hrv": 0.5, "rhr": 0.25, "sleep": 0.15, "resp": 0.1})
        comps = {k: {"z": r[f"z_{k}"], "weight": w.get(k),
                     "contribution": round(w[k] * r[f"z_{k}"], 3) if r[f"z_{k}"] is not None else None}
                 for k in ("hrv", "rhr", "sleep", "resp")}
        return {"date": ds, "score": "recovery", "value": r["recovery"], "band": r["band"],
                "formula": "C = sum(w*z) / sum(w over present z) - penalty; Recovery = 1 + 98*Phi(C/s). "
                           "z > 0 = better than your 30-night robust baseline (illness/alcohol nights excluded).",
                "components": comps, "composite": r["composite"], "s": r["s"], "penalty": r["penalty"],
                "flags": r["flags"],
                "raw": {k: r.get(k) for k in ("hrv_ms", "hrv_source", "google_rmssd", "deep_hrv_ms", "rhr", "breathing", "skin_rel",
                                          "spo2")},
                "baseline_n": r["baseline_n"], "provisional": bool(r["provisional"]),
                "excluded_event_night": bool(r["excluded_event"]), "annotations": anns}
    if score == "sleep":
        r = _one(db.h, "SELECT * FROM score_sleep_daily WHERE date = ?", (ds,))
        if not r:
            return {"date": ds, "score": score, "error": "no sleep score for this night",
                    "hypnogram": hypnogram(db, ds)}
        w = {"sufficiency": 0.60, "consistency": 0.15, "efficiency": 0.15, "calm": 0.10}
        comps = {k: {"value": r[k], "weight": w[k],
                     "contribution": round(w[k] * r[k], 1) if r[k] is not None else None} for k in w}
        return {"date": ds, "score": "sleep", "value": r["sleep_performance"],
                "formula": "Sleep Performance = 0.60 Sufficiency + 0.15 Consistency + 0.15 Efficiency + 0.10 Calm",
                "components": comps,
                "need": {"total_min": r["need_min"], "baseline_min": r["need_baseline_min"],
                         "from_strain_min": r["need_strain_min"], "from_sleep_debt_min": r["need_debt_min"],
                         "minus_naps_min": r["nap_prev_day_min"]},
                "asleep_min": r["asleep_min"], "hypnogram": hypnogram(db, ds), "annotations": anns}
    if score == "strain":
        r = _one(db.h, "SELECT * FROM score_strain_daily WHERE date = ?", (ds,))
        if not r:
            return {"date": ds, "score": score, "error": "no strain score for this date"}
        k = cfg.get("strain", {}).get("k")
        wo = _all(db.h, "SELECT log_id, name, log_type, minutes, cardio_load, muscular_load, load, strain "
                        "FROM score_strain_workout WHERE date = ?", (ds,))
        return {"date": ds, "score": "strain", "value": r["strain"], "band": r["band"],
                "formula": "Strain = 21 * (1 - e^(-L/k)); L = sum over minutes of 0.64*HRr*e^(1.92*HRr) for "
                           "HRr >= 0.30, plus 0.5 per strength-training minute",
                "k": k, "load": r["load"], "cardio_load": r["cardio_load"], "muscular_load": r["muscular_load"],
                "minutes_counted": r["minutes_counted"], "valid_minutes": r["valid_minutes"],
                "rhr_used": r["rhr_used"], "hrmax_used": r["hrmax_used"], "partial_day": bool(r["partial"]),
                "workouts": [{**_round(x), "id": short_id(x["log_id"])} for x in wo], "annotations": anns}
    raise ValueError("score must be recovery, sleep or strain")
