"""Sleep Performance (0 to 100) and Recovery (1 to 99 %), WHOOP-style.
Spec sections 2 and 3. Nights are keyed by the local wake date.

Sleep Performance = 0.60 Sufficiency + 0.15 Consistency + 0.15 Efficiency + 0.10 Calm
  need = baseline (8.2 h, personalised over 60 days, 7.0 to 9.5 h)
         + b min per Strain point of the previous day above the median day (max 45),
           b learned from your own nights: slope of minutes asleep on the previous
           day's Strain over the last 60 nights, 0 to 4 (0 until 20 nights). Exercise
           only slightly lengthens sleep (Kredlow 2015 meta-analysis), so no fixed dose.
         + 0.5 / 0.25 / 0.125 x shortfall of the previous three nights
         - naps of the previous day
Recovery: HRV = Google's night-average RMSSD (hrv.daily_rmssd; deep-sleep windows only
  as fallback): wearable sleep staging is ~60-75 % accurate (Dial 2025), and the
  night average was steadier and tracked RHR and illness better.
  C = w_hrv z_HRV + w_rhr z_RHR + w_sleep z_sleep + w_resp z_resp against a
  robust 30-night baseline without illness/alcohol nights, -0.5 if skin temp
  >= +0.5 C or SpO2 >= 2 points below baseline; Recovery = 1 + 98 Phi(C / s).

    python -I recovery.py [--retune]
Writes score_sleep_daily and score_recovery_daily in health.db. Needs
score_strain_daily (runs strain.py first if it's missing).
"""

import bisect
import math
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir
import baseline as bl
import scores_common as sc

DEFAULT_WEIGHTS = {"hrv": 0.50, "rhr": 0.25, "sleep": 0.15, "resp": 0.10}
SP_WEIGHTS = {"sufficiency": 0.60, "consistency": 0.15, "efficiency": 0.15, "calm": 0.10}
POOR_R = 0.5          # Pearson r vs Readiness below this -> tune weights once
SHORT_NIGHT_MIN = 420  # 7 h
STRAIN_SLOPE_MAX = 4.0   # min of extra sleep need per Strain point, upper bound
STRAIN_SLOPE_MIN_N = 20  # nights needed before the slope is used
# minimum baseline spread: RHR is whole bpm (MAD ~1), breathing is in 0.2 steps
SD_FLOOR = {"rhr": 2.0, "resp": 0.4}


def band(r):
    if r is None:
        return None
    return "red" if r < 34 else "yellow" if r < 67 else "green"


# ------------------------------------------------------------ sleep ------

def asleep_minutes(s):
    if not s["stages"]:
        return (s["end"] - s["start"]) / 60, 0.0
    asleep = sum(e - b for b, e, t in s["stages"] if t != "AWAKE") / 60
    awake = sum(e - b for b, e, t in s["stages"] if t == "AWAKE") / 60
    return asleep, awake


def is_nap(s):
    hour = datetime.fromtimestamp(s["start"] + s["offset"], tz=timezone.utc).hour
    return 10 <= hour < 20


