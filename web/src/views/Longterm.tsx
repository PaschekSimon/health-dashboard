// Long-term: Healthspan age, VO2 max, weight, monthly averages, events
// (annotations you can add, confirm and delete), ECGs and assessments.
// #/longterm/vo2max and #/longterm/healthspan open the VO2 max and Healthspan
// age histories.

import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { get, send } from "../api/client";
import type { AnnotationResult, Ecg, LongtermResponse, Meta } from "../api/types";
import { EventNote, Kv, Paged, RangeSeg, Table } from "../components/bits";
import type { ChartConfig } from "../lib/charts.js";
import { Chart } from "../components/Chart";
import { Hint } from "../components/Hint";
import { followRun, refresh } from "../sync";
import { addDays, byKey, dateList, fClock, fDay, fitY, fMonth, fShort, n0, n1, n2, pick, sgn, shades, today, xDates } from "../lib/format";
import { rangeDates, useUi, type Ui } from "../ui";

const HS: Record<string, string> = { vo2max: "VO2 max", rhr: "Resting HR", steps: "Steps", moderate: "Moderate activity", vigorous: "Vigorous activity",
  strength: "Strength training", sleep_h: "Sleep", sri: "Sleep regularity" };
const MONTHLY: [string, string, (v: number) => string][] = [["recovery", "Recovery %", n0], ["hrv_ms", "HRV ms", n1], ["rhr", "Resting HR", n1],
  ["sleep_h", "Sleep h", n1], ["sleep_performance", "Sleep Performance", n0], ["strain", "Strain", n1], ["steps", "Steps / day", n0],
  ["vo2max", "VO2 max", n1], ["weight", "Weight kg", n1]];
const HS_HINT: Record<string, string> = { vo2max: "vo2max", rhr: "rhr", moderate: "active_min", vigorous: "active_min", sri: "sri" };
const MONTHLY_HINT: Record<string, string> = { recovery: "recovery", hrv_ms: "hrv", rhr: "rhr", sleep_performance: "sleep_perf", strain: "strain", vo2max: "vo2max" };
const VN: Record<string, string> = { all: "All days", excl_illness: "Without illness", last30: "Last 30 days", last30_excl_illness: "Last 30, without illness" };
const lower = (s: string | null | undefined) => (s || "").replace(/_/g, " ").toLowerCase();

export async function loadLongterm(args: string[], _q: URLSearchParams, ui: Ui) {
  if (args[0] === "vo2max") return <Vo2 lt={await get<LongtermResponse>("/longterm")} range={rangeDates(ui)} />;
  if (args[0] === "healthspan") return <HsHistory lt={await get<LongtermResponse>("/longterm")} range={rangeDates(ui)} />;
  // annotation kinds come from the backend's fixed list
  const [lt, m] = await Promise.all([get<LongtermResponse>("/longterm"), get<Meta>("/meta").catch(() => null)]);
  return <Longterm lt={lt} kinds={m?.annotation.kinds || ["note"]} />;
}

