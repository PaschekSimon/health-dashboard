"""Healthspan age and Pace of Aging, WHOOP-style. Spec section 4.

Each metric's all-cause mortality hazard ratio vs a guideline referent is
turned into years with the Gompertz rule years = 10 ln(HR) and summed onto the
chronological age. Overlapping pairs (VO2 max + RHR, steps + moderate minutes)
are multiplied by 0.7. Lean body mass is left out (no data).

Variants: "all" (all available data, WHOOP uses 6 months), "excl_illness"
(illness annotations dropped) and "last30" (last 30 days). Pace of Aging =
(0.5 + (age_last30 - age_all)) / 0.5, clamped to -1.0x .. 3.0x.

    python -I healthspan.py
Writes score_healthspan in health.db (one row per run date, variant and metric).
"""

import math
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # -I drops the script dir
import baseline as bl
import scores_common as sc
from recovery import asleep_set, sri

START = bl.ANALYSIS_START       # first full watch days skipped
OVERLAP = 0.7
YEARS_PER_VO2 = 10 * math.log(0.87) / 3.5     # Kodama 2009: HR 0.87 per MET
YEARS_PER_BPM = 10 * math.log(1.09) / 10      # Zhang 2016: HR 1.09 per +10 bpm

# referents (spec section 4, step 2). VO2 max: FRIEND treadmill norms, men
# 20-29, 50th percentile (WHOOP's "> 44" is for a 30-year-old).
REF = {"vo2max": 48.0, "rhr": 60.0, "steps": 8000, "moderate": 150, "vigorous": 75,
       "strength": 40, "sleep_h": (7.0, 9.0), "sri": 72.0}
VO2_CAP = 60.0                 # ~elite level for men 20-29: no extra years beyond
RHR_FLOOR = 45.0               # Zhang: linear from 45 bpm


def interp_lnhr(x, pts):
    """ln(HR) by linear interpolation of ln(HR) between (x, HR) points; flat
    outside the first and last point."""
    if x <= pts[0][0]:
        return math.log(pts[0][1])
    for (x0, h0), (x1, h1) in zip(pts, pts[1:]):
        if x <= x1:
            return math.log(h0) + (x - x0) / (x1 - x0) * (math.log(h1) - math.log(h0))
    return math.log(pts[-1][1])


# Paluch 2022 (vs ~3,550 steps/day), plateau at 10,000 (under 60)
STEPS_PTS = [(3550, 1.0), (5800, 0.60), (7850, 0.55), (10000, 0.4935)]
# Lee 2022: moderate 150-299 min/wk ~0.79, 300+ ~0.75 (plateau); vigorous 75-149
# min/wk 0.81, 150-300 ~0.78 (plateau)
MOD_PTS = [(0, 1.0), (150, 0.79), (300, 0.75)]
VIG_PTS = [(0, 1.0), (75, 0.81), (150, 0.78)]
# Momma 2022: max benefit (~HR 0.85) at 30-60 min/wk
STR_PTS = [(0, 1.0), (40, 0.86), (60, 0.85)]


def years_for(metric, v):
    if v is None:
        return None
    if metric == "vo2max":
        return YEARS_PER_VO2 * (min(v, VO2_CAP) - REF["vo2max"])
    if metric == "rhr":
        return YEARS_PER_BPM * (max(v, RHR_FLOOR) - REF["rhr"])
    if metric == "steps":
        return 10 * (interp_lnhr(v, STEPS_PTS) - interp_lnhr(REF["steps"], STEPS_PTS))
    if metric == "moderate":
        return 10 * (interp_lnhr(v, MOD_PTS) - interp_lnhr(REF["moderate"], MOD_PTS))
    if metric == "vigorous":
        return 10 * (interp_lnhr(v, VIG_PTS) - interp_lnhr(REF["vigorous"], VIG_PTS))
    if metric == "strength":
        return 10 * (interp_lnhr(v, STR_PTS) - interp_lnhr(REF["strength"], STR_PTS))
    if metric == "sleep_h":  # Cappuccio 2010: short 1.12, long 1.30
        return 10 * math.log(1.12) if v < 7 else 10 * math.log(1.30) if v > 9 else 0.0
    if metric == "sri":      # Windred 2024: least regular fifth ~HR 1/0.70
        return 10 * math.log(1 / 0.70) if v < REF["sri"] else 0.0


UNITS = {"vo2max": "ml/kg/min", "rhr": "bpm", "steps": "steps/day", "moderate": "min/week",
         "vigorous": "min/week", "strength": "min/week", "sleep_h": "h/night", "sri": "-100..100"}


