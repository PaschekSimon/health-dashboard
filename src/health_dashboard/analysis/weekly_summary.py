"""Weekly summary: training load, easy share, sleep regularity, n-of-1 effects.

Per ISO week (Mon-Sun):
  * TRIMP sum (Banister, all watch workouts), and at the end of the week
    fitness = 42-day EWMA of daily TRIMP, fatigue = 7-day EWMA,
    form = fitness - fatigue. Both start at 0 on the first watch day, so fitness is
    underestimated for the first weeks.
  * % easy time: share of workout time (walks left out) with HR below the
    first Google zone boundary (start of MODERATE), and below the start of
    VIGOROUS
  * Sleep Regularity Index: the probability that you are in the same state
    (asleep / awake) at two moments 24 h apart, minute by minute, from the
    sleep stages of all sleep sessions incl. naps. SRI = 200 * P - 100
    (100 = perfectly regular, 0 = random). Minutes without a sleep record
    count as awake.
Then n-of-1 effects on the next night (HRV, non-REM HR, resting HR, skin
temp): after alcohol, after hard days (daily TRIMP in the top quartile),
after late sessions (ending after 20:00) - each vs all other nights, as a
mean difference with a 95 % CI (Welch) and n. Illness/alcohol nights are left
out of the comparison groups except where they are the condition.

Results replace the table analysis_weekly in health.db. Run:
    python -m health_dashboard weekly-summary
"""

import math
import statistics
import time
from datetime import timedelta

from ..context import Context
from . import baseline as bl
from . import run_report as rr

LATE_HOUR = 20
OUTCOMES = {"ln_rmssd": "HRV (ln RMSSD)", "nrem_hr": "non-REM HR", "rhr": "resting HR", "skin_temp": "skin temp"}


def ewma(series, days):
    a = 1 - math.exp(-1 / days)
    out, v = {}, 0.0
    for d, x in series:
        v += a * (x - v)
        out[d] = v
    return out