function Longterm({ lt, kinds }: { lt: LongtermResponse; kinds: string[] }) {
  const [ui, setUi] = useUi();
  const [ecg, setEcg] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const hs = lt.healthspan, variants = Object.keys(hs.variants);
  const v = variants.includes(ui.hsVariant) ? ui.hsVariant : variants[0];
  const rows = hs.variants[v] || [];
  const age = rows.find(r => r.metric === "healthspan_age"), poa = rows.find(r => r.metric === "pace_of_aging");
  const parts = rows.filter(r => HS[r.metric]).sort((a, b) => (b.years_adjusted ?? 0) - (a.years_adjusted ?? 0));
  const monthN: Record<string, number> = {};
  Object.values(lt.monthly).flat().forEach(r => { monthN[r.month] = Math.max(monthN[r.month] || 0, r.n); });
  const months = Object.keys(monthN).filter(m => monthN[m] >= 3).sort();   // drop months with a stray day or two
  const vo2 = lt.vo2max, wt = lt.weight;

  const cfg = useMemo(() => {
    const vx = vo2.map(r => r.date), rv = byKey(lt.run_vo2max);
    const target = rows.find(r => r.metric === "vo2max")?.referent;
    const wx = wt.length ? dateList(wt[0].date, today()) : [];
    const wm = byKey(wt);
    return {
      vo2: { x: vx, height: 180, xfmt: fShort, tipx: fDay, shades: shades(lt.annotations),
        refs: target ? [{ y: +target, label: "target " + target }] : [],
        series: [{ name: "Google", type: "line" as const, noDots: true, data: vo2.map(r => r.vo2_max), fmt: n1 },
          { name: "Run", type: "dot" as const, color: "var(--ink-2)", r: 3, data: vx.map(d => rv[d]?.vo2max ?? null), fmt: n1 }] },
      wt: { x: wx, height: 180, xfmt: fShort, tipx: fDay,
        series: [{ name: "kg", type: "line" as const, connect: true, noDots: true, color: "var(--ink-3)", data: wx.map(d => wm[d]?.weight_kg ?? null), noTip: true },
          { name: "kg", type: "dot" as const, r: 3.5, data: wx.map(d => wm[d]?.weight_kg ?? null), fmt: n1 }] },
    };
  }, [lt, v]);   // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (action: "confirm" | "reject" | "delete", id: number) => {
    if (action === "delete" && !confirm("Delete this event?")) return;
    const r = action === "delete" ? await send<AnnotationResult>("DELETE", `/annotations/${id}`) : await send<AnnotationResult>("POST", `/annotations/${id}/${action}`);
    if (r.recompute) followRun(r.recompute);
    refresh();
  };
  const add = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>;
    const body: Record<string, unknown> = { kind: f.kind, start: f.start, end: f.end || null, note: f.note || null, status: "confirmed", source: "user" };
    if (f.kind === "alcohol") { body.end = addDays(f.start, 1); body.target = "night:" + addDays(f.start, 1); }
    try {
      const r = await send<AnnotationResult>("POST", "/annotations", body);
      if (r.recompute) followRun(r.recompute);
      refresh();
    } catch (err) { setMsg((err as Error).message); }
  };

  return (
    <>
      <div className="head"><h1>Long-term</h1></div>
      <div className="grid g2">
        <div className="card hs"><a className="hs-open" href="#/longterm/healthspan"><div className="label"><Hint k="healthspan">Healthspan age</Hint> ›</div>
          <div className="big">{age ? n1(age.value) : "–"}<small> years{age?.referent != null ? ` · actual ${n0(+age.referent)}` : ""}</small></div></a>
          {poa ? <Kv k={<Hint k="pace_of_aging">Pace of aging</Hint>}>{n2(poa.value) + "× "}<span className="note">({poa.note || ""})</span></Kv> : null}
          <Kv k="Based on">{age?.note || "–"}</Kv>
          <div className="spacer" />
          <div className="seg" id="hsv" style={{ marginTop: 10 }}>
            {variants.map(x => <button key={x} className={x === v ? "on" : ""} onClick={() => setUi({ hsVariant: x })}>{VN[x] || x}</button>)}
          </div>
          <p className="note">Computed {fDay(hs.computed_on)}. Each line: your average over the period above and the target. Averages lag behind today's values (VO2 max today: {vo2.length ? n1(vo2[vo2.length - 1].vo2_max) : "–"}).</p></div>
        <div className="card"><h3>What moves it</h3>
          <Table><tr><th>Factor</th><th className="num">Your average</th><th className="num">Target</th><th className="num"><Hint k="hs_years">Years</Hint></th></tr>
            {parts.map(r => (
              <tr key={r.metric}><td>{HS_HINT[r.metric] ? <Hint k={HS_HINT[r.metric]}>{HS[r.metric]}</Hint> : HS[r.metric]}</td>
                <td className="num">{r.metric === "steps" ? n0(r.value) : n1(r.value)} <span className="note">{r.unit}</span></td>
                <td className="num">{String(r.referent ?? "–").replace(/[()]/g, "").replace(", ", "–")}</td><td className="num">{sgn(r.years_adjusted, 2)}</td></tr>
            ))}
          </Table></div>
      </div>
      <section><div className="grid g2">
        <a className="card" href="#/longterm/vo2max"><div className="chart-title"><h3><Hint k="vo2max">VO2 max</Hint> ›</h3>
          <span className="note">line: Google daily · dots: run estimates · dashed: healthspan target</span>
          <span className="now">{vo2.length ? "today " + n1(vo2[vo2.length - 1].vo2_max) : ""}</span></div><Chart id="c-vo2" cfg={cfg.vo2} /></a>
        <div className="card"><div className="chart-title"><h3>Weight</h3>{lt.height_cm ? <span className="note">height {lt.height_cm} cm</span> : null}
          <span className="now">{wt.length ? n1(wt[wt.length - 1].weight_kg) + " kg" : ""}</span></div><Chart id="c-wt" cfg={cfg.wt} /></div>
      </div></section>
      <section><h2>Monthly averages</h2>
        <Table><tr><th />{months.map(m => <th key={m} className="num">{fMonth(m)}</th>)}</tr>
          {MONTHLY.map(([k, l, f]) => {
            const mm = byKey(lt.monthly[k] || [], "month");
            return <tr key={k}><td>{MONTHLY_HINT[k] ? <Hint k={MONTHLY_HINT[k]}>{l}</Hint> : l}</td>
              {months.map(m => <td key={m} className="num" title={mm[m] ? mm[m].n + " days" : ""}>{mm[m] ? f(mm[m].mean) : ""}</td>)}</tr>;
          })}
        </Table><p className="note">Hover a value for the number of days behind it. Months with only a few days of data are less reliable.</p></section>
      <section><h2>Events</h2><p className="note">Context the watch can't know. Confirmed illness and alcohol nights are left out of your baselines (scores recompute automatically).</p>
        <Paged head={<tr><th>Kind</th><th>From</th><th>To</th><th>Status</th><th>Note</th><th /></tr>}
          rows={lt.annotations.length ? lt.annotations.map(a => (
            <tr key={a.id}><td>{a.kind}</td><td>{fDay(a.start.slice(0, 10))}</td><td>{a.end ? fDay(a.end.slice(0, 10)) : ""}</td><td>{a.status}</td>
              <td style={{ whiteSpace: "normal" }}>{a.note || ""}</td>
              <td>{a.status !== "confirmed" ? <><button className="btn small" onClick={() => act("confirm", a.id)}>Confirm</button> </> : null}
                {a.status === "suggested" ? <><button className="btn small" onClick={() => act("reject", a.id)}>Reject</button> </> : null}
                <button className="btn small" onClick={() => act("delete", a.id)}>Delete</button></td></tr>
          )) : [<tr key="none"><td colSpan={6} className="note">No events.</td></tr>]} />
        <form className="form" id="addev" style={{ marginTop: 12 }} onSubmit={add}>
          <label>Kind<select name="kind">{kinds.map(k => <option key={k}>{k}</option>)}</select></label>
          <label>From<input type="date" name="start" required max={today()} /></label>
          <label>To (optional)<input type="date" name="end" /></label>
          <label>Note<input name="note" placeholder="what happened" /></label>
          <button className="btn primary cta">Add<span className="ico" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></svg></span></button>
          <span className="note" id="addmsg">{msg}</span>
        </form><p className="note">Alcohol: pick the evening you drank; the night after it is the one left out.</p></section>
      <section><h2>ECG recordings</h2>
        <Paged head={<tr><th>Date</th><th>Time</th><th className="num">Avg HR</th><th>Result</th><th className="num">Length</th></tr>}
          rows={lt.ecg.length ? lt.ecg.map(e => {
            const id = e.reading_id.split("/").pop()!;
            return <tr key={e.reading_id} className="click" data-ecg={id} onClick={() => setEcg(id)}><td>{fDay(e.date)}</td><td>{fClock(e.start_time)}</td>
              <td className="num">{e.avg_bpm}</td><td>{lower(e.classification)}</td><td className="num">{n0(e.duration_sec)} s</td></tr>;
          }) : [<tr key="none"><td colSpan={5} className="note">No ECGs.</td></tr>]} />
        <div id="ecg" style={{ marginTop: 12 }}>{ecg ? <EcgStrips key={ecg} id={ecg} /> : null}</div></section>
      {lt.assessments.length ? <section><h2>Google assessments</h2><p className="note">From Takeout.</p>
        <Paged head={<tr><th>Date</th><th>Assessment</th><th>Result</th></tr>}
          rows={lt.assessments.map((a, i) => <tr key={i}><td>{fDay(a.date)}</td><td>{a.kind.replace(/_/g, " ")}</td><td>{lower(a.classification)}</td></tr>)} />
      </section> : null}
    </>
  );
}

