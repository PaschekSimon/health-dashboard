// Activity: steps, zone minutes, active minutes, floors and sitting time over
// the range, one day in detail (#/activity/<date>), weekly totals.

import { useMemo } from "react";
import { get, prefetch } from "../api/client";
import type { ActivityResponse, DayDetail } from "../api/types";
import { EventNote, Paged, RangeSeg } from "../components/bits";
import { Chart, ChartBox, Legend } from "../components/Chart";
import { Hint } from "../components/Hint";
import { STEP_GOAL, addDays, byKey, fClock, fDay, fHM, fShort, fWeek, iso, mean, mondayOf, n0, n1, niceTicksBy, pad2, pick, shades, today, xDates } from "../lib/format";
import { rangeDates, type Ui } from "../ui";

export async function loadActivity(args: string[], _q: URLSearchParams, ui: Ui) {
  const [fr, to] = rangeDates(ui);
  const day = args[0] || today();
  const [ac, dd] = await Promise.all([get<ActivityResponse>(`/activity?from=${fr}&to=${to}`), get<DayDetail>(`/day/${day}`)]);
  prefetch(`/day/${addDays(day, -1)}`, ...(day < today() ? [`/day/${addDays(day, 1)}`] : []));
  return <Activity fr={fr} to={to} day={day} ac={ac} dd={dd} />;
}

interface Week { days: number; steps: number; azm: number; active: number; km: number; floors: number }

