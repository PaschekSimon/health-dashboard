// Recovery: the daily score, the signals behind it against your normal range,
// and every night in a table. Click a day for its breakdown.

import { useEffect, useMemo, useRef, useState, type ReactElement } from "react";
import { get } from "../api/client";
import type { ExplainError, ExplainRecovery, RecoveryDay, RecoveryResponse, TrendPoint, TrendResponse } from "../api/types";
import { BandDot, EventNote, Kv, Paged, RangeSeg } from "../components/bits";
import { Chart, ChartBox } from "../components/Chart";
import { Hint } from "../components/Hint";
import { COLORS, addDays, byKey, fDay, fShort, mean, n0, n1, pct, pick, sd, sgn, shades, xDates } from "../lib/format";
import { rangeDates, type Ui } from "../ui";

export async function loadRecovery(_args: string[], _q: URLSearchParams, ui: Ui) {
  const [fr, to] = rangeDates(ui);
  const base = addDays(to, -59) < fr ? addDays(to, -59) : fr;   // 60 days for the normal ranges
  const [rec, nrem] = await Promise.all([get<RecoveryResponse>(`/recovery?from=${base}&to=${to}`),
    get<TrendResponse>(`/trend/nrem_hr?from=${base}&to=${to}`)]);
  return <Recovery fr={fr} to={to} rec={rec} nrem={nrem} />;
}

type Row = RecoveryDay | TrendPoint;
type Sig = [key: string, title: ReactElement, f: (r: Row) => number | null | undefined, unit: string, sub: string, src: Row[]];

// normal = last 30 days with a value and no event, mean ± 1 SD
const normal = (rows: Row[], f: Sig[2]) => {
  const v = rows.filter(r => !(r.annotation_ids || []).length).slice(-30).map(f).filter((x): x is number => x != null);
  const m = mean(v), s = sd(v);
  return m == null || s == null ? null : { lo: m - s, hi: m + s, m };
};

const REC_PARTS: Record<string, string> = { hrv: "HRV", rhr: "Resting HR", sleep: "Sleep", resp: "Breathing rate" };
const PART_HINT: Record<string, string> = { hrv: "hrv", rhr: "rhr", resp: "breathing", sleep: "sleep_perf" };
const HRV_SRC: Record<string, string> = { google_night: "Google night average", last_deep: "last deep-sleep phase",
  deep_median: "median of deep sleep", sleep_median: "median of sleep" };