function EcgStrips({ id }: { id: string }) {
  const [e, setE] = useState<Ecg | null>(null);
  useEffect(() => { get<Ecg>(`/ecg/${id}`).then(setE); }, [id]);
  const strips = useMemo(() => {
    if (!e) return [];
    const hz = e.sampling_hz || 250, wf = e.waveform, per = hz * 10;
    const lo = Math.min(...wf), hi = Math.max(...wf), out: ChartConfig<number>[] = [];
    for (let i = 0; i < wf.length; i += per) {
      const s = wf.slice(i, i + per), k = out.length;
      out.push({ x: s.map((_, j) => k * 10 + j / hz), numeric: true, height: 110, y: { min: lo, max: hi, ticks: [], fmt: () => "" },
        xfmt: (v: number) => v.toFixed(0) + " s", series: [{ name: "", type: "line" as const, width: 1, data: s, noTip: true }] });
    }
    return out;
  }, [e]);
  if (!e) return <div className="note">Loading…</div>;
  return (
    <div className="card"><h3>ECG {fDay(e.date)} {fClock(e.start_time)} · {e.avg_bpm} bpm · {lower(e.classification)}</h3>
      {strips.map((cfg, i) => <Chart key={i} id={`ecg-${i}`} cfg={cfg} />)}<p className="note">Three 10-second strips, {e.sampling_hz || 250} Hz.</p></div>
  );
}

