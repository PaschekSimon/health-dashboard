"""Per-workout report: HR zones, efficiency, decoupling, HR recovery, TRIMP.

For every watch running workout:
  * minutes in each Google HR zone (that day's limits from daily_hr_zones)
    and % of time below the first zone boundary (start of MODERATE)
  * efficiency factor EF = speed (m/min) / avg HR over moving time, for
    steady runs only (km splits within 8 % of each other, stops adding up to
    at most a minute, not a custom/interval workout); pauses are left out of
    both time and HR, so a short stop doesn't bias it
  * aerobic decoupling = (EF first half - EF second half) / EF first half,
    on steady runs and < 10 m climb per km; the first 5 min
    are left out so the HR lag at the start doesn't bias the first half
  * HRR60 / HRR120 = HR at the end of the run minus HR 60 / 120 s later,
    marked unreliable if there is no HR sample within 5 s of either point
And Banister TRIMP and minutes per HR zone for every workout (running,
football, table tennis, strength, walks, ...).

Speed and distance come from the per-second running samples of the Takeout
(takeout_run_samples), else from the GPS track (TCX distance, scaled to the
workout's total); runs with neither fall back to whole-run distance.
Results replace the table analysis_workouts in health.db. Run:
    python -m health_dashboard run-report
"""

import bisect
import json
import statistics
import xml.etree.ElementTree as ET

from ..context import Context
from . import baseline as bl

WARMUP_S = 300
STEADY_SPLIT_CV = 8.0       # % coefficient of variation of km split paces
MAX_CLIMB_PER_KM = 10.0     # m
MAX_STOP_S = 10             # one interior pause at least this long = "a stop"
MAX_STOPS_TOTAL_S = 60      # stops adding up to more than this make a run unsteady
SAMPLE_GAP = 10             # an HR sample counts for at most 10 s


def run_window(w):
    """(start, end, interior pauses [(from, to)]) from the workout events. A
    PAUSE right before STOP marks the real end (time after it is standing)."""
    events = [(e["exerciseEventType"], bl.parse_time(e["eventTime"]))
              for e in json.loads(w["exercise_events"] or "[]")]
    end = w["t_end"]
    if events and events[-1][0] == "STOP":
        end = events[-1][1]
        if len(events) >= 2 and events[-2][0] == "PAUSE":
            end = events[-2][1]
    pauses, paused_at = [], None
    for kind, t in events:
        if kind in ("PAUSE", "AUTO_PAUSE") and t < end:
            paused_at = t
        elif kind in ("RESUME", "AUTO_RESUME") and paused_at is not None:
            pauses.append((paused_at, t))
            paused_at = None
    return w["t_start"], end, pauses


def moving(t, pauses):
    return not any(a <= t < b for a, b in pauses)


def durations(samples, end):
    """[(t, bpm, seconds this sample stands for)]"""
    out = []
    for i, (t, b) in enumerate(samples):
        nxt = samples[i + 1][0] if i + 1 < len(samples) else end
        out.append((t, b, max(0, min(nxt - t, SAMPLE_GAP))))
    return out


def distance_track(ctx, w, t0, t1):
    """[(t, metres so far)] for a run: the Takeout's per-second distance, else
    the TCX track's distance scaled to the workout total (raw GPS runs a few
    % short). Empty if neither exists."""
    samples = ctx.health.execute("SELECT t, value/1000.0 FROM takeout_run_samples WHERE metric='pace_distance_mm' "
                                 "AND source=? AND t BETWEEN ? AND ? ORDER BY t", (ctx.watch, t0, t1)).fetchall()
    if samples:
        out, total = [(samples[0][0] - 1, 0.0)], 0.0
        for t, metres in samples:
            total += metres
            out.append((t, total))
        return out
    row = ctx.mcp.execute("SELECT tcx FROM exercise_routes WHERE log_id = ?", (w["log_id"],)).fetchone()
    if not row or not row[0]:
        return []
    try:
        root = ET.fromstring(row[0].encode("utf-8"))
    except ET.ParseError:
        return []
    out = []
    for tp in root.iter():
        if not tp.tag.endswith("Trackpoint"):
            continue
        t = d = None
        for e in tp:
            if e.tag.endswith("Time"):
                t = bl.parse_time(e.text)
            elif e.tag.endswith("DistanceMeters"):
                d = float(e.text)
        if t is not None and d is not None:
            out.append((t, d))
    if len(out) < 2 or not out[-1][1]:
        return []
    k = (w["distance_km"] or 0) * 1000 / out[-1][1]
    k = k if 0.85 < k < 1.15 else 1
    return [(t, d * k) for t, d in out]


def metres_between(track, t0, t1):
    """Distance covered in [t0, t1) along a distance track, linear between points."""
    ts = [t for t, _ in track]

    def at(t):
        i = bisect.bisect_left(ts, t)
        if i <= 0:
            return track[0][1]
        if i >= len(ts):
            return track[-1][1]
        (ta, da), (tb, db) = track[i - 1], track[i]
        return da + (db - da) * (t - ta) / ((tb - ta) or 1)
    return at(t1) - at(t0)