def sleep_minutes(ctx):
    """Set of UTC minute indices spent asleep (any non-AWAKE stage, naps too),
    and the (first, last) minute covered by sleep records."""
    asleep, first, last = set(), None, None
    for s in bl.sleep_sessions(ctx):
        first = s["start"] // 60 if first is None else min(first, s["start"] // 60)
        last = s["end"] // 60 if last is None else max(last, s["end"] // 60)
        for a, b, kind in s["stages"]:
            if kind != "AWAKE":
                asleep.update(range(a // 60, b // 60))
    return asleep, first, last


def sri(asleep, m0, m1, data_end):
    """SRI and P over minutes m in [m0, m1) whose partner m + 1440 has data."""
    same = total = 0
    for m in range(m0, min(m1, data_end - 1440)):
        total += 1
        same += (m in asleep) == (m + 1440 in asleep)
    if total < 1440 * 3:  # need at least 3 days of pairs
        return None, None
    p = same / total
    return 200 * p - 100, p


def effect(ctx, nights, condition_nights, key, include_events=False):
    """Mean difference (condition - other nights) for one outcome."""
    a, b = [], []
    for d, v in nights.items():
        if d < ctx.analysis_start or key not in v:
            continue
        if d in condition_nights:
            if include_events or not ctx.is_excluded(d):
                a.append(v[key])
        elif not ctx.is_excluded(d):
            b.append(v[key])
    return bl.mean_diff_ci(a, b), len(a), len(b)


def run(ctx):
    health, mcp = ctx.health, ctx.mcp
    hr_max = bl.current_max_hr(health)
    workouts = bl.watch_workouts(ctx)
    today = ctx.today
    load_start = ctx.watch_start

    # daily TRIMP and easy time
    trimp, easy = {}, {}
    late_days = set()
    for w in workouts:
        t0, t1, pauses = rr.run_window(w)
        samples = bl.hr_series(ctx, t0, t1)
        trimp[w["day"]] = trimp.get(w["day"], 0) + bl.banister_trimp(samples, bl.resting_hr_for(mcp, w["day"]), hr_max)
        if (t1 + w["offset"]) % 86400 >= LATE_HOUR * 3600:
            late_days.add(w["day"])
        if w["log_type"] == "WALKING":
            continue  # walks aren't training; they would inflate the easy share
        zones = bl.hr_zones(health, w["day"])
        tot, below1, belowv = easy.get(w["day"], (0, 0, 0))
        for _t, b, d in rr.durations(samples, t1):
            tot += d
            below1 += d * (b < zones["MODERATE"][0])
            belowv += d * (b < zones["VIGOROUS"][0])
        easy[w["day"]] = (tot, below1, belowv)

    days = [load_start + timedelta(days=i) for i in range((today - load_start).days + 1)]
    series = [(d, trimp.get(d, 0.0)) for d in days]
    fitness, fatigue = ewma(series, 42), ewma(series, 7)

    asleep, _, sleep_end = sleep_minutes(ctx)
    nights = bl.nightly_metrics(ctx)

    weeks = []
    monday = ctx.analysis_start - timedelta(days=ctx.analysis_start.weekday())
    while monday <= today:
        week = [monday + timedelta(days=i) for i in range(7)]
        last = min(week[-1], today)
        tot = sum(easy.get(d, (0, 0, 0))[0] for d in week)
        m0 = int(time.mktime(monday.timetuple())) // 60
        s, p = sri(asleep, m0, m0 + 7 * 1440, sleep_end)
        weeks.append({
            "week": f"{monday.isocalendar()[0]}-W{monday.isocalendar()[1]:02d}",
            "monday": monday.isoformat(),
            "workouts": sum(1 for w in workouts if w["day"] in week),
            "trimp": sum(trimp.get(d, 0) for d in week),
            "fitness": fitness[last], "fatigue": fatigue[last], "form": fitness[last] - fatigue[last],
            "pct_below_first": 100 * sum(easy.get(d, (0, 0, 0))[1] for d in week) / tot if tot else None,
            "pct_below_vigorous": 100 * sum(easy.get(d, (0, 0, 0))[2] for d in week) / tot if tot else None,
            "sri": s, "sri_p_same": p,
            "partial": int(last < week[-1]),
        })
        monday += timedelta(days=7)

    cols = list(weeks[0])
    health.execute("DROP TABLE IF EXISTS analysis_weekly")
    health.execute(f"CREATE TABLE analysis_weekly ({', '.join(c + (' TEXT PRIMARY KEY' if c == 'week' else '') for c in cols)})")
    health.executemany(f"INSERT INTO analysis_weekly VALUES ({', '.join('?' * len(cols))})",
                       [tuple(r[c] for c in cols) for r in weeks])
    health.commit()

    # n-of-1: the night after day D is keyed D + 1
    load_days = [d for d in days if ctx.analysis_start <= d < today]
    q3 = statistics.quantiles([trimp.get(d, 0) for d in load_days], n=4)[2]
    hard = {d + timedelta(days=1) for d in load_days if trimp.get(d, 0) >= q3 and trimp.get(d, 0) > 0}
    late = {d + timedelta(days=1) for d in late_days}
    conditions = [("after alcohol", ctx.exclusions.alcohol, True),
                  (f"after hard day (TRIMP >= {q3:.0f})", hard, False),
                  (f"after late session (ends after {LATE_HOUR}:00)", late, False)]
    effects = []
    for label, cond, incl in conditions:
        for key in OUTCOMES:
            (diff, lo, hi), na, nb = effect(ctx, nights, cond, key, incl)
            effects.append((label, key, diff, lo, hi, na, nb))
    return weeks, effects, late_days


def f(v, spec):
    return format(v, spec) if v is not None else "-"


def main(argv=None):
    with Context.open() as ctx:
        weeks, effects, late_days = run(ctx)
    print("week      workouts  TRIMP  fitness fatigue  form  easy<1st easy<vig   SRI  P(same)")
    for w in weeks:
        print(f"{w['week']}  {w['workouts']:>5} {w['trimp']:7.0f} {w['fitness']:8.1f} {w['fatigue']:7.1f} "
              f"{w['form']:6.1f} {f(w['pct_below_first'], '7.0f'):>7}% {f(w['pct_below_vigorous'], '7.0f'):>7}% "
              f"{f(w['sri'], '6.0f'):>6} {f(w['sri_p_same'], '7.2f'):>7}{'  (so far)' if w['partial'] else ''}")
    print("\nNext-night effects (condition minus other nights, 95 % CI, n condition / n other):")
    for label, key, diff, lo, hi, na, nb in effects:
        ci = f"[{lo:+.2f}, {hi:+.2f}]" if lo is not None else "no CI (n < 2)"
        print(f"  {label:38} {OUTCOMES[key]:15} {f(diff, '+6.2f'):>6}  {ci:18} n={na}/{nb}")
    print(f"\nLate sessions found on: {', '.join(sorted(d.isoformat() for d in late_days)) or 'none'}"
          " (illness nights are excluded from that comparison).")


if __name__ == "__main__":
    main()