// VO2 max detail: one source only, Google's daily value (cardio_fitness), so
// current, lowest and highest always agree with the chart. Run estimates stay
// on the Long-term card; Healthspan averages the same daily values.
function Vo2({ lt, range: [fr, to] }: { lt: LongtermResponse; range: [string, string] }) {
  const [ui] = useUi();
  const week = ui.range === 7;
  const all = lt.vo2max.filter((r): r is typeof r & { vo2_max: number } => r.vo2_max != null);
  const rows = all.filter(r => r.date >= fr && r.date <= to);
  const last = all[all.length - 1], first = rows[0];
  const ext = (cmp: (a: number, b: number) => boolean) => rows.reduce<typeof rows[number] | null>((b, r) => b && !cmp(r.vo2_max, b.vo2_max) ? b : r, null);   // first day reaching it
  const lo = ext((a, b) => a < b), hi = ext((a, b) => a > b);
  const changes = all.map((r, i) => ({ ...r, d: i ? r.vo2_max - all[i - 1].vo2_max : null }))
    .filter((r, i) => r.date >= fr && (i === 0 || Math.abs(r.d!) >= 0.005)).reverse();
  const hsv = lt.healthspan.variants, v = hsv[ui.hsVariant] ? ui.hsVariant : Object.keys(hsv)[0];
  const hs = (hsv[v] || []).find(r => r.metric === "vo2max");
  const stat = (label: ReactNode, r: { vo2_max: number } | null | undefined, sub: ReactNode) =>
    <div className="card"><div className="label">{label}</div><div className="big">{r ? n1(r.vo2_max) : "–"}</div><div className="note">{sub}</div></div>;
  const cfg = useMemo(() => {
    const xs = xDates(fr, to, rows), map = byKey(rows), data = pick(xs, map, r => r.vo2_max);
    return { x: xs, height: 220, y: fitY(data, week), xfmt: fShort, tipx: fDay, shades: shades(lt.annotations),
      series: [{ name: "VO2 max", type: "line" as const, connect: true, noDots: true, data, fmt: n2 }] };
  }, [lt, fr, to, week]);   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <div className="head"><a href="#/longterm" className="note">‹ Long-term</a></div>
      <div className="head"><h1>VO2 max</h1><span className="sub"><Hint k="vo2_source">Google daily estimate</Hint> · ml/kg/min</span><span className="spacer" /><RangeSeg /></div>
      <div className="grid g5 three">
        {stat(<Hint k="vo2max">Current</Hint>, last, last ? <>{fShort(last.date)}{last.cardio_fitness_level ? <> · <Hint k="vo2_level">{lower(last.cardio_fitness_level)}</Hint></> : null}</> : "")}
        {stat(<Hint k="vo2_range">Lowest</Hint>, lo, lo ? fShort(lo.date) : "")}
        {stat(<Hint k="vo2_range">Highest</Hint>, hi, hi ? fShort(hi.date) : "")}
      </div>
      <section><div className="card"><div className="chart-title"><h3>Course</h3><EventNote anns={lt.annotations} />
        <span className="now">{first && last && rows.length > 1 ? sgn(last.vo2_max - first.vo2_max, 1) : ""}</span></div>
        <div className="note csub">{rows.length ? `${rows.length} days since ${fShort(first.date)}; the change on the right is first day to today.` : ""}</div>
        <Chart id="c-vo2d" cfg={cfg} /></div></section>
      <section><h2>Changes</h2><p className="note">Days the value moved.</p>
        <Paged head={<tr><th>Day</th><th className="num">VO2 max</th><th className="num">Change</th><th><Hint k="vo2_level">Level</Hint></th></tr>}
          rows={changes.length ? changes.map(r => (
            <tr key={r.date}><td>{fDay(r.date)}</td><td className="num">{n2(r.vo2_max)}</td><td className="num">{r.d == null ? "first" : sgn(r.d)}</td><td>{lower(r.cardio_fitness_level)}</td></tr>
          )) : [<tr key="none"><td colSpan={4} className="note">No values in this range.</td></tr>]} />
        <p className="note">{hs ? <><Hint k="healthspan">Healthspan</Hint> uses the average of these daily values ({n1(hs.value)} for "{v.replace(/_/g, " ")}"), target {hs.referent}. </> : null}Run-by-run estimates are on the Long-term tab.</p></section>
    </>
  );
}

