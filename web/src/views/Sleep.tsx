// Sleep: Sleep Performance, asleep vs. need, stages and bed times over the
// range, every night in a table. #/sleep/<wake date> opens one night.

import { useEffect, useMemo, type ReactNode } from "react";
import { get, prefetch } from "../api/client";
import type { ExplainSleep, Nap, Night as NightRow, SleepResponse, TrendResponse } from "../api/types";
import { Kv, Paged, RangeSeg } from "../components/bits";
import { Chart, ChartBox, Hypnogram, Legend } from "../components/Chart";
import { Hint } from "../components/Hint";
import { addDays, byKey, fClock, fDay, fHM, fShort, mean, n1, pad2, pct, pick, shades, xDates } from "../lib/format";
import { rangeDates, type Ui } from "../ui";

const clockY = (t: string | null | undefined) => { if (!t) return null; const d = new Date(t); return ((d.getHours() + d.getMinutes() / 60) - 18 + 24) % 24; };
const fClockY = (v: number) => pad2(Math.round((v + 18) % 24)) + ":00";

const LANES: [string, string, string][] = [["awake", "Awake", "var(--red)"], ["rem", "REM", "var(--ink-2)"], ["light", "Light", "var(--ink-3)"], ["deep", "Deep", "var(--ink)"]];
type StageKey = "deep_minutes" | "rem_minutes" | "light_minutes" | "wake_minutes";
const STAGES: [string, StageKey, string][] = [["Deep", "deep_minutes", "var(--ink)"], ["REM", "rem_minutes", "var(--ink-2)"],
  ["Light", "light_minutes", "var(--ink-3)"], ["Awake", "wake_minutes", "var(--red)"]];

const nightPaths = (d: string) => [`/sleep?from=${addDays(d, -30)}&to=${addDays(d, 7)}`, `/explain/sleep?date=${d}`];

export async function loadSleep(args: string[], _q: URLSearchParams, ui: Ui) {
  if (args[0]) return loadNight(args[0]);
  const [fr, to] = rangeDates(ui);
  const [sl, gs] = await Promise.all([get<SleepResponse>(`/sleep?from=${fr}&to=${to}`), get<TrendResponse>(`/trend/google_sleep_score?from=${fr}&to=${to}`)]);
  return <Sleep fr={fr} to={to} sl={sl} gs={gs} />;
}

const asleepOf = (r: NightRow) => r.performance?.asleep_min ?? r.google?.total_minutes;
const napLine = (n: Nap) => `<div><span>Nap ${fClock(n.start_time)}–${fClock(n.end_time)}</span><b>${fHM(n.asleep_min)}</b></div>`;