def asleep_set(sessions):
    """Set of UTC minutes asleep (any session, stages other than AWAKE)."""
    out = set()
    for s in sessions:
        spans = [(b, e) for b, e, t in s["stages"] if t != "AWAKE"] or [(s["start"], s["end"])]
        for b, e in spans:
            out.update(range(b // 60, e // 60))
    return out


def sri(asleep, end_utc, days):
    """Sleep Regularity Index (0 to 100): % of minutes in the same state 24 h
    apart, over `days` days ending at end_utc ((days - 1) day pairs)."""
    end = end_utc // 60
    start = end - days * 1440
    same = sum((m in asleep) == ((m + 1440) in asleep) for m in range(start, end - 1440))
    return 100 * same / ((days - 1) * 1440)


def hrv_windows(health):
    """Watch 5-min RMSSD windows [(t, rmssd_ms)] in time order."""
    return health.execute("SELECT t, rmssd_ms FROM hrv_samples WHERE source=? AND rmssd_ms > 0 ORDER BY t",
                          (sc.WATCH,)).fetchall()


def windows_in(hrv, ts, start, end):
    """The (t, rmssd) windows with start <= t < end; ts = [t for t, _ in hrv]."""
    return hrv[bisect.bisect_left(ts, start):bisect.bisect_left(ts, end)]


def strain_slope(actual_hist, strain_by_day, night):
    """Extra minutes asleep per Strain point of the previous day, from the 60
    nights before `night` (OLS), clipped to 0..STRAIN_SLOPE_MAX; 0 with too few nights."""
    xs, ys = [], []
    for m, mins in actual_hist.items():
        if night - timedelta(days=60) <= m < night and not sc.is_excluded(m):
            st = strain_by_day.get((m - timedelta(days=1)).isoformat())
            if st is not None:
                xs.append(st)
                ys.append(mins)
    if len(xs) < STRAIN_SLOPE_MIN_N or len(set(xs)) < 2:
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return min(max(b, 0.0), STRAIN_SLOPE_MAX)


def sleep_scores(health, mcp, strain_by_day, median_strain, rec_hrv):
    """Per night: sufficiency, consistency, efficiency, calm, performance.
    rec_hrv = {night: lnRMSSD} for the need personalisation."""
    sessions = bl.sleep_sessions(mcp)
    asleep_all = asleep_set(sessions)
    mhr = sc.minute_hr(health)
    hrv = hrv_windows(health)
    ts = [t for t, _ in hrv]

    mains, naps, extra = {}, {}, {}
    for s in sessions:
        if is_nap(s):
            d = sc.local_date(s["start"], s["offset"])
            naps[d] = naps.get(d, 0) + asleep_minutes(s)[0]
            continue
        cur = mains.get(s["night"])
        if cur is None or s["end"] - s["start"] > cur["end"] - cur["start"]:
            if cur is not None:
                extra[s["night"]] = extra.get(s["night"], 0) + asleep_minutes(cur)[0]
            mains[s["night"]] = s
        else:
            extra[s["night"]] = extra.get(s["night"], 0) + asleep_minutes(s)[0]

    # per-night sleep HR stats and HRV windows, for the "calm" baselines
    night_stats = {}
    for n, s in mains.items():
        nrem = [mhr[m][0] for b, e, t in s["stages"] if t in ("LIGHT", "DEEP")
                for m in range(b // 60, e // 60) if m in mhr]
        wins = [r for t, r in windows_in(hrv, ts, s["start"], s["end"])]
        night_stats[n] = {"nrem_mean": statistics.mean(nrem) if len(nrem) > 30 else None,
                          "nrem_sd": statistics.stdev(nrem) if len(nrem) > 30 else None,
                          "wins": wins}
    eligible_all = [n for n in mains if not sc.is_excluded(n)]

    out, need_hist, actual_hist = {}, {}, {}
    for n in sorted(mains):
        s = mains[n]
        asleep, awake = asleep_minutes(s)
        asleep_total = asleep + extra.get(n, 0)
        actual_hist[n] = asleep_total

        # baseline need: nights followed by above-median HRV, not after a short night
        window = [m for m in actual_hist if n - timedelta(days=60) <= m < n and not sc.is_excluded(m)]
        hv = [rec_hrv[m] for m in window if rec_hrv.get(m) is not None]
        need_b = 8.2 * 60
        if len(hv) >= 10:
            med = statistics.median(hv)
            good = [actual_hist[m] for m in window
                    if rec_hrv.get(m) is not None and rec_hrv[m] > med
                    and actual_hist.get(m - timedelta(days=1), 9999) >= SHORT_NIGHT_MIN]
            if len(good) >= 5:
                need_b = min(max(statistics.mean(good), 7.0 * 60), 9.5 * 60)
        prev_day = n - timedelta(days=1)
        st = strain_by_day.get(prev_day.isoformat())
        slope = strain_slope(actual_hist, strain_by_day, n)
        need_strain = min(max(0.0, (st - median_strain) * slope), 45) if st is not None else 0.0
        # shortfall vs that night's need without its own debt term, so debt
        # doesn't compound from night to night
        need_debt = sum(w * max(0.0, need_hist[n - timedelta(days=i)] - actual_hist[n - timedelta(days=i)])
                        for i, w in ((1, 0.5), (2, 0.25), (3, 0.125))
                        if n - timedelta(days=i) in need_hist)
        nap = naps.get(prev_day, 0.0)
        need = max(need_b + need_strain + need_debt - nap, 300)
        need_hist[n] = need_b + need_strain

        sufficiency = min(1.0, asleep_total / need) * 100
        local_noon = sc.local_midnight_utc(n, s["offset"]) + 12 * 3600
        consistency = sri(asleep_all, local_noon, 5)
        efficiency = 100 * asleep / (asleep + awake) if asleep + awake else None

        # calm: share of asleep minutes in a high-HR or low-HRV state
        prior = [m for m in sorted(eligible_all, reverse=True) if m < n][:sc.BASE_NIGHTS]
        if len(prior) < sc.BASE_MIN:
            prior = eligible_all
        means = [night_stats[m]["nrem_mean"] for m in prior if night_stats[m]["nrem_mean"]]
        sds = [night_stats[m]["nrem_sd"] for m in prior if night_stats[m]["nrem_sd"]]
        thr_hr = statistics.median(means) + 2 * statistics.median(sds)
        p10 = sc.percentile([r for m in prior for r in night_stats[m]["wins"]], 10)
        low_hrv = set()
        for t, r in windows_in(hrv, ts, s["start"], s["end"]):
            if r < p10:
                low_hrv.update(range(t // 60, t // 60 + 5))
        sleep_min = [m for b, e, t in s["stages"] if t != "AWAKE" for m in range(b // 60, e // 60)]
        stress = sum(1 for m in sleep_min if m in low_hrv or (m in mhr and mhr[m][0] > thr_hr))
        calm = 100 - 100 * stress / len(sleep_min) if sleep_min else None

        parts = {"sufficiency": sufficiency, "consistency": consistency,
                 "efficiency": efficiency, "calm": calm}
        avail = {k: v for k, v in parts.items() if v is not None}
        wsum = sum(SP_WEIGHTS[k] for k in avail)
        perf = sum(SP_WEIGHTS[k] * v for k, v in avail.items()) / wsum
        out[n] = {"date": n.isoformat(), "sleep_performance": round(perf, 1),
                  "sufficiency": round(sufficiency, 1), "consistency": round(consistency, 1),
                  "efficiency": round(efficiency, 1) if efficiency else None,
                  "calm": round(calm, 1) if calm is not None else None,
                  "asleep_min": round(asleep_total), "need_min": round(need),
                  "need_baseline_min": round(need_b), "need_strain_min": round(need_strain, 1),
                  "need_debt_min": round(need_debt, 1), "nap_prev_day_min": round(nap),
                  "need_strain_slope": round(slope, 2)}
    return out, mains


# --------------------------------------------------------- recovery ------

def nightly_hrv(health, mcp, mains):
    """{night: (ln RMSSD, ms, source)}: Google's night-average RMSSD for the wake
    date; if Google has none, the deep-sleep value below."""
    google = {date.fromisoformat(d): v for d, v in mcp.execute("SELECT date, daily_rmssd FROM hrv")
              if v and v > 0}
    deep = deep_sleep_hrv(health, mcp, mains)
    out = {}
    for n in mains:
        if n in google:
            out[n] = (math.log(google[n]), google[n], "google_night")
        elif n in deep:
            out[n] = deep[n]
    return out


def deep_sleep_hrv(health, mcp, mains):
    """{night: (ln RMSSD, ms, source)} from the 5-min windows overlapping the last
    deep segment of the main sleep; fallback median of deep windows, then of
    all windows in the main sleep. (Used for Recovery until 2026-10-24; kept
    for display as deep_hrv_ms.)"""
    hrv = hrv_windows(health)
    ts = [t for t, _ in hrv]
    out = {}
    for n, s in mains.items():
        wins = windows_in(hrv, ts, s["start"], s["end"])
        deep = [(b, e) for b, e, t in s["stages"] if t == "DEEP"]
        ms, src = None, None
        if deep:
            b, e = deep[-1]
            last = [r for t, r in wins if t < e and t + 300 > b]
            if last:
                ms, src = statistics.mean(last), "last_deep"
            else:
                any_deep = [r for t, r in wins if any(t < e2 and t + 300 > b2 for b2, e2 in deep)]
                if any_deep:
                    ms, src = statistics.median(any_deep), "deep_median"
        if ms is None and len(wins) >= 6:
            ms, src = statistics.median(r for t, r in wins), "sleep_median"
        if ms:
            out[n] = (math.log(ms), ms, src)
    return out


def add_columns(health, table, cols):
    """Columns added after the table was first created (CREATE IF NOT EXISTS won't)."""
    if not health.execute("SELECT name FROM sqlite_master WHERE name = ?", (table,)).fetchone():
        return
    have = {r[1] for r in health.execute(f"PRAGMA table_info({table})")}
    for c in cols:
        if c not in have:
            health.execute(f"ALTER TABLE {table} ADD COLUMN {c} REAL")
    health.commit()


def daily(mcp, table, col):
    return {date.fromisoformat(d): v for d, v in mcp.execute(f"SELECT date, {col} FROM {table}") if v is not None}


def recovery_scores(inputs, weights):
    """inputs: {night: {...}} with z's prepared; returns {night: row}."""
    comp = {}
    for n, x in inputs.items():
        zs = {k: x[f"z_{k}"] for k in weights if x.get(f"z_{k}") is not None}
        if "hrv" not in zs:
            continue
        wsum = sum(weights[k] for k in zs)
        comp[n] = sum(weights[k] * v for k, v in zs.items()) / wsum - x["penalty"]
    eligible = {n: c for n, c in comp.items() if not sc.is_excluded(n)}
    glob_sd = statistics.stdev(eligible.values())
    out = {}
    for n, c in comp.items():
        prior = sc.prior_eligible(eligible, n)
        s = statistics.stdev(prior) if len(prior) >= sc.BASE_MIN else glob_sd
        out[n] = {"composite": c, "s": s, "recovery": 1 + 98 * sc.PHI(c / s)}
    return out


def tune(inputs, readiness):
    """Grid search (step 0.05, HRV >= 0.2) for the weights that maximise Pearson r
    of Recovery vs Readiness."""
    best = None
    steps = [i / 20 for i in range(21)]
    for wh, wr, ws in product(steps, steps, steps):
        wp = round(1 - wh - wr - ws, 2)
        if wh < 0.2 or wp < 0:
            continue
        w = {"hrv": wh, "rhr": wr, "sleep": ws, "resp": wp}
        rec = recovery_scores(inputs, w)
        common = [n for n in rec if n in readiness]
        r, _ = sc.pearson([rec[n]["recovery"] for n in common], [readiness[n] for n in common])
        if r is not None and (best is None or r > best[0]):
            best = (r, w)
    return best


def compute(health, mcp, retune=False, verbose=True):
    if not health.execute("SELECT name FROM sqlite_master WHERE name='score_strain_daily'").fetchone():
        import strain
        strain.compute(health, mcp, verbose=False)
    cfg = sc.load_config()
    strain_by_day = dict(health.execute("SELECT date, strain FROM score_strain_daily"))
    median_strain = cfg["strain"]["median_day_strain"]

    mains = bl.main_sleep_by_night(mcp)
    hrv = nightly_hrv(health, mcp, {n: s for n, s in mains.items()})
    ln_hrv = {n: v[0] for n, v in hrv.items()}
    sleep, mains = sleep_scores(health, mcp, strain_by_day, median_strain, ln_hrv)
    hrv = nightly_hrv(health, mcp, mains)
    ln_hrv = {n: v[0] for n, v in hrv.items()}
    deep_hrv = deep_sleep_hrv(health, mcp, mains)

    rhr = daily(mcp, "heart_rate", "resting_hr")
    resp = daily(mcp, "breathing_rate", "breaths_per_min")
    skin = daily(mcp, "skin_temperature", "nightly_relative")
    spo2 = daily(mcp, "spo2", "avg")
    g_rmssd = daily(mcp, "hrv", "daily_rmssd")
    sp = {n: r["sleep_performance"] for n, r in sleep.items()}

    inputs = {}
    for n in sorted(mains):
        z_hrv, nb = sc.robust_z(ln_hrv, n, +1)
        z_rhr, _ = sc.robust_z(rhr, n, -1, floor=SD_FLOOR["rhr"])
        z_br, _ = sc.robust_z(resp, n, +1, floor=SD_FLOOR["resp"])
        z_resp = -max(0.0, z_br) if z_br is not None else None
        z_sleep = (sp[n] - 85) / 10 if n in sp else None
        flags, penalty = [], 0.0
        for series, name, test in ((skin, "skin temp", lambda x, med: x - med >= 0.5),
                                   (spo2, "SpO2", lambda x, med: med - x >= 2)):
            prior = sc.prior_eligible(series, n)
            if n in series and len(prior) >= sc.BASE_MIN and test(series[n], statistics.median(prior)):
                flags.append(name)
        if flags:
            penalty = 0.5
        inputs[n] = {"z_hrv": z_hrv, "z_rhr": z_rhr, "z_sleep": z_sleep, "z_resp": z_resp,
                     "penalty": penalty, "flags": ", ".join(flags) or None, "baseline_n": nb}

    readiness = {date.fromisoformat(d): s for d, s in
                 health.execute("SELECT date, score FROM takeout_daily_readiness WHERE score IS NOT NULL")}
    weights = cfg.get("recovery", {}).get("weights", DEFAULT_WEIGHTS)
    rec = recovery_scores(inputs, weights)
    common = [n for n in rec if n in readiness]
    r0, n0 = sc.pearson([rec[n]["recovery"] for n in common], [readiness[n] for n in common])
    tuned = None
    if retune or "recovery" not in cfg:
        rd = sc.pearson([recovery_scores(inputs, DEFAULT_WEIGHTS)[n]["recovery"] for n in common],
                        [readiness[n] for n in common])[0]
        entry = {"default_weights": DEFAULT_WEIGHTS, "r_default": rd, "poor_threshold": POOR_R,
                 "decided_on": sc.today_local().isoformat()}
        tuned = tune(inputs, readiness)
        if tuned is None:  # no Google Readiness (Takeout) to check against
            entry.update(weights=DEFAULT_WEIGHTS, tuned=False)
        elif rd is None or rd < POOR_R:
            entry.update(weights=tuned[1], r_tuned=tuned[0], tuned=True)
        else:  # good enough: keep the spec weights, record what tuning would give
            entry.update(weights=DEFAULT_WEIGHTS, tuned=False,
                         tune_check_not_applied={"weights": tuned[1], "r": tuned[0]})
        cfg["recovery"] = entry
        sc.save_config(cfg)
        weights = entry["weights"]
        rec = recovery_scores(inputs, weights)
        r0, n0 = sc.pearson([rec[n]["recovery"] for n in common], [readiness[n] for n in common])

    rows = []
    for n in sorted(inputs):
        x, r = inputs[n], rec.get(n)
        ln, ms, src = hrv.get(n, (None, None, None))
        dms = deep_hrv.get(n, (None, None, None))[1]
        rows.append({"date": n.isoformat(),
                     "recovery": round(r["recovery"], 1) if r else None,
                     "band": band(r["recovery"]) if r else None,
                     "composite": round(r["composite"], 3) if r else None,
                     "s": round(r["s"], 3) if r else None,
                     "hrv_ms": round(ms, 1) if ms else None, "ln_rmssd": ln, "hrv_source": src,
                     "deep_hrv_ms": round(dms, 1) if dms else None,
                     "google_rmssd": g_rmssd.get(n), "rhr": rhr.get(n), "breathing": resp.get(n),
                     "skin_rel": skin.get(n), "spo2": spo2.get(n),
                     "z_hrv": x["z_hrv"], "z_rhr": x["z_rhr"], "z_sleep": x["z_sleep"], "z_resp": x["z_resp"],
                     "penalty": x["penalty"], "flags": x["flags"], "baseline_n": x["baseline_n"],
                     "provisional": 1 if (x["baseline_n"] or 0) < sc.BASE_PROVISIONAL else 0,
                     "excluded_event": 1 if sc.is_excluded(n) else 0,
                     "sleep_performance": sp.get(n)})
    add_columns(health, "score_sleep_daily", ["need_strain_slope"])
    add_columns(health, "score_recovery_daily", ["deep_hrv_ms"])
    sc.write_rows(health, "score_sleep_daily",
                  ["date", "sleep_performance", "sufficiency", "consistency", "efficiency", "calm",
                   "asleep_min", "need_min", "need_baseline_min", "need_strain_min", "need_debt_min",
                   "nap_prev_day_min", "need_strain_slope"], ["date"], list(sleep.values()))
    sc.write_rows(health, "score_recovery_daily", list(rows[0].keys()), ["date"], rows)

    val = validate(health, rows, sleep, readiness, r0, n0)
    val["weights"] = weights
    val["config"] = cfg["recovery"]
    if verbose:
        report(rows, sleep, val)
    return rows, sleep, val


def validate(health, rows, sleep, readiness, r_rec, n_rec):
    # Google sleep score v2: the longest sleep per local wake date
    g = {}
    for end, off, overall, mins in health.execute(
            "SELECT sleep_end, utc_offset, overall, sleep_minutes FROM takeout_sleep_score "
            "WHERE algorithm='sleep_session_v2' AND overall IS NOT NULL"):
        d = sc.local_date(end, off)
        if d not in g or mins > g[d][1]:
            g[d] = (overall, mins)
    nights = sorted(sleep)
    xs = [sleep[n]["sleep_performance"] for n in nights if n in g]
    ys = [g[n][0] for n in nights if n in g]
    recd = {date.fromisoformat(r["date"]): r for r in rows if r["recovery"] is not None}
    common = [n for n in recd if n in readiness]
    ill = [recd[n] for n in recd if bl.is_ill(n)]
    alc_night = max((n for n in bl.ALCOHOL_NIGHTS if n in recd), default=None)
    alc = recd.get(alc_night)
    return {"r_recovery_readiness": r_rec, "n_recovery": n_rec,
            "rho_recovery_readiness": sc.spearman([recd[n]["recovery"] for n in common],
                                                  [readiness[n] for n in common])[0],
            "r_sleep_v2": sc.pearson(xs, ys)[0], "rho_sleep_v2": sc.spearman(xs, ys)[0], "n_sleep": len(xs),
            "illness_n": len(ill), "illness_red": sum(r["band"] == "red" for r in ill),
            "illness_yellow": sum(r["band"] == "yellow" for r in ill),
            "illness_green": sum(r["band"] == "green" for r in ill),
            "alcohol_night": alc_night, "alcohol": alc, "alcohol_sleep": sleep.get(alc_night),
            "google_sleep_alcohol": g.get(alc_night, (None,))[0]}


def report(rows, sleep, val):
    c = val["config"]
    w = val["weights"]
    print(f"RECOVERY  weights HRV {w['hrv']:.2f} / RHR {w['rhr']:.2f} / sleep {w['sleep']:.2f} / resp {w['resp']:.2f}"
          f" ({'tuned on Readiness' if c.get('tuned') else 'spec defaults'}; default r={sc.fmt(c['r_default'], 2)})")
    print(f"  vs Google Readiness: Pearson r={sc.fmt(val['r_recovery_readiness'], 2)}, "
          f"Spearman {sc.fmt(val['rho_recovery_readiness'], 2)}, n={val['n_recovery']}")
    a = val["alcohol"]
    if a:
        print(f"  latest alcohol night {val['alcohol_night']}: Recovery {a['recovery']:.0f} ({a['band']}), HRV {a['hrv_ms']} ms, flags {a['flags']}")
    print(f"  illness nights: {val['illness_red']} red, {val['illness_yellow']} yellow, "
          f"{val['illness_green']} green of {val['illness_n']}")
    print(f"SLEEP PERFORMANCE vs Google sleep score v2: Pearson r={sc.fmt(val['r_sleep_v2'], 2)}, "
          f"Spearman {sc.fmt(val['rho_sleep_v2'], 2)}, n={val['n_sleep']}")
    s = val["alcohol_sleep"]
    if s:
        print(f"  latest alcohol night: Sleep Performance {s['sleep_performance']:.0f} "
              f"(Google {sc.fmt(val['google_sleep_alcohol'], 0)})")
    print("  last 7 nights (Recovery / Sleep Perf / need h / asleep h):")
    for r in rows[-7:]:
        sl = sleep[date.fromisoformat(r["date"])]
        print(f"    {r['date']}  {sc.fmt(r['recovery'], 0):>3} {r['band'] or '':6}  SP {sl['sleep_performance']:5.1f}"
              f"  need {sl['need_min'] / 60:.1f}  slept {sl['asleep_min'] / 60:.1f}"
              f"  HRV {r['hrv_ms']} ({r['hrv_source']})  RHR {r['rhr']}{'  ' + r['flags'] if r['flags'] else ''}")


if __name__ == "__main__":
    h, m = sc.connect()
    compute(h, m, retune="--retune" in sys.argv)
