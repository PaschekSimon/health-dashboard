// Training: daily Strain, every workout, weekly load, personal records.
// #/training/w/<id> opens one workout.

import { useMemo, useRef, useLayoutEffect, useState } from "react";
import { get } from "../api/client";
import type { RunDetail, RunsResponse, TrainingResponse, WorkoutSeries } from "../api/types";
import { EventNote, Kv, Paged, RangeSeg, Table } from "../components/bits";
import { Chart, ChartBox, Legend, Route } from "../components/Chart";
import { Hint } from "../components/Hint";
import { PACED, byKey, fClock, fDay, fDur, fHM, fPace, fShort, fWeek, mean, n0, n1, n2, niceTicksBy, pad2, pct, pick, sgn, shades, xDates } from "../lib/format";
import { rangeDates, useUi, type Ui } from "../ui";

const TYPE: Record<string, string> = { RUNNING: "Run", WALKING: "Walk", STRENGTH_TRAINING: "Strength", TABLE_TENNIS: "Table tennis",
  SWIMMING: "Swim", BIKING: "Ride", HIKING: "Hike" };
const typeName = (t: string) => TYPE[t] || (t || "").toLowerCase().replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase());

export async function loadTraining(args: string[], _q: URLSearchParams, ui: Ui) {
  if (args[0] === "w" && args[1]) return loadWorkout(args[1]);
  const [fr, to] = rangeDates(ui);
  const [rs, tr] = await Promise.all([get<RunsResponse>(`/runs?from=${fr}&to=${to}&all_types=true`), get<TrainingResponse>(`/training?from=${fr}&to=${to}`)]);
  return <Training fr={fr} to={to} rs={rs} tr={tr} />;
}