function Recovery({ fr, to, rec, nrem }: { fr: string; to: string; rec: RecoveryResponse; nrem: TrendResponse }) {
  const [day, setDay] = useState<{ d: string; n: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const show = (d: string) => setDay(x => ({ d, n: (x?.n ?? 0) + 1 }));
  useEffect(() => { if (day) box.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }, [day]);

  const all = rec.days, shown = all.filter(r => r.date >= fr);
  const last = shown[shown.length - 1];
  const sig: Sig[] = [
    ["hrv", <Hint k="hrv">HRV</Hint>, r => (r as RecoveryDay).google_rmssd ?? (r as RecoveryDay).hrv_ms, "ms", "dashed: deep-sleep HRV", all],
    ["rhr", <Hint k="rhr">Resting heart rate</Hint>, r => (r as RecoveryDay).rhr, "bpm", "", all],
    ["nrem", <Hint k="nrem_hr">Non-REM heart rate</Hint>, r => (r as TrendPoint).value, "bpm", "", nrem.points],
    ["resp", <Hint k="breathing">Breathing rate</Hint>, r => (r as RecoveryDay).breathing, "/min", "", all],
    ["spo2", <Hint k="spo2">SpO2</Hint>, r => (r as RecoveryDay).spo2, "%", "", all],
    ["skin", <Hint k="skin_temp">Skin temperature</Hint>, r => (r as RecoveryDay).skin_rel, "°C", "", all],
  ];

  const charts = useMemo(() => {
    const map = byKey(all), nmap = byKey(nrem.points);
    const xs = xDates(fr, to, shown);
    const sh = shades(rec.annotations);
    const recCfg = {
      x: xs, height: 190, y: { min: 0, max: 100, ticks: [0, 33, 66, 100], fmt: (v: number) => v + "%" }, shades: sh, xfmt: fShort, tipx: fDay,
      series: [{ name: "Recovery", type: "bar" as const, data: pick(xs, map, r => r.recovery), colors: xs.map(d => map[d] ? COLORS[map[d].band] : null), fmt: pct }],
      onClick: (i: number) => { if (map[xs[i]]) show(xs[i]); },
    };
    const sigCfg = Object.fromEntries(sig.map(([k, , f, u, , src]) => {
      const m: Record<string, Row> = k === "nrem" ? nmap : map;
      return [k, {
        x: xs, height: 140, band: normal(src, f), shades: sh, xfmt: fShort, tipx: fDay,
        series: [{ name: k === "hrv" ? "Google" : u, type: "line" as const, data: pick(xs, m, f), fmt: k === "skin" ? ((v: number | null) => sgn(v)) : n1 },
          ...(k === "hrv" ? [{ name: "Deep sleep", type: "line" as const, dash: true, noDots: true, color: "var(--ink-2)", data: pick(xs, map, r => r.deep_hrv_ms), fmt: n1 }] : [])],
      }];
    }));
    return { recCfg, sigCfg };
  }, [rec, nrem, fr, to]);   // eslint-disable-line react-hooks/exhaustive-deps

  const rows = shown.slice().reverse();
  return (
    <>
      <div className="head"><h1>Recovery</h1><span className="spacer" /><RangeSeg /></div>
      <div className="card">
        <div className="chart-title"><h3><Hint k="recovery">Recovery</Hint></h3><EventNote anns={rec.annotations} />
          <span className="now">{last ? <><BandDot band={last.band} />{Math.round(last.recovery) + "%"}</> : ""}</span></div>
        <Chart id="c-rec" cfg={charts.recCfg} /><div className="note">Click a day to see why.</div>
      </div>
      <div id="explain" style={{ marginTop: 16 }} ref={box}>{day ? <Explain key={day.n} date={day.d} /> : null}</div>
      <section><h2>Signals against your normal range</h2>
        <p className="note">Shaded band: your normal (mean ± 1 SD of the last 30 nights without illness or alcohol).</p>
        <div className="grid g3">{sig.map(([k, t, f, u, sub, src]) => {
          const lv = src.filter(r => r.date >= fr).slice(-1)[0], v = lv ? f(lv) : null;
          return <ChartBox key={k} id={"c-" + k} title={t} now={v != null ? `${k === "skin" ? sgn(v) : n1(v)} ${u}` : ""} sub={sub} cfg={charts.sigCfg[k]} />;
        })}</div>
      </section>
      <section><h2>Days</h2>
        <Paged head={<tr><th>Night</th><th className="num"><Hint k="recovery">Recovery</Hint></th><th className="num"><Hint k="hrv">HRV</Hint></th>
          <th className="num"><Hint k="deep_hrv">Deep HRV</Hint></th><th className="num"><Hint k="rhr">RHR</Hint></th><th className="num"><Hint k="breathing">Breathing</Hint></th>
          <th className="num"><Hint k="spo2">SpO2</Hint></th><th className="num"><Hint k="skin_temp">Skin °C</Hint></th><th className="num"><Hint k="sleep_perf">Sleep perf.</Hint></th>
          <th><Hint k="check">Check</Hint></th><th><Hint k="hrv_status">HRV status</Hint></th></tr>}
          rows={rows.map(r => (
            <tr key={r.date} className="click" data-d={r.date} onClick={() => show(r.date)}>
              <td>{fDay(r.date)}</td><td className="num"><BandDot band={r.band} />{`${Math.round(r.recovery)}%`}</td><td className="num">{n1(r.google_rmssd ?? r.hrv_ms)}</td>
              <td className="num">{n1(r.deep_hrv_ms)}</td><td className="num">{n0(r.rhr)}</td><td className="num">{n1(r.breathing)}</td><td className="num">{n1(r.spo2)}</td>
              <td className="num">{sgn(r.skin_rel)}</td><td className="num">{pct(r.sleep_performance)}</td><td>{r.daily_check_flag || ""}</td><td>{r.hrv_status || ""}</td>
            </tr>
          ))} />
      </section>
    </>
  );
}

// one day's Recovery broken into its signals
function Explain({ date }: { date: string }) {
  const [e, setE] = useState<ExplainRecovery | ExplainError | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { get<ExplainRecovery | ExplainError>(`/explain/recovery?date=${date}`).then(setE, x => setErr(x.message)); }, [date]);
  if (err) return <div className="note">{err}</div>;
  if (!e) return <div className="note">Loading…</div>;
  if (e.error) return <div className="note">{e.error}</div>;
  const x = e as ExplainRecovery;
  return (
    <div className="panel"><h3>{`${fDay(date)}: `}<BandDot band={x.band} />{`${Math.round(x.value)}% recovery`}</h3>
      <p className="note">Each signal is compared with your own baseline (last {n0(x.baseline_n)} nights, illness and alcohol nights left out).</p>
      <div className="tablewrap"><table><tbody>
        <tr><th>Signal</th><th className="num"><Hint k="z">vs. normal (z)</Hint></th><th className="num">Weight</th><th className="num"><Hint k="contribution">Contribution</Hint></th></tr>
        {Object.entries(x.components).map(([k, c]) => (
          <tr key={k}><td><Hint k={PART_HINT[k] || k}>{REC_PARTS[k] || k}</Hint></td><td className="num">{sgn(c.z)}</td><td className="num">{c.weight ?? "–"}</td><td className="num">{sgn(c.contribution, 3)}</td></tr>
        ))}
      </tbody></table></div>
      <div className="cols" style={{ marginTop: 10 }}>
        <Kv k="HRV used">{n1(x.raw.hrv_ms) + " ms (" + (HRV_SRC[x.raw.hrv_source || ""] || x.raw.hrv_source || "") + ")"}</Kv>
        <Kv k="Deep-sleep HRV">{n1(x.raw.deep_hrv_ms) + " ms (not used)"}</Kv>
        <Kv k="Resting HR">{n0(x.raw.rhr)}</Kv>
        <Kv k="Breathing">{n1(x.raw.breathing)}</Kv>
        <Kv k="SpO2">{n1(x.raw.spo2) + "%"}</Kv>
        <Kv k="Skin temp">{sgn(x.raw.skin_rel) + " °C"}</Kv>
      </div>
      {x.penalty ? <p className="note">Penalty {x.penalty}{x.flags ? " · " + x.flags : ""}</p> : null}
      {x.provisional ? <p className="note">Provisional: the baseline is still short.</p> : null}
      {x.excluded_event_night ? <p className="note">Event night: not used in future baselines.</p> : null}
    </div>
  );
}