function Sleep({ fr, to, sl, gs }: { fr: string; to: string; sl: SleepResponse; gs: TrendResponse }) {
  const nights = sl.nights;
  const last = nights[nights.length - 1];
  const cons = nights.map(r => r.performance?.consistency).filter((v): v is number => v != null);
  const show = (d: string) => { location.hash = `#/sleep/${d}`; };

  const cfg = useMemo(() => {
    const map = byKey(nights, "night");
    const xs = xDates(fr, to, nights, "night");
    // naps by the day they were taken, which is also the column of the night ending that morning
    const naps: Record<string, Nap[]> = {};
    sl.naps.forEach(n => { (naps[n.date] = naps[n.date] || []).push(n); });
    const napTip = (i: number) => (naps[xs[i]] || []).map(napLine).join("");
    const sh = shades(sl.annotations);
    const gmap: Record<string, number[]> = {};
    gs.points.forEach(p => { (gmap[p.date] = gmap[p.date] || []).push(p.value); });
    const gx = xs.filter(d => d <= (gs.points.slice(-1)[0]?.date || ""));
    const hfmt = (v: number | null) => v == null ? "–" : v.toFixed(1) + " h";
    const onClick = (i: number) => { if (map[xs[i]]) show(xs[i]); };
    const base = { x: xs, xfmt: fShort, tipx: fDay, onClick };
    const st = (k: StageKey) => pick(xs, map, r => r.google?.[k] != null ? r.google[k]! / 60 : null);
    return {
      dur: { ...base, height: 180, y: { min: 0, fmt: (v: number) => v + "h" }, shades: sh,
        series: [
          { name: "Asleep", type: "bar" as const, data: pick(xs, map, r => { const a = asleepOf(r); return a != null ? a / 60 : null; }), fmt: hfmt },
          { name: "Need", type: "line" as const, dash: true, color: "var(--ink-2)", noDots: true, data: pick(xs, map, r => r.performance?.need_min != null ? r.performance.need_min / 60 : null), fmt: hfmt },
        ] },
      perf: { ...base, height: 180, y: { min: 0, max: 100, fmt: (v: number) => v + "%" }, shades: sh,
        series: [{ name: "Performance", type: "line" as const, data: pick(xs, map, r => r.performance?.sleep_performance), fmt: pct },
          { name: "Sufficiency", type: "line" as const, color: "var(--ink-3)", noDots: true, data: pick(xs, map, r => r.performance?.sufficiency), fmt: pct }] },
      stages: { ...base, height: 170, y: { min: 0, fmt: (v: number) => v + "h" },
        series: STAGES.map(([n, k, c]) => ({ name: n, type: "bar" as const, stack: "s", color: c, data: st(k), fmt: (v: number | null) => fHM(v != null ? v * 60 : null) })) },
      times: { ...base, height: 170, y: { min: 0, max: 24, invert: true, ticks: [0, 6, 12, 18, 24], fmt: fClockY }, tipExtra: napTip,
        series: [{ name: "In bed", type: "range" as const, color: "var(--ink)", lo: pick(xs, map, r => clockY(r.google?.start_time)),
          hi: pick(xs, map, r => { const a = clockY(r.google?.start_time), b = clockY(r.google?.end_time); return b != null && a != null && b < a ? b + 24 : b; }),
          tip: (i: number) => { const g = map[xs[i]]?.google; return g ? `${fClock(g.start_time)} – ${fClock(g.end_time)}` : "–"; } },
          // a day's naps as one light bar, first start to last end
          { name: "Nap", type: "range" as const, color: "var(--ink-3)", noTip: true,
            lo: xs.map(d => naps[d] ? Math.min(...naps[d].map(n => clockY(n.start_time)!)) : null),
            hi: xs.map(d => naps[d] ? Math.max(...naps[d].map(n => clockY(n.end_time)!)) : null) }] },
      naps: { ...base, height: 140, y: { min: 0, fmt: (v: number) => v + "m" }, tipExtra: napTip,
        series: [{ name: "Napped", type: "bar" as const, color: "var(--ink-2)", noTip: true,
          data: xs.map(d => naps[d] ? naps[d].reduce((t, n) => t + (n.asleep_min || 0), 0) : map[d] ? 0 : null) }] },
      gscore: { x: gx, height: 140, y: { fmt: (v: number) => Math.round(v) }, xfmt: fShort, tipx: fDay,
        series: [{ name: "Score", type: "line" as const, data: gx.map(d => gmap[d] ? Math.max(...gmap[d]) : null), fmt: (v: number | null) => v == null ? "–" : String(Math.round(v)) }] },
    };
  }, [sl, gs, fr, to]);   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="head"><h1>Sleep</h1><span className="spacer" /><RangeSeg /></div>
      <div className="grid g2">
        <ChartBox id="c-perf" title={<Hint k="sleep_perf">Sleep Performance</Hint>} now={last ? pct(last.performance?.sleep_performance) : ""} cfg={cfg.perf} />
        <ChartBox id="c-dur" title={<>Asleep vs. <Hint k="need">need</Hint></>} now={last ? fHM(asleepOf(last)) : ""} sub="bars asleep, dashed line need" cfg={cfg.dur} />
      </div>
      <section><div className="grid g2">
        <div className="card"><div className="chart-title"><h3><Hint k="stages">Stages</Hint></h3></div>
          <Legend items={STAGES.map(([n, , c]) => [n, c])} /><Chart id="c-stages" cfg={cfg.stages} /></div>
        <ChartBox id="c-times" title="Bed and wake times" now={cons.length ? <><Hint k="consistency">consistency</Hint> {Math.round(mean(cons.slice(-7))!)}</> : ""} sub="regularity" cfg={cfg.times} />
      </div></section>
      <section><div className="grid g2">
        <ChartBox id="c-gscore" title={<Hint k="google_sleep">Google sleep score</Hint>} now="" sub="from the latest Takeout export" cfg={cfg.gscore} />
        <ChartBox id="c-naps" title={<Hint k="naps">Naps</Hint>} now={sl.naps.length ? `${sl.naps.length} in this range` : "none in this range"} cfg={cfg.naps} />
      </div></section>
      <section><h2>Nights</h2><p className="note">Click a night, or a day on the charts, for its detail.</p>
        <Paged head={<tr><th>Night (wake date)</th><th className="num"><Hint k="sleep_perf">Performance</Hint></th><th>Bed</th><th>Wake</th><th className="num">Asleep</th>
          <th className="num"><Hint k="need">Need</Hint></th><th className="num">Deep</th><th className="num">REM</th><th className="num">Light</th><th className="num">Awake</th></tr>}
          rows={nights.slice().reverse().map(r => {
            const g = r.google, p = r.performance;
            return (
              <tr key={r.night} className="click" data-d={r.night} onClick={() => show(r.night)}
                onPointerEnter={() => { get(`/explain/sleep?date=${r.night}`).catch(() => {}); }}>
                <td>{fDay(r.night)}</td><td className="num"><b>{pct(p?.sleep_performance)}</b></td><td>{fClock(g?.start_time)}</td><td>{fClock(g?.end_time)}</td>
                <td className="num">{fHM(asleepOf(r))}</td>
                <td className="num" title={p?.need_min != null ? `your baseline ${fHM(p.need_baseline_min)} + after strain ${fHM(p.need_strain_min)} + sleep debt ${fHM(p.need_debt_min)} − naps ${fHM(p.nap_prev_day_min)}` : ""}>{fHM(p?.need_min)}</td>
                <td className="num">{fHM(g?.deep_minutes)}</td><td className="num">{fHM(g?.rem_minutes)}</td><td className="num">{fHM(g?.light_minutes)}</td><td className="num">{fHM(g?.wake_minutes)}</td>
              </tr>
            );
          })} />
      </section>
    </>
  );
}

