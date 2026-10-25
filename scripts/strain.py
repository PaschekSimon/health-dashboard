"""Day and workout Strain (0 to 21), WHOOP-style. Spec section 1.

Per-minute HR (median, >= 10 samples) -> HR reserve fraction
HRr = (HR - RHR) / (HRmax - RHR), RHR = rolling 14-night median of non-REM HR,
HRmax = max(highest 30 s workout average in the last 180 days, 208 - 0.7 age).
Load per minute = 0.64 HRr e^(1.92 HRr) for HRr >= 0.30; strength workouts add
minutes x 0.5. Strain = 21 (1 - e^(-L/k)), k frozen in scores_config.json so the
95th-percentile day = 17.5 (k = L_p95 / ln 6).

    python -I strain.py [--recalibrate]
Writes score_strain_daily and score_strain_workout in health.db.
"""

import math
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir
import baseline as bl
import scores_common as sc

HRR_FLOOR = 0.30
STRENGTH_FACTOR = 0.5
P95_TARGET = 17.5
FULL_DAY_MIN = 1000   # valid HR minutes for a day to count in calibration


def band(s):
    if s is None:
        return None
    return "light" if s < 10 else "moderate" if s < 14 else "high" if s < 18 else "all out"


def nrem_by_night(mcp):
    return {date.fromisoformat(d): v for d, v in
            mcp.execute("SELECT date, non_rem_hr FROM hrv WHERE non_rem_hr IS NOT NULL")}


def rhr_for_day(nrem, d):
    vals = [v for n, v in nrem.items() if d - timedelta(days=13) <= n <= d]
    if not vals:  # before the first night: nearest nights
        near = sorted(nrem, key=lambda n: abs((n - d).days))[:7]
        vals = [nrem[n] for n in near]
    return statistics.median(vals)


def workout_peaks(health, mcp):
    """[(day, peak 30 s average HR)] for every logged workout (any source;
    Strava copies have the same HR, which is fine for a maximum)."""
    out = []
    for d, st, et in mcp.execute("SELECT date, start_time, end_time FROM exercises WHERE start_time IS NOT NULL"):
        s = bl.hr_series(health, sc.parse_time(st), sc.parse_time(et))
        best, j, acc = None, 0, 0
        for i, (t, b) in enumerate(s):
            acc += b
            while s[j][0] < t - 29:
                acc -= s[j][1]
                j += 1
            if i - j + 1 >= 8:  # at least 8 samples in the 30 s window
                avg = acc / (i - j + 1)
                best = avg if best is None or avg > best else best
        if best:
            out.append((date.fromisoformat(d), best))
    return out