def ef(track, hr, t0, t1, pauses):
    """EF over [t0, t1): metres per moving minute / mean HR (time-weighted)."""
    metres = metres_between(track, t0, t1)
    paused = sum(max(0, min(b, t1) - max(a, t0)) for a, b in pauses)
    secs = sum(d for t, b, d in hr if t0 <= t < t1 and moving(t, pauses))
    beats = sum(b * d for t, b, d in hr if t0 <= t < t1 and moving(t, pauses))
    if not metres or not secs:
        return None
    return (metres / ((t1 - t0 - paused) / 60)) / (beats / secs)


def split_cv(ctx, w, t0, t1):
    """CV (%) of the km split paces. Splits are rebuilt from the per-second
    distance samples when they exist, else taken from the workout's splits
    (some runs have none)."""
    samples = ctx.health.execute("SELECT t, value/1000.0 FROM takeout_run_samples WHERE metric='pace_distance_mm' "
                                 "AND source=? AND t BETWEEN ? AND ? ORDER BY t", (ctx.watch, t0, t1)).fetchall()
    if samples:
        paces, dist, mark_t, mark_d = [], 0.0, t0, 0.0
        for t, metres in samples:
            dist += metres
            if dist - mark_d >= 1000:
                paces.append((t - mark_t) / (dist - mark_d))
                mark_t, mark_d = t, dist
        if dist - mark_d >= 500:
            paces.append((t1 - mark_t) / (dist - mark_d))
    else:
        paces = [s["metricsSummary"]["averagePaceSecondsPerMeter"] for s in json.loads(w["splits"] or "[]")
                 if s.get("metricsSummary", {}).get("distanceMillimeters", 0) >= 500_000]
    if len(paces) < 2:
        return None
    return 100 * statistics.stdev(paces) / statistics.mean(paces)


def hr_at(ctx, t):
    row = ctx.health.execute("SELECT bpm, abs(t-?) d FROM hr_samples WHERE source=? AND t BETWEEN ? AND ? "
                             "ORDER BY d LIMIT 1", (t, ctx.watch, t - 5, t + 5)).fetchone()
    return row[0] if row else None


def add_zone_minutes(row, hr, zones):
    """min_light .. min_peak: minutes of hr ((t, bpm, seconds) points) in each Google zone."""
    for z in ("LIGHT", "MODERATE", "VIGOROUS", "PEAK"):
        row[f"min_{z.lower()}"] = sum(d for _, b, d in hr if bl.zone_of(b, zones) == z) / 60


def analyse_run(ctx, w, row):
    t0, t1, pauses = run_window(w)
    zones = bl.hr_zones(ctx.health, w["day"])
    hr = durations(bl.hr_series(ctx, t0, t1), t1)
    hr = [(t, b, d) for t, b, d in hr if moving(t, pauses)]
    total = sum(d for _, _, d in hr) or 1
    add_zone_minutes(row, hr, zones)
    row["first_boundary"] = zones["MODERATE"][0]
    row["pct_below_first"] = 100 * sum(d for _, b, d in hr if b < zones["MODERATE"][0]) / total
    row["pct_below_vigorous"] = 100 * sum(d for _, b, d in hr if b < zones["VIGOROUS"][0]) / total
    row["avg_hr"] = sum(b * d for _, b, d in hr) / total

    track = distance_track(ctx, w, t0, t1)
    has_samples = bool(track)
    ms = json.loads(w["metrics_summary"] or "{}")
    km = (w["distance_km"] or 0)
    row["climb_m_per_km"] = ms.get("elevationGainMillimeters", 0) / 1000 / km if km else None
    row["stop_s"] = sum(b - a for a, b in pauses if b - a >= MAX_STOP_S)
    row["split_cv"] = split_cv(ctx, w, t0, t1)
    custom = "Benutzerdef" in (w["name"] or "")
    steady = row["split_cv"] is not None and row["split_cv"] <= STEADY_SPLIT_CV and not custom \
        and row["stop_s"] <= MAX_STOPS_TOTAL_S
    row["steady"] = int(steady)
    notes = []
    if custom:
        notes.append("custom/interval workout")
    if row["stop_s"]:
        notes.append(f"stopped {row['stop_s']} s" + (" (left out)" if row["stop_s"] <= MAX_STOPS_TOTAL_S else ""))
    if row["split_cv"] and row["split_cv"] > STEADY_SPLIT_CV:
        notes.append(f"uneven pace (CV {row['split_cv']:.0f} %)")

    hr_all = durations(bl.hr_series(ctx, t0, t1), t1)
    if steady and has_samples:
        row["ef"] = ef(track, hr_all, t0, t1, pauses)
    elif steady:
        moving_min = (t1 - t0 - sum(b - a for a, b in pauses)) / 60
        row["ef"] = km * 1000 / moving_min / row["avg_hr"]
        notes.append("EF from whole-run distance")
    if steady and has_samples and row["climb_m_per_km"] is not None and row["climb_m_per_km"] < MAX_CLIMB_PER_KM:
        mid = (t0 + WARMUP_S + t1) // 2
        e1, e2 = ef(track, hr_all, t0 + WARMUP_S, mid, pauses), ef(track, hr_all, mid, t1, pauses)
        if e1 and e2:
            row["decoupling_pct"] = 100 * (e1 - e2) / e1
    elif steady and row["climb_m_per_km"] is not None and row["climb_m_per_km"] >= MAX_CLIMB_PER_KM:
        notes.append(f"hilly ({row['climb_m_per_km']:.0f} m/km), no decoupling")

    h0, h60, h120 = hr_at(ctx, t1), hr_at(ctx, t1 + 60), hr_at(ctx, t1 + 120)
    row["hr_end"] = h0
    row["hrr60"] = h0 - h60 if h0 and h60 else None
    row["hrr120"] = h0 - h120 if h0 and h120 else None
    row["hrr_reliable"] = int(None not in (h0, h60, h120))
    if not row["hrr_reliable"]:
        notes.append("HRR unreliable: HR missing after stop")
    row["notes"] = "; ".join(notes)