// ----------------------------------------------------------------- night ----
// Night detail: the hypnogram first, then Sleep Performance and its parts,
// then the stages against the 30 nights before.

async function loadNight(d: string) {
  const [sl, e] = await Promise.all([get<SleepResponse>(nightPaths(d)[0]),
    get<ExplainSleep>(nightPaths(d)[1]).catch((err: Error): Ex => ({ error: err.message }))]);
  return <Night d={d} sl={sl} e={e} />;
}

// the explanation, or only its error when the request itself failed
type Ex = Partial<ExplainSleep> & { error: string | null };

function Night({ d, sl, e }: { d: string; sl: SleepResponse; e: Ex }) {
  const i = sl.nights.findIndex(r => r.night === d);
  const r = sl.nights[i], g = r?.google || {} as Partial<NonNullable<NightRow["google"]>>, p = r?.performance || {} as Partial<NonNullable<NightRow["performance"]>>;
  const ok = !e.error ? e : null, N = ok?.need;
  const prev = i > 0 ? sl.nights[i - 1].night : (i < 0 ? sl.nights.filter(x => x.night < d).pop()?.night : null);
  const next = i >= 0 ? sl.nights[i + 1]?.night : sl.nights.find(x => x.night > d)?.night;
  const before = sl.nights.filter(x => x.night < d && x.google);
  const avg = (k: StageKey | "total_minutes") => before.length ? mean(before.map(x => x.google![k]).filter(v => v != null)) : null;
  const inBed = STAGES.reduce((t, [, k]) => t + (g[k] || 0), 0);
  const asleep = p.asleep_min ?? g.total_minutes;
  const hyp = e.hypnogram ?? null;
  // the night an annotation lands on (alcohol: the night after the evening)
  const anns = sl.annotations.filter(a => a.status !== "rejected" && (a.target === `night:${d}` || (!a.target && (a.excludes_nights || [] as string[]).includes(d))));
  const go = (n: string | null | undefined) => { if (n) location.hash = `#/sleep/${n}`; };
  useEffect(() => { prefetch(...[prev, next].filter((n): n is string => !!n).flatMap(nightPaths)); }, [prev, next]);
  const hcfg = useMemo(() => hyp ? { ...hyp, lanes: LANES, tfmt: (t: Date) => fClock(t), dfmt: fHM } : null, [hyp]);
  const stat = (label: ReactNode, big: string, sub: ReactNode) =>
    <div className="card"><div className="label">{label}</div><div className="big">{big}</div><div className="note">{sub}</div></div>;
  return (
    <>
      <div className="head"><a href="#/sleep" className="note">‹ Sleep</a></div>
      <div className="head"><h1>{fDay(d)}</h1><span className="sub">{g.start_time ? `${fClock(g.start_time)}–${fClock(g.end_time)} · ` : ""}night ending on this day</span>
        <div className="datenav">
          <button className="btn small" id="prev" aria-label="Previous night" disabled={!prev} onClick={() => go(prev)}>‹</button>
          <button className="btn small" id="next" aria-label="Next night" disabled={!next} onClick={() => go(next)}>›</button>
        </div>
      </div>
      <div className="grid g5 trio">
        {stat(<Hint k="sleep_perf">Sleep Performance</Hint>, pct(ok?.value ?? p.sleep_performance), p.sufficiency != null ? <><Hint k="sufficiency">sufficiency</Hint> {pct(p.sufficiency)}</> : "")}
        {stat("Asleep", fHM(asleep), N ? <><Hint k="need">need</Hint> {fHM(N.total_min)}</> : "")}
        {stat("In bed", fHM(inBed || null), g.start_time ? `${fClock(g.start_time)}–${fClock(g.end_time)}` : "")}
      </div>
      <section><div className="card"><h3>Sleep phases</h3>{hcfg ? (
        <><div className="note csub">Stages through the night, {fClock(hcfg.start)}–{fClock(hcfg.end)}{hcfg.brief.length ? ` · thin marks: ${hcfg.brief.length} brief wake-ups` : ""}</div>
          <Hypnogram id="c-hypno" cfg={hcfg} /></>
      ) : <p className="note">No stage data for this night.</p>}</div></section>
      <section><div className="card"><h3><Hint k="sleep_perf">Sleep Performance</Hint></h3>{!ok ? <p className="note">{e.error}</p> : (
        <div className="cols">
          <div style={{ flex: 1, minWidth: 240 }}><div className="tablewrap"><table><tbody>
            <tr><th>Part</th><th className="num">Score</th><th className="num">Points</th></tr>
            {Object.entries(ok.components || {}).map(([k, c]) => (
              <tr key={k}><td><Hint k={k}>{k[0].toUpperCase() + k.slice(1)}</Hint></td><td className="num">{n1(c.value)}</td><td className="num">{n1(c.contribution)}</td></tr>
            ))}
          </tbody></table></div></div>
          <div style={{ flex: 1, minWidth: 220 }}>
            <Kv k="Asleep">{fHM(ok.asleep_min)}</Kv><Kv k={<Hint k="need">Need</Hint>}>{fHM(N?.total_min)}</Kv>
            <Kv k="· baseline">{fHM(N?.baseline_min)}</Kv><Kv k="· from strain">{"+" + fHM(N?.from_strain_min)}</Kv>
            <Kv k="· from sleep debt">{"+" + fHM(N?.from_sleep_debt_min)}</Kv><Kv k="· minus naps">{"−" + fHM(N?.minus_naps_min)}</Kv>
          </div>
        </div>
      )}</div></section>
      <section><h2><Hint k="stages">Stages</Hint></h2><div className="tablewrap"><table><tbody>
        <tr><th>Stage</th><th className="num">Time</th><th className="num">Share</th><th className="num">Usual</th></tr>
        {STAGES.map(([n, k, c]) => (
          <tr key={k}><td><span className="band" style={{ background: c }} />{n}</td><td className="num">{fHM(g[k])}</td>
            <td className="num">{inBed && g[k] != null ? pct(g[k]! / inBed * 100) : "–"}</td><td className="num">{fHM(avg(k))}</td></tr>
        ))}
        <tr><td><b>Asleep</b></td><td className="num"><b>{fHM(asleep)}</b></td><td className="num">{inBed && asleep != null ? pct(asleep / inBed * 100) : "–"}</td><td className="num">{fHM(avg("total_minutes"))}</td></tr>
      </tbody></table></div><p className="note">Share of the time in bed. Usual is the average of the {before.length} nights before this one.</p></section>
      {anns.length ? <section><h2>Notes</h2>{anns.map(x => <p key={x.id} className="note">{x.kind} ({x.status}): {x.note || ""}</p>)}</section> : null}
    </>
  );
}