// Healthspan age history: the value each day's run computed (past days the
// scores didn't run are backfilled by the backend), for the chosen variant.
function HsHistory({ lt, range: [fr, to] }: { lt: LongtermResponse; range: [string, string] }) {
  const [ui, setUi] = useUi();
  const week = ui.range === 7, hs = lt.healthspan, variants = Object.keys(hs.variants);
  const v = variants.includes(ui.hsVariant) ? ui.hsVariant : variants[0];
  const all = hs.history.filter((r): r is typeof r & { value: number } => r.variant === v && r.value != null)
    .map(r => ({ date: r.computed_on, value: r.value }));
  const rows = all.filter(r => r.date >= fr && r.date <= to);
  const last = all[all.length - 1], first = rows[0];
  const ext = (cmp: (a: number, b: number) => boolean) => rows.reduce<typeof rows[number] | null>((b, r) => b && !cmp(r.value, b.value) ? b : r, null);
  const lo = ext((a, b) => a < b), hi = ext((a, b) => a > b);
  const changes = all.map((r, i) => ({ ...r, d: i ? r.value - all[i - 1].value : null })).filter(r => r.date >= fr).reverse();
  const actual = (hs.variants[v] || []).find(r => r.metric === "healthspan_age")?.referent;
  const stat = (label: ReactNode, r: { value: number } | null | undefined, sub: ReactNode) =>
    <div className="card"><div className="label">{label}</div><div className="big">{r ? n1(r.value) : "–"}</div><div className="note">{sub}</div></div>;
  const cfg = useMemo(() => {
    const xs = xDates(fr, to, rows), map = byKey(rows), data = pick(xs, map, r => r.value);
    return { x: xs, height: 220, y: fitY(data, week), xfmt: fShort, tipx: fDay, shades: shades(lt.annotations),
      series: [{ name: "Healthspan age", type: "line" as const, connect: true, noDots: true, data, fmt: n2 }] };
  }, [lt, fr, to, v, week]);   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <div className="head"><a href="#/longterm" className="note">‹ Long-term</a></div>
      <div className="head"><h1>Healthspan age</h1><span className="sub"><Hint k="hs_history">Computed daily</Hint> · years</span><span className="spacer" /><RangeSeg /></div>
      <div className="grid g5 three">
        {stat(<Hint k="healthspan">Current</Hint>, last, last ? <>{fShort(last.date)}{actual != null ? ` · actual ${n0(+actual)}` : ""}</> : "")}
        {stat(<Hint k="hs_range">Youngest</Hint>, lo, lo ? fShort(lo.date) : "")}
        {stat(<Hint k="hs_range">Oldest</Hint>, hi, hi ? fShort(hi.date) : "")}
      </div>
      <section><div className="card"><div className="chart-title"><h3>Course</h3><EventNote anns={lt.annotations} />
        <span className="now">{first && last && rows.length > 1 ? sgn(last.value - first.value, 2) : ""}</span></div>
        <div className="note csub">{rows.length ? `${rows.length} days since ${fShort(first.date)}; the change on the right is first day to today, in years.` : ""}</div>
        <div className="seg hs-variants">
          {variants.map(x => <button key={x} className={x === v ? "on" : ""} onClick={() => setUi({ hsVariant: x })}>{VN[x] || x}</button>)}
        </div>
        <Chart id="c-hsd" cfg={cfg} /></div></section>
      <section><h2>Days</h2>
        <Paged head={<tr><th>Day</th><th className="num">Healthspan age</th><th className="num">Change</th></tr>}
          rows={changes.length ? changes.map(r => (
            <tr key={r.date}><td>{fDay(r.date)}</td><td className="num">{n2(r.value)}</td><td className="num">{r.d == null ? "first" : sgn(r.d)}</td></tr>
          )) : [<tr key="none"><td colSpan={3} className="note">No values in this range.</td></tr>]} /></section>
    </>
  );
}