function Training({ fr, to, rs, tr }: { fr: string; to: string; rs: RunsResponse; tr: TrainingResponse }) {
  const [ui, setUi] = useUi();
  const runs = rs.runs.filter(r => r.type === "RUNNING");
  const list = (ui.runsOnly ? runs : rs.runs).slice().reverse();
  const km = runs.reduce((s, r) => s + (r.km || 0), 0), mins = runs.reduce((s, r) => s + (r.minutes || 0), 0);
  const easyBase = runs.filter(r => r.pct_below_vigorous != null);
  const easy = easyBase.reduce((s, r) => s + r.minutes * r.pct_below_vigorous! / 100, 0) / (easyBase.reduce((s, r) => s + r.minutes, 0) || 1) * 100;
  const weeks = tr.weeks, ac = tr.acwr;
  const open = (id: string) => { location.hash = `#/training/w/${id}`; };

  const cfg = useMemo(() => {
    const smap = byKey(tr.strain), xs = xDates(fr, to, tr.strain), sh = shades(tr.annotations);
    const amap = byKey(ac), ax = xDates(fr, ac.length ? ac[ac.length - 1].date : to, ac);
    return {
      strain: { x: xs, height: 180, y: { min: 0, max: 21, ticks: [0, 7, 14, 21] }, shades: sh, xfmt: fShort, tipx: fDay,
        refs: [{ y: 10 }, { y: 14 }, { y: 18 }],
        series: [{ name: "Strain", type: "bar" as const, data: pick(xs, smap, r => r.strain), fmt: n1 }] },
      load: { x: weeks.map(w => w.monday), height: 170, xfmt: fShort, tipx: fWeek, series: [
        { name: "TRIMP / 7", type: "bar" as const, color: "var(--line)", data: weeks.map(w => w.trimp / 7), fmt: n1 },
        { name: "Fitness", type: "line" as const, data: weeks.map(w => w.fitness), fmt: n1 },
        { name: "Fatigue", type: "line" as const, color: "var(--ink-2)", data: weeks.map(w => w.fatigue), fmt: n1 }] },
      acwr: { x: ax, height: 170, band: { lo: 0.8, hi: 1.3 }, xfmt: fShort, tipx: fDay,
        series: [{ name: "ACWR", type: "line" as const, data: pick(ax, amap, r => r.acwr), fmt: n2 }] },
    };
  }, [tr, fr, to]);   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="head"><h1>Training</h1><span className="spacer" /><RangeSeg /></div>
      <div className="grid g5">
        <div className="card"><div className="label">Runs</div><div className="big">{runs.length}</div></div>
        <div className="card"><div className="label">Distance</div><div className="big">{n1(km)}<small> km</small></div></div>
        <div className="card"><div className="label">Run time</div><div className="big">{fHM(mins)}</div></div>
        <div className="card"><div className="label"><Hint k="easy">Easy (Z1–Z2)</Hint></div><div className="big">{easyBase.length ? Math.round(easy) : "–"}<small>%</small></div></div>
        <div className="card"><div className="label">Workouts</div><div className="big">{rs.runs.length}</div><div className="note">all types</div></div>
      </div>
      <section><div className="card"><div className="chart-title"><h3><Hint k="strain">Daily Strain</Hint></h3><span className="note">dashed: moderate 10, high 14, all out 18</span><EventNote anns={tr.annotations} /></div>
        <Chart id="c-strain" cfg={cfg.strain} /></div></section>
      <section><h2>Workouts</h2>
        <div className="head" style={{ marginBottom: 10 }}><div className="seg" id="wt">
          <button className={!ui.runsOnly ? "on" : ""} onClick={() => setUi({ runsOnly: false })}>All</button>
          <button className={ui.runsOnly ? "on" : ""} onClick={() => setUi({ runsOnly: true })}>Runs</button>
        </div></div>
        <Paged head={<tr><th>Date</th><th>Type</th><th>Name</th><th className="num">Time</th><th className="num">km</th><th className="num">Pace</th><th className="num">Avg HR</th>
          <th className="num"><Hint k="easy">Z1–Z2</Hint></th><th className="num"><Hint k="drift">Drift</Hint></th><th className="num"><Hint k="ef">EF</Hint></th>
          <th className="num"><Hint k="trimp">TRIMP</Hint></th><th className="num"><Hint k="strain">Strain</Hint></th></tr>}
          rows={list.length ? list.map(r => (
            <tr key={r.id} className="click" data-id={r.id} onClick={() => open(r.id)}>
              <td>{fDay(r.date)}</td><td>{typeName(r.type)}</td><td>{r.name}</td><td className="num">{fHM(r.minutes)}</td><td className="num">{r.km ? n2(r.km) : "–"}</td>
              <td className="num">{r.km && PACED.has(r.type) ? fPace(r.minutes * 60 / r.km) : "–"}</td><td className="num">{n0(r.avg_hr)}</td><td className="num">{pct(r.pct_below_vigorous)}</td>
              <td className="num">{r.decoupling_pct != null ? sgn(r.decoupling_pct, 1) + "%" : "–"}</td><td className="num">{n2(r.ef)}</td><td className="num">{n0(r.trimp)}</td><td className="num">{n1(r.strain)}</td>
            </tr>
          )) : [<tr key="none"><td colSpan={12} className="note">No workouts in this range.</td></tr>]} />
      </section>
      <section><h2>Weekly load</h2><div className="grid g2">
        <div className="card"><div className="chart-title"><h3><Hint k="fitness">Fitness</Hint> and <Hint k="fatigue">fatigue</Hint></h3></div>
          <Legend items={[["Fitness (42 d)", "var(--ink)"], ["Fatigue (7 d)", "var(--ink-2)"], ["Weekly TRIMP", "var(--line)"]]} /><Chart id="c-load" cfg={cfg.load} /></div>
        <ChartBox id="c-acwr" title={<Hint k="acwr">Acute:chronic load</Hint>} now={ac.length ? n2(ac[ac.length - 1].acwr) : ""} sub="from the latest Takeout export" cfg={cfg.acwr} />
      </div>
        <Paged style={{ marginTop: 16 }} head={<tr><th>Week</th><th className="num">Workouts</th><th className="num"><Hint k="trimp">TRIMP</Hint></th><th className="num"><Hint k="fitness">Fitness</Hint></th>
          <th className="num"><Hint k="fatigue">Fatigue</Hint></th><th className="num"><Hint k="form">Form</Hint></th><th className="num"><Hint k="easy">Z1–Z2 (easy)</Hint></th>
          <th className="num">Z1 only</th><th className="num"><Hint k="sri">Sleep regularity</Hint></th></tr>}
          rows={weeks.slice().reverse().map(w => (
            <tr key={w.monday}><td>{fWeek(w.monday)}{w.partial ? <> <span className="pill">so far</span></> : null}</td><td className="num">{w.workouts}</td><td className="num">{n0(w.trimp)}</td>
              <td className="num">{n1(w.fitness)}</td><td className="num">{n1(w.fatigue)}</td><td className="num">{sgn(w.form, 1)}</td><td className="num">{pct(w.pct_below_vigorous)}</td>
              <td className="num">{pct(w.pct_below_first)}</td><td className="num">{n0(w.sri)}</td></tr>
          ))} />
      </section>
      <section><h2>Personal records</h2><p className="note">From Google (latest Takeout export).</p>
        <Paged head={<tr><th>Record</th><th className="num">Value</th><th className="num">Pace</th><th>Date</th></tr>}
          rows={tr.records.map(p => (
            <tr key={p.record}><td>{p.record}</td><td className="num">{p.seconds != null ? fDur(p.seconds) : n2(p.km) + " km"}</td>
              <td className="num">{p.seconds != null && p.km ? fPace(p.seconds / p.km) + " /km" : "–"}</td><td>{fDay(p.date)}</td></tr>
          ))} />
      </section>
    </>
  );
}