function Activity({ fr, to, day, ac, dd }: { fr: string; to: string; day: string; ac: ActivityResponse; dd: DayDetail }) {
  const go = (d: string) => { location.hash = `#/activity/${d}`; };
  // weekly totals
  const weeks: Record<string, Week> = {};
  for (const r of ac.days) {
    const w = weeks[mondayOf(r.date)] = weeks[mondayOf(r.date)] || { days: 0, steps: 0, azm: 0, active: 0, km: 0, floors: 0 };
    w.days++; w.steps += r.steps || 0; w.azm += r.azm || 0; w.active += r.active_min || 0; w.km += r.distance_km || 0; w.floors += r.floors || 0;
  }
  const wk = Object.entries(weeks).sort().reverse();
  const avg = mean(ac.days.filter(r => r.date < today()).map(r => r.steps));
  const dsum = dd.steps_by_hour.reduce((s, h) => s + h.steps, 0);
  const bpm = dd.hr_5min.map(p => p.bpm).filter(v => v != null);
  const hrNow = bpm.length ? `${n0(Math.min(...bpm))}–${n0(Math.max(...dd.hr_5min.map(p => p.max ?? p.bpm).filter(v => v != null)))} bpm` : "";

  // the range charts depend only on the range; the day charts only on the day
  const range = useMemo(() => {
    const map = byKey(ac.days), xs = xDates(fr, to, ac.days), sh = shades(ac.annotations);
    const base = { x: xs, xfmt: fShort, tipx: fDay, shades: sh };
    return {
      xs,
      steps: { ...base, height: 190, refs: [{ y: STEP_GOAL, label: "8,000" }], selected: xs.indexOf(day) >= 0 ? xs.indexOf(day) : null,
        series: [{ name: "Steps", type: "bar" as const, data: pick(xs, map, r => r.steps), fmt: n0 }], onClick: (i: number) => go(xs[i]) },
      azm: { ...base, height: 160, series: [
        { name: "Fat burn", type: "bar" as const, stack: "a", color: "var(--ink-3)", data: pick(xs, map, r => r.azm_fat_burn), fmt: n0 },
        { name: "Cardio ×2", type: "bar" as const, stack: "a", color: "var(--ink-2)", data: pick(xs, map, r => r.azm_cardio != null ? r.azm_cardio * 2 : null), fmt: n0 },
        { name: "Peak ×2", type: "bar" as const, stack: "a", color: "var(--ink)", data: pick(xs, map, r => r.azm_peak != null ? r.azm_peak * 2 : null), fmt: n0 }] },
      act: { ...base, height: 160, series: [
        { name: "Light", type: "bar" as const, stack: "a", color: "var(--ink-3)", data: pick(xs, map, r => r.active_light), fmt: n0 },
        { name: "Moderate", type: "bar" as const, stack: "a", color: "var(--ink-2)", data: pick(xs, map, r => r.active_moderate), fmt: n0 },
        { name: "Vigorous", type: "bar" as const, stack: "a", color: "var(--ink)", data: pick(xs, map, r => r.active_vigorous), fmt: n0 }] },
      floors: { ...base, height: 140, series: [{ name: "Floors", type: "bar" as const, data: pick(xs, map, r => r.floors), fmt: n0 }] },
      sed: { ...base, height: 140, series: [{ name: "Sitting", type: "bar" as const, color: "var(--ink-2)",
        data: pick(xs, map, r => r.sedentary_min != null ? r.sedentary_min / 60 : null), fmt: (v: number | null) => fHM(v != null ? v * 60 : null) }] },
    };
  }, [ac, fr, to, day]);   // eslint-disable-line react-hooks/exhaustive-deps

  const dayCharts = useMemo(() => {
    const mins = (t: string) => { const [h, m] = t.slice(11, 16).split(":"); return +h * 60 + +m; };
    const sleepSh: { from: number; to: number; label: string }[] = [];
    if (dd.sleep?.start_time && dd.sleep.end_time) {
      const a = new Date(dd.sleep.start_time), b = new Date(dd.sleep.end_time);
      const from = iso(a) < day ? 0 : a.getHours() * 60 + a.getMinutes();
      sleepSh.push({ from, to: b.getHours() * 60 + b.getMinutes(), label: "sleep" });
    }
    return {
      hr: { x: dd.hr_5min.map(p => mins(p.t)), numeric: true, xmin: 0, xmax: 1440, xticks: niceTicksBy(0, 1440, 120), gap: 20, height: 260, shades: sleepSh,
        xfmt: (v: number) => pad2(Math.floor(v / 60)) + ":" + pad2(v % 60), series: [
          { name: "bpm", type: "line" as const, width: 1.2, data: dd.hr_5min.map(p => p.bpm), fmt: n0 },
          { name: "max", type: "line" as const, width: 0.8, color: "var(--ink-3)", data: dd.hr_5min.map(p => p.max), fmt: n0 }] },
      hours: { x: dd.steps_by_hour.map(h => h.hour), height: 170, xfmt: (h: number) => pad2(h), tipx: (h: number) => `${pad2(h)}:00–${pad2(h + 1)}:00`,
        series: [{ name: "Steps", type: "bar" as const, data: dd.steps_by_hour.map(h => h.steps), fmt: n0 }] },
    };
  }, [dd, day]);

  return (
    <>
      <div className="head"><h1>Activity</h1><span className="spacer" /><RangeSeg /></div>
      <div className="card"><div className="chart-title"><h3>Steps</h3><span className="note">avg {n0(avg)} / day · goal {n0(STEP_GOAL)} · click a day for detail</span><EventNote anns={ac.annotations} /></div>
        <Chart id="c-steps" cfg={range.steps} /></div>
      <section><div className="grid g2">
        <div className="card"><div className="chart-title"><h3><Hint k="azm">Active Zone Minutes</Hint></h3></div>
          <Legend items={[["Fat burn", "var(--ink-3)"], ["Cardio", "var(--ink-2)"], ["Peak", "var(--ink)"]]} /><Chart id="c-azm" cfg={range.azm} /></div>
        <div className="card"><div className="chart-title"><h3><Hint k="active_min">Active minutes</Hint></h3><span className="note">watch</span></div>
          <Legend items={[["Light", "var(--ink-3)"], ["Moderate", "var(--ink-2)"], ["Vigorous", "var(--ink)"]]} /><Chart id="c-act" cfg={range.act} /></div>
        <ChartBox id="c-floors" title="Floors" cfg={range.floors} />
        <ChartBox id="c-sed" title={<Hint k="sedentary">Sitting time</Hint>} now="" sub="sedentary periods, hours" cfg={range.sed} />
      </div></section>
      <section><h2>{fDay(day)}</h2>
        <div className="head" style={{ marginBottom: 10 }}>
          <div className="datenav">
            <button className="btn small" id="prev" onClick={() => go(addDays(day, -1))}>‹</button>
            <input type="date" id="day" value={day} max={today()} onChange={e => e.target.value && go(e.target.value)} />
            <button className="btn small" id="next" disabled={day >= today()} onClick={() => go(addDays(day, 1))}>›</button>
          </div>
          <span className="note">{n0(dsum)} steps{dd.sleep ? ` · slept ${fClock(dd.sleep.start_time)}–${fClock(dd.sleep.end_time)}` : ""}
            {dd.workouts.length ? <> · {dd.workouts.map((w, i) => <span key={w.id}>{i ? ", " : ""}<a href={`#/training/w/${w.id}`}>{w.name}</a></span>)}</> : null}</span>
        </div>
        <div className="grid">
          <ChartBox id="c-hr" title="Heart rate" now={hrNow} sub="5-minute averages; thin line: highest in each 5 minutes; shaded: sleep" cfg={dayCharts.hr} />
          <ChartBox id="c-hours" title="Steps per hour" cfg={dayCharts.hours} />
        </div>
      </section>
      <section><h2>Weeks</h2>
        <Paged head={<tr><th>Week</th><th className="num">Steps / day</th><th className="num"><Hint k="azm">AZM</Hint></th><th className="num"><Hint k="active_min">Active min</Hint></th><th className="num">km</th><th className="num">Floors</th></tr>}
          rows={wk.map(([m, w]) => (
            <tr key={m}><td>{fWeek(m)}</td><td className="num">{n0(w.steps / w.days)}</td><td className="num">{n0(w.azm)}</td><td className="num">{n0(w.active)}</td><td className="num">{n1(w.km)}</td><td className="num">{n0(w.floors)}</td></tr>
          ))} />
      </section>
    </>
  );
}