def inputs(health, mcp, d0, d1, drop_illness):
    """Metric averages over local days d0..d1 (nights d0+1..d1+1)."""
    def keep(d):
        return d0 <= d <= d1 and not (drop_illness and bl.is_ill(d))

    days = [d0 + timedelta(i) for i in range((d1 - d0).days + 1)]
    days = [d for d in days if keep(d)]
    weeks = len(days) / 7

    vo2 = [v for d, v in mcp.execute("SELECT date, vo2_max FROM cardio_fitness WHERE vo2_max IS NOT NULL")
           if keep(date.fromisoformat(d))]
    rhr = [v for d, v in mcp.execute("SELECT date, resting_hr FROM heart_rate WHERE resting_hr IS NOT NULL")
           if keep(date.fromisoformat(d))]
    sc.cache(health)
    worn = {date.fromisoformat(d) for d, n in sc.score_cache.hr_sample_counts(health).items()
            if n >= 30000}  # >= ~17 h of HR samples
    steps = sc.score_cache.watch_steps_by_day(health)
    st = [steps.get(d, 0) for d in days if d in worn]
    azm = {date.fromisoformat(d): (f or 0, (c or 0) + (p or 0)) for d, f, c, p in
           mcp.execute("SELECT date, fat_burn_minutes, cardio_minutes, peak_minutes FROM azm")}
    mod = sum(azm.get(d, (0, 0))[0] for d in days) / weeks
    vig = sum(azm.get(d, (0, 0))[1] for d in days) / weeks
    strength = sum((w["t_end"] - w["t_start"]) / 60 for w in bl.watch_workouts(mcp)
                   if w["log_type"] == "STRENGTH_TRAINING" and keep(w["day"])) / weeks
    sleep = [a / 60 for d, a in health.execute("SELECT date, asleep_min FROM score_sleep_daily")
             if keep(date.fromisoformat(d) - timedelta(1))]

    # SRI over all consecutive day pairs inside the window (noon-to-noon days),
    # on Phillips' -100..100 scale (2P - 1) so it matches the UK Biobank
    # quintiles in Windred 2024 (median 81)
    sessions = bl.sleep_sessions(mcp)
    asleep = asleep_set(sessions)
    off = sessions[-1]["offset"] if sessions else 0
    vals = []
    for d in days:
        if keep(d + timedelta(1)):
            end = sc.local_midnight_utc(d + timedelta(2), off) + 12 * 3600
            vals.append(2 * sri(asleep, end, 2) - 100)
    return {"vo2max": statistics.mean(vo2) if vo2 else None,
            "rhr": statistics.mean(rhr) if rhr else None,
            "steps": statistics.mean(st) if st else None,
            "moderate": mod, "vigorous": vig, "strength": strength,
            "sleep_h": statistics.mean(sleep) if sleep else None,
            "sri": statistics.mean(vals) if vals else None,
            "_days": len(days)}


def evaluate(age, x):
    yrs = {k: years_for(k, x[k]) for k in UNITS}
    adj = dict(yrs)
    for a, b in (("vo2max", "rhr"), ("steps", "moderate")):
        if yrs[a] is not None and yrs[b] is not None:
            adj[a], adj[b] = OVERLAP * yrs[a], OVERLAP * yrs[b]
    total = sum(v for v in adj.values() if v is not None)
    return yrs, adj, age + total


def compute(health, mcp, verbose=True):
    age, src = sc.age_years(mcp)
    last = sc.today_local() - timedelta(1)          # last complete day
    variants = {"all": (START, last, False), "excl_illness": (START, last, True),
                "last30": (last - timedelta(29), last, False),
                "last30_excl_illness": (last - timedelta(29), last, True)}
    res = {}
    for name, (d0, d1, drop) in variants.items():
        x = inputs(health, mcp, d0, d1, drop)
        yrs, adj, hage = evaluate(age, x)
        res[name] = {"x": x, "yrs": yrs, "adj": adj, "age": hage, "window": (d0, d1)}

    def pace(base, recent):
        return max(-1.0, min(3.0, (0.5 + (res[recent]["age"] - res[base]["age"])) / 0.5))

    paces = {"all": pace("all", "last30"), "excl_illness": pace("excl_illness", "last30_excl_illness")}
    today = sc.today_local().isoformat()
    rows = []
    for name, r in res.items():
        for k in UNITS:
            rows.append({"computed_on": today, "variant": name, "metric": k, "value": r["x"][k],
                         "unit": UNITS[k], "referent": str(REF[k]), "years": r["yrs"][k],
                         "years_adjusted": r["adj"][k]})
        rows.append({"computed_on": today, "variant": name, "metric": "healthspan_age", "value": r["age"],
                     "unit": "years", "referent": str(age), "years": r["age"] - age,
                     "years_adjusted": r["age"] - age,
                     "note": f"{r['window'][0]}..{r['window'][1]}, {r['x']['_days']} days"})
        if name in paces:
            rows.append({"computed_on": today, "variant": name, "metric": "pace_of_aging", "value": paces[name],
                         "unit": "x", "note": "6-month projection from the last 30 days"})
    sc.write_rows(health, "score_healthspan",
                  ["computed_on", "variant", "metric", "value", "unit", "referent", "years", "years_adjusted", "note"],
                  ["computed_on", "variant", "metric"], rows)
    if verbose:
        report(age, src, res, paces)
    return res, paces, age


def report(age, src, res, paces):
    print(f"HEALTHSPAN  chronological age {age:.0f} ({src})")
    for name in ("all", "excl_illness"):
        r = res[name]
        print(f"  {name:13s} ({r['window'][0]}..{r['window'][1]}, {r['x']['_days']} days): "
              f"Healthspan age {r['age']:.1f} ({r['age'] - age:+.1f} y), Pace of Aging {paces[name]:.1f}x"
              f" (last-30-day age {res['last30' if name == 'all' else 'last30_excl_illness']['age']:.1f})")
    print(f"  {'metric':10s} {'value':>8s} {'referent':>10s} {'years':>6s} {'adj.':>6s}   (all data | excl. illness adj.)")
    for k in UNITS:
        a, e = res["all"], res["excl_illness"]
        print(f"  {k:10s} {sc.fmt(a['x'][k]):>8s} {str(REF[k]):>10s} {a['yrs'][k]:+6.2f} {a['adj'][k]:+6.2f}"
              f"   | {sc.fmt(e['x'][k]):>7s} {e['adj'][k]:+6.2f}")


if __name__ == "__main__":
    h, m = sc.connect()
    compute(h, m)
