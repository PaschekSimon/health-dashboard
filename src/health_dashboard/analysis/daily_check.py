"""Daily readiness check: how far each night is from your own baseline.

For every night (keyed by wake date) it computes
  * a robust z-score per signal against the previous 28-42 eligible nights
    (see baseline.py); positive = worse than usual
  * a flag: AMBER if one physiological signal has z > 2 or two have z > 1.5,
    RED if the night before was AMBER too. Physiological signals are resting
    HR, HRV, non-REM HR, breathing rate and skin temperature; sleep duration
    and steps are shown for context but don't trigger flags.
  * HRV trend: 7-night rolling mean of ln RMSSD against a band of
    mean +/- 0.5 SD of the previous 30-60 eligible nights, and the 7-night
    coefficient of variation of ln RMSSD (only if >= 5 of 7 nights exist).

Results replace the table analysis_daily in health.db; the last 7 nights are
printed. Run:
    python -m health_dashboard daily-check [--days N]
"""

import argparse
import statistics
from datetime import timedelta

from ..context import Context
from . import baseline as bl

FLAG_SIGNALS = ["rhr", "ln_rmssd", "nrem_hr", "breathing", "skin_temp"]
LABELS = {"rhr": "RHR", "ln_rmssd": "HRV", "nrem_hr": "nREM", "breathing": "BR",
          "skin_temp": "Temp", "sleep_min": "Sleep", "steps": "Steps"}
HRV_BAND_MIN, HRV_BAND_MAX = 30, 60


def flag_for(z):
    zs = [z[k] for k in FLAG_SIGNALS if z.get(k) is not None]
    if any(v > 2 for v in zs) or sum(v > 1.5 for v in zs) >= 2:
        return "AMBER"
    return "OK"


def hrv_trend(ctx, nights, night):
    """(rolling 7-night mean, band low, band high, CV %) of ln RMSSD."""
    week = [nights[d]["ln_rmssd"] for d in (night - timedelta(days=i) for i in range(7))
            if d in nights and "ln_rmssd" in nights[d]]
    mean7 = statistics.mean(week) if len(week) >= 5 else None
    cv7 = 100 * statistics.stdev(week) / statistics.mean(week) if len(week) >= 5 else None

    history = []
    for d in sorted(nights, reverse=True):
        if d >= night or d < ctx.analysis_start or ctx.is_excluded(d) or "ln_rmssd" not in nights[d]:
            continue
        history.append(nights[d]["ln_rmssd"])
        if len(history) == HRV_BAND_MAX:
            break
    lo = hi = None
    if len(history) >= HRV_BAND_MIN:
        m, sd = statistics.mean(history), statistics.stdev(history)
        lo, hi = m - 0.5 * sd, m + 0.5 * sd
    return mean7, lo, hi, cv7


def run(ctx):
    health = ctx.health
    nights = bl.nightly_metrics(ctx)
    rows = []
    prev_flag = None
    for night in nights:
        if night < ctx.analysis_start:
            continue
        z, n_base = {}, 0
        for key in bl.SIGNALS:
            z[key], _, n = bl.robust_z(ctx, nights, night, key)
            n_base = max(n_base, n)
        flag = flag_for(z)
        if flag == "AMBER" and prev_flag in ("AMBER", "RED"):
            flag = "RED"
        if all(z[k] is None for k in FLAG_SIGNALS):
            flag = None  # no baseline yet
        prev_flag = flag
        mean7, lo, hi, cv7 = hrv_trend(ctx, nights, night)
        status = None
        if mean7 is not None and lo is not None:
            status = "below" if mean7 < lo else "above" if mean7 > hi else "within"
        rows.append({"night": night.isoformat(), **{f"z_{k}": z[k] for k in bl.SIGNALS},
                     "flag": flag, "baseline_n": n_base, "provisional": int(n_base < bl.BASELINE_MIN_NIGHTS),
                     "excluded_event": int(ctx.is_excluded(night)),
                     "hrv_ln": nights[night].get("ln_rmssd"), "hrv7_mean": mean7, "hrv_band_lo": lo,
                     "hrv_band_hi": hi, "hrv_status": status, "hrv7_cv": cv7})

    cols = list(rows[0])
    health.execute("DROP TABLE IF EXISTS analysis_daily")
    health.execute(f"CREATE TABLE analysis_daily ({', '.join(c + (' TEXT PRIMARY KEY' if c == 'night' else '') for c in cols)})")
    health.executemany(f"INSERT INTO analysis_daily VALUES ({', '.join('?' * len(cols))})",
                       [tuple(r[c] for c in cols) for r in rows])
    health.commit()
    return rows


def fmt(v, spec="5.1f"):
    return format(v, spec) if v is not None else "  -  "


def print_rows(rows):
    head = "night       " + " ".join(f"{LABELS[k]:>5}" for k in bl.SIGNALS) + \
           "  flag   HRV7  band          status  CV%  base"
    print(head)
    for r in rows:
        band = f"{fmt(r['hrv_band_lo'], '.2f')}-{fmt(r['hrv_band_hi'], '.2f')}" if r["hrv_band_lo"] else "     -     "
        note = " provisional" if r["provisional"] else ""
        note += " (event night)" if r["excluded_event"] else ""
        print(f"{r['night']}  " + " ".join(fmt(r[f'z_{k}']) for k in bl.SIGNALS) +
              f"  {(r['flag'] or '-'):5}  {fmt(r['hrv7_mean'], '.2f'):>5}  {band:12}  {(r['hrv_status'] or '-'):6}"
              f"  {fmt(r['hrv7_cv'], '4.1f')}  {r['baseline_n']:>3}{note}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7, help="how many recent nights to print (default 7)")
    ap.add_argument("--from", dest="start", help="print from this wake date instead, e.g. 2026-09-25")
    args = ap.parse_args(argv)
    with Context.open() as ctx:
        rows = run(ctx)
    shown = [r for r in rows if r["night"] >= args.start] if args.start else rows[-args.days:]
    print("z-scores: positive = worse than your baseline. AMBER: one signal > 2 or two > 1.5; "
          "RED: AMBER two nights running.")
    print_rows(shown)


if __name__ == "__main__":
    main()