// --------------------------------------------------------------- workout ----

async function loadWorkout(id: string) {
  const [w, s] = await Promise.all([get<RunDetail>(`/runs/${id}`), get<WorkoutSeries>(`/workout-series/${id}`).catch(() => null)]);
  return <Workout w={w} s={s} />;
}

const PACE_SRC: Record<string, string> = { watch: "from the watch's distance", gps: "from the GPS track" };
const CAD_SRC: Record<string, string> = { watch: "30 s averages", "steps per minute": "watch steps per minute" };
const fT = (v: number) => `${Math.floor(v / 60)}:${pad2(Math.round(v % 60))}`;

function Workout({ w, s }: { w: RunDetail; s: WorkoutSeries | null }) {
  const a = w.analysis, g = w.google, ms = g?.metrics_summary || {}, mob = ms.mobilityMetrics || {};
  // the API also sends day/night annotations (alcohol, illness); only notes on the run itself belong here
  const runAnns = w.annotations.filter(x => (x.target || "").startsWith("workout:"));
  // Google has four zones; Z3 and Z4 split its vigorous zone at the middle
  // (about 72 % of heart-rate reserve), so Z5 is Google's peak. Minutes come
  // from the run analysis; the vigorous minutes are shared out by how the HR
  // samples fall either side of the split.
  const gz = s?.zones || {}, split = gz.VIGOROUS && gz.PEAK ? Math.round((gz.VIGOROUS[0] + gz.PEAK[0]) / 2) : null;
  const vig = (s?.hr || []).filter(p => p.s >= 0 && split && p.bpm >= gz.VIGOROUS[0] && p.bpm < gz.PEAK[0]);
  const lowVig = vig.length ? vig.filter(p => p.bpm < split!).length / vig.length : null;
  const zones: [string, number | null, string | null][] = [
    ["Z1", a.min_light, gz.LIGHT && gz.MODERATE ? `< ${gz.MODERATE[0]}` : null],
    ["Z2", a.min_moderate, gz.MODERATE && gz.VIGOROUS ? `${gz.MODERATE[0]}–${gz.VIGOROUS[0] - 1}` : null],
    ["Z3", a.min_vigorous != null && lowVig != null ? a.min_vigorous * lowVig : null, split ? `${gz.VIGOROUS[0]}–${split - 1}` : null],
    ["Z4", a.min_vigorous != null && lowVig != null ? a.min_vigorous * (1 - lowVig) : null, split ? `${split}–${gz.PEAK[0] - 1}` : null],
    ["Z5", a.min_peak, gz.PEAK ? `${gz.PEAK[0]}+` : null]];
  if (lowVig == null && a.min_vigorous) zones[2][1] = a.min_vigorous;   // no HR samples: all vigorous time shows as Z3
  const ztot = zones.reduce((t, [, v]) => t + (v || 0), 0);
  const pace = a.km && PACED.has(a.type) ? a.minutes * 60 / a.km : null;
  const splits = g?.km_splits || [];
  const splitHr = splits.some(k => k.avg_hr != null);
  // pace, heart rate and cadence share one time axis; walking breaks (cadence
  // under a jog) are shaded on all three and left out of the running figures
  const hr = s ? s.hr.filter(p => p.s >= 0).map(p => ({ s: p.s, v: p.bpm })) : [];
  const paceS = s?.pace || [], cadS = s?.cadence || [], walks = s?.walks || [];
  const walking = (x: number) => walks.some(w => x >= w.from && x < w.to);
  const walkS = walks.reduce((t, w) => t + w.to - w.from, 0);
  const runPace = paceS.filter(p => p.v && !walking(p.s)).map(p => 1000 / p.v!);   // speeds, averaged as speeds
  const runCad = cadS.filter(p => !walking(p.s)).map(p => p.v);
  const runOnly = walks.length ? `, walking breaks shaded (${fDur(walkS)} in total) and left out of the average` : "";

  // time ticks on whole minutes, about one per 90 px of the chart card
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => setWidth(box.current?.clientWidth || 0), []);
  const cfg = useMemo(() => {
    if (!width) return null;
    const tStep = [60, 120, 300, 600, 900, 1800].find(t => (s?.duration || 0) / t <= width / 90) || 3600;
    const axis = { numeric: true, xmin: 0, xmax: s?.duration, height: 150, xfmt: fT, sync: "run",
      xticks: Array.from({ length: Math.floor((s?.duration || 0) / tStep) + 1 }, (_, k) => k * tStep),
      shades: walks.map(w => ({ ...w, label: "walking" })) };
    const tipWalk = (xs: number[]) => (i: number) => walking(xs[i]) ? "<div><span>walking</span></div>" : "";
    const out: Record<string, object> = {};
    if (paceS.length) {
      const pv = paceS.map(p => p.v).filter((v): v is number => !!v).sort((x, y) => x - y);
      const pq = (q: number) => pv[Math.min(pv.length - 1, Math.floor(q * pv.length))];
      const pLo = pv.length ? Math.floor(pq(0.02) / 60) * 60 : 0, pHi = pv.length ? Math.ceil(pq(0.98) / 60) * 60 : 0;
      const pStep = pHi - pLo > 360 ? 120 : 60;
      const xs = paceS.map(p => p.s);
      out.pace = { ...axis, x: xs, gap: 30, tipExtra: tipWalk(xs),
        y: { invert: true, fmt: fPace, min: pLo, max: pHi, ticks: Array.from({ length: (pHi - pLo) / pStep + 1 }, (_, k) => pLo + k * pStep) },
        series: [{ name: "pace", type: "line", width: 1.3, data: paceS.map(p => p.v), fmt: (v: number | null) => v ? fPace(v) + " /km" : "stopped" }] };
    }
    if (hr.length) {
      const xs = hr.map(p => p.s), hv = hr.map(p => p.v);
      const hLo = Math.floor(Math.min(...hv) / 20) * 20, hHi = Math.ceil(Math.max(...hv) / 20) * 20;
      out.hr = { ...axis, x: xs, gap: 120, tipExtra: tipWalk(xs), y: { min: hLo, max: hHi, fmt: n0, ticks: niceTicksBy(hLo, hHi, hHi - hLo > 100 ? 40 : 20) },
        series: [{ name: "heart rate", type: "line", width: 1.3, data: hv, fmt: (v: number | null) => n0(v) + " bpm" }] };
    }
    if (cadS.length) {
      const xs = cadS.map(p => p.s);
      out.cad = { ...axis, x: xs, tipExtra: (i: number) => walking(xs[i]) ? `<div><span>walking</span><b>${cadS[i].v} spm</b></div>` : "",
        series: [{ name: "cadence", type: "line", width: 1.3, data: cadS.map(p => walking(p.s) ? null : p.v), fmt: (v: number | null) => v != null ? v + " spm" : "–" }] };
    }
    return out as Record<string, Parameters<typeof Chart>[0]["cfg"]>;
  }, [width, s]);   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="head"><a href="#/training" className="note">‹ Training</a></div>
      <div className="head"><h1>{a.name}</h1><span className="sub">{typeName(a.type)} · {fDay(a.date)}{g?.start_time ? " · " + fClock(g.start_time) : ""}</span></div>
      <div className="grid g5">
        <div className="card"><div className="label">Distance</div><div className="big">{a.km ? n2(a.km) : "–"}<small>{a.km ? " km" : ""}</small></div></div>
        <div className="card"><div className="label">Time</div><div className="big">{fDur(a.minutes * 60)}</div></div>
        <div className="card"><div className="label">Pace</div><div className="big">{fPace(pace)}<small>{pace ? " /km" : ""}</small></div></div>
        <div className="card"><div className="label">Avg HR</div><div className="big">{n0(a.avg_hr)}<small> bpm</small></div><div className="note">max {n0(a.max_hr)}</div></div>
        <div className="card"><div className="label"><Hint k="strain">Strain</Hint></div><div className="big">{n1(w.strain)}</div><div className="note"><Hint k="trimp">TRIMP</Hint> {n0(a.trimp)}</div></div>
      </div>
      <section><div className="card runcharts" ref={box}>
        <div className="chart-title"><h3>Pace</h3><span className="now">{runPace.length ? fPace(1000 / mean(runPace)!) + " /km" : ""}</span></div>
        <div className="note csub">{paceS.length ? `min/km over 30 s, ${PACE_SRC[s!.pace_source || ""] || ""}${runOnly}` : "No distance samples for this workout."}</div>
        {paceS.length && cfg?.pace ? <Chart id="c-pace" cfg={cfg.pace} /> : null}
        <div className="chart-title"><h3>Heart rate</h3><span className="now">{hr.length ? n0(mean(hr.map(p => p.v))) + " bpm" : ""}</span></div>
        {hr.length ? (cfg?.hr ? <Chart id="c-whr" cfg={cfg.hr} /> : null) : <div id="c-whr"><div className="empty">No heart-rate samples.</div></div>}
        {cadS.length ? <>
          <div className="chart-title"><h3><Hint k="cadence">Cadence</Hint></h3><span className="now">{runCad.length ? n0(mean(runCad)) + " spm" : ""}</span></div>
          <div className="note csub">steps/min, {CAD_SRC[s!.cadence_source || ""] || ""}{walks.length ? ", running only" : ""}</div>
          {cfg?.cad ? <Chart id="c-cad" cfg={cfg.cad} /> : null}
        </> : null}
      </div></section>
      <section><div className="grid g3">
        <div className="card"><h3><Hint k="zones">Heart rate zones</Hint></h3>
          {zones.map(([n, v, bpm]) => <Kv key={n} k={<>{n}{bpm ? <> <span className="note">{bpm} bpm</span></> : null}</>}>{v != null ? `${n1(v)} min · ${pct(v / (ztot || 1) * 100)}` : "–"}</Kv>)}</div>
        <div className="card"><h3>Efficiency</h3>
          <Kv k={<Hint k="drift">Aerobic decoupling</Hint>}>{a.decoupling_pct != null ? sgn(a.decoupling_pct, 1) + "%" : "–"}</Kv>
          <Kv k={<Hint k="ef">Efficiency factor</Hint>}>{n2(a.ef)}</Kv>
          <Kv k={<Hint k="hrr">HR recovery 60 s</Hint>}>{a.hrr60 != null ? n0(a.hrr60) + " bpm" : "–"}</Kv>
          <Kv k={<Hint k="hrr">HR recovery 120 s</Hint>}>{a.hrr120 != null ? n0(a.hrr120) + " bpm" : "–"}</Kv>
          <Kv k={<Hint k="steady">Steady pace</Hint>}>{a.steady == null ? "–" : a.steady ? "yes" : "no"}</Kv>
          <Kv k={<Hint k="split_cv">Split variation</Hint>}>{a.split_cv != null ? n1(a.split_cv) + "%" : "–"}</Kv>
          <Kv k={<Hint k="climb">Climb</Hint>}>{a.climb_m_per_km != null ? n1(a.climb_m_per_km) + " m/km" : "–"}</Kv></div>
        <div className="card"><h3>Running form</h3>
          <Kv k={<Hint k="cadence">Cadence</Hint>}>{mob.avgCadenceStepsPerMinute ? mob.avgCadenceStepsPerMinute + " spm" : "–"}</Kv>
          <Kv k={<Hint k="stride">Stride</Hint>}>{mob.avgStrideLengthMillimeters ? n0(mob.avgStrideLengthMillimeters / 10) + " cm" : "–"}</Kv>
          <Kv k={<Hint k="vert_osc">Vertical oscillation</Hint>}>{mob.avgVerticalOscillationMillimeters ? n1(mob.avgVerticalOscillationMillimeters / 10) + " cm" : "–"}</Kv>
          <Kv k={<Hint k="vert_ratio">Vertical ratio</Hint>}>{mob.avgVerticalRatio ? n1(mob.avgVerticalRatio) + "%" : "–"}</Kv>
          <Kv k={<Hint k="gct">Ground contact</Hint>}>{mob.avgGroundContactTimeDuration ? n0(parseFloat(mob.avgGroundContactTimeDuration) * 1000) + " ms" : "–"}</Kv>
          <Kv k="Elevation gain">{ms.elevationGainMillimeters ? n0(ms.elevationGainMillimeters / 1000) + " m" : "–"}</Kv>
          <Kv k="Calories">{n0(g?.calories)}</Kv><Kv k="Steps">{n0(g?.steps)}</Kv></div>
      </div></section>
      {s?.route ? <section><div className="card"><h3>Route</h3><Route pts={s.route} /></div></section> : null}
      {splits.length ? <section><h2>Kilometre splits</h2>
        <Table><tr><th>km</th><th className="num">Pace</th>{splitHr ? <th className="num">Avg HR</th> : null}</tr>
          {splits.map(k => <tr key={k.km}><td>{k.km}</td><td className="num">{fPace(k.pace_s_per_km)}</td>{splitHr ? <td className="num">{n0(k.avg_hr)}</td> : null}</tr>)}</Table>
      </section> : null}
      {a.notes || runAnns.length ? <section><h2>Notes</h2>{a.notes ? <p>{a.notes}</p> : null}
        {runAnns.map(x => <p key={x.id} className="note">{x.kind} ({x.status}): {x.note || ""}</p>)}</section> : null}
    </>
  );
}