def run(ctx):
    health, mcp = ctx.health, ctx.mcp
    hr_max = bl.current_max_hr(health)
    rows = []
    for w in bl.watch_workouts(ctx):
        t0, t1, _ = run_window(w)
        local_end = (t1 + w["offset"]) % 86400 / 3600
        row = {"log_id": w["log_id"], "date": w["date"], "type": w["log_type"], "name": w["name"],
               "minutes": (t1 - t0) / 60, "km": w["distance_km"], "end_local_h": local_end,
               "rest_hr": bl.resting_hr_for(mcp, w["day"]), "max_hr": hr_max,
               "trimp": bl.banister_trimp(bl.hr_series(ctx, t0, t1), bl.resting_hr_for(mcp, w["day"]), hr_max)}
        for k in ("min_light", "min_moderate", "min_vigorous", "min_peak", "first_boundary", "pct_below_first",
                  "pct_below_vigorous", "avg_hr", "climb_m_per_km", "stop_s", "split_cv", "steady", "ef",
                  "decoupling_pct", "hr_end", "hrr60", "hrr120", "hrr_reliable", "notes"):
            row[k] = None
        if w["log_type"] == "RUNNING":
            analyse_run(ctx, w, row)
        else:
            hr = durations(bl.hr_series(ctx, t0, t1), t1)
            total = sum(d for _, _, d in hr) or 1
            row["avg_hr"] = sum(b * d for _, b, d in hr) / total
            add_zone_minutes(row, hr, bl.hr_zones(health, w["day"]))
        rows.append(row)

    cols = list(rows[0])
    health.execute("DROP TABLE IF EXISTS analysis_workouts")
    health.execute(f"CREATE TABLE analysis_workouts ({', '.join(c + (' TEXT PRIMARY KEY' if c == 'log_id' else '') for c in cols)})")
    health.executemany(f"INSERT INTO analysis_workouts VALUES ({', '.join('?' * len(cols))})",
                       [tuple(r[c] for c in cols) for r in rows])
    health.commit()
    return rows, hr_max


def f(v, spec):
    return format(v, spec) if v is not None else "-"


def main(argv=None):
    with Context.open() as ctx:
        rows, hr_max = run(ctx)
    runs = [r for r in rows if r["type"] == "RUNNING"]
    print(f"Max HR {hr_max} (from Google's zones). Zone minutes: L=light M=moderate V=vigorous P=peak.")
    print(f"{'date':10} {'km':>4} {'min':>4} {'avgHR':>5} {'L':>4} {'M':>4} {'V':>4} {'P':>4} "
          f"{'<1st':>5} {'1st':>4} {'EF':>5} {'dec%':>5} {'HRR60':>5} {'HRR120':>6} {'TRIMP':>5}  notes")
    for r in runs:
        print(f"{r['date']:10} {f(r['km'], '4.1f'):>4} {r['minutes']:4.0f} {r['avg_hr']:5.0f} "
              f"{r['min_light']:4.0f} {r['min_moderate']:4.0f} {r['min_vigorous']:4.0f} {r['min_peak']:4.0f} "
              f"{r['pct_below_first']:4.0f}% {r['first_boundary']:4} {f(r['ef'], '5.2f'):>5} "
              f"{f(r['decoupling_pct'], '5.1f'):>5} {f(r['hrr60'], '5.0f'):>5} {f(r['hrr120'], '6.0f'):>6} "
              f"{r['trimp']:5.0f}  {r['notes']}")
    print("\nTRIMP, all workouts")
    print(f"{'date':10} {'type':18} {'min':>4} {'avgHR':>5} {'TRIMP':>6}  ends")
    for r in rows:
        h = r["end_local_h"]
        print(f"{r['date']:10} {r['type']:18} {r['minutes']:4.0f} {f(r['avg_hr'], '5.0f'):>5} {r['trimp']:6.0f}"
              f"  {int(h):02d}:{int(h % 1 * 60):02d}")


if __name__ == "__main__":
    main()