def compute(health, mcp, recalibrate=False, verbose=True):
    age, _ = sc.age_years(mcp)
    tanaka = 208 - 0.7 * age
    peaks = workout_peaks(health, mcp)
    nrem = nrem_by_night(mcp)
    mhr = sc.minute_hr(health)
    today = sc.today_local()

    def hrmax_for(d):
        p = [v for wd, v in peaks if d - timedelta(days=180) <= wd <= d]
        return max([tanaka] + p)

    # per-day cardio load
    days = {}
    for m, (bpm, off) in mhr.items():
        d = sc.local_date(m * 60, off)
        if d < sc.START:
            continue
        rec = days.get(d)
        if rec is None:
            rec = days[d] = {"date": d, "rhr": rhr_for_day(nrem, d), "hrmax": hrmax_for(d),
                             "cardio": 0.0, "counted": 0, "valid": 0}
        rec["valid"] += 1
        hrr = (bpm - rec["rhr"]) / (rec["hrmax"] - rec["rhr"])
        if hrr >= HRR_FLOOR:
            hrr = min(hrr, 1.0)
            rec["cardio"] += 0.64 * hrr * math.exp(1.92 * hrr)
            rec["counted"] += 1

    # workouts (watch only): load inside the session, muscular bonus for strength
    workouts = []
    for w in bl.watch_workouts(mcp):
        d = w["day"]
        rhr, hmax = rhr_for_day(nrem, d), hrmax_for(d)
        load = 0.0
        for m in range(w["t_start"] // 60, w["t_end"] // 60 + 1):
            if m in mhr:
                hrr = (mhr[m][0] - rhr) / (hmax - rhr)
                if hrr >= HRR_FLOOR:
                    hrr = min(hrr, 1.0)
                    load += 0.64 * hrr * math.exp(1.92 * hrr)
        minutes = (w["t_end"] - w["t_start"]) / 60
        musc = minutes * STRENGTH_FACTOR if w["log_type"] == "STRENGTH_TRAINING" else 0.0
        if d in days:
            days[d]["muscular"] = days[d].get("muscular", 0.0) + musc
        workouts.append({"log_id": w["log_id"], "date": d.isoformat(), "name": w["name"],
                         "log_type": w["log_type"], "minutes": round(minutes, 1),
                         "cardio_load": load, "muscular_load": musc, "load": load + musc})

    for r in days.values():
        r["muscular"] = r.get("muscular", 0.0)
        r["load"] = r["cardio"] + r["muscular"]
        r["partial"] = 1 if (r["date"] >= today or r["valid"] < FULL_DAY_MIN) else 0

    # calibration (once)
    cfg = sc.load_config()
    if recalibrate or "strain" not in cfg:
        full = [r["load"] for r in days.values() if not r["partial"]]
        p95 = sc.percentile(full, 95)
        k = p95 / math.log(21 / (21 - P95_TARGET))
        strains = sorted(21 * (1 - math.exp(-x / k)) for x in full)
        cfg["strain"] = {"k": k, "load_p95": p95, "n_days": len(full),
                         "median_day_strain": statistics.median(strains),
                         "calibrated_on": sc.today_local().isoformat(),
                         "hrr_floor": HRR_FLOOR, "strength_factor": STRENGTH_FACTOR,
                         "tanaka_hrmax": tanaka}
        sc.save_config(cfg)
    k = cfg["strain"]["k"]

    def to_strain(x):
        return 21 * (1 - math.exp(-x / k))

    rows = []
    for d in sorted(days):
        r = days[d]
        s = to_strain(r["load"])
        rows.append({"date": d.isoformat(), "strain": round(s, 2), "band": band(s),
                     "load": round(r["load"], 2), "cardio_load": round(r["cardio"], 2),
                     "muscular_load": round(r["muscular"], 2), "minutes_counted": r["counted"],
                     "valid_minutes": r["valid"], "rhr_used": r["rhr"], "hrmax_used": round(r["hrmax"], 1),
                     "partial": r["partial"]})
    for w in workouts:
        w["strain"] = round(to_strain(w["load"]), 2)
        w["load"] = round(w["load"], 2)
    sc.write_rows(health, "score_strain_daily",
                  ["date", "strain", "band", "load", "cardio_load", "muscular_load", "minutes_counted",
                   "valid_minutes", "rhr_used", "hrmax_used", "partial"], ["date"], rows)
    sc.write_rows(health, "score_strain_workout",
                  ["log_id", "date", "name", "log_type", "minutes", "cardio_load", "muscular_load",
                   "load", "strain"], ["log_id"], workouts)

    val = validate(health, rows)
    if verbose:
        report(rows, workouts, cfg, val, health)
    return rows, val


def validate(health, rows):
    """Correlate daily Strain with Google's daily cardio load (Takeout)."""
    gl = dict(health.execute("SELECT date(t + utc_offset, 'unixepoch'), sum(total) "
                             "FROM takeout_cardio_load_minute GROUP BY 1"))
    full = [r for r in rows if not r["partial"] and r["date"] in gl]
    xs = [r["strain"] for r in full]
    ls = [r["load"] for r in full]
    ys = [gl[r["date"]] for r in full]
    return {"pearson_strain": sc.pearson(xs, ys)[0], "spearman_strain": sc.spearman(xs, ys)[0],
            "pearson_load": sc.pearson(ls, ys)[0], "n": len(full)}


def report(rows, workouts, cfg, val, health):
    c = cfg["strain"]
    print(f"STRAIN  k={c['k']:.1f} (95th-pct day load {c['load_p95']:.1f}, {c['n_days']} full days, "
          f"calibrated {c['calibrated_on']}); HRmax used {rows[-1]['hrmax_used']}, "
          f"Google zones imply {bl.current_max_hr(health)}")
    print(f"  vs Google cardio load: Pearson r={sc.fmt(val['pearson_strain'], 2)} (Strain), "
          f"{sc.fmt(val['pearson_load'], 2)} (raw load), Spearman {sc.fmt(val['spearman_strain'], 2)}, n={val['n']}")
    top = sorted((r for r in rows if not r["partial"]), key=lambda r: -r["strain"])[:3]
    print("  top days: " + ", ".join(f"{r['date']} {r['strain']:.1f}" for r in top))
    print("  last 7 days: " + ", ".join(f"{r['date'][5:]} {r['strain']:.1f}{'*' if r['partial'] else ''}"
                                        for r in rows[-7:]) + "   (* partial day)")
    print("  recent workouts: " + ", ".join(f"{w['date'][5:]} {w['name']} {w['strain']:.1f}" for w in workouts[-5:]))


if __name__ == "__main__":
    h, m = sc.connect()
    compute(h, m, recalibrate="--recalibrate" in sys.argv)
