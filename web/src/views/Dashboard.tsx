// Dashboard: one day's Recovery, last night's sleep, Strain against its
// target, steps and this week's running. #/dashboard/<date>, today by default.

import { get, prefetch } from "../api/client";
import type { DashboardResponse } from "../api/types";
import { Kv, Ring } from "../components/bits";
import { Hint } from "../components/Hint";
import { STEP_GOAL, addDays, fClock, fDay, fHM, n0, n1, pct, today } from "../lib/format";

const ZN: Record<string, string> = { rhr: "resting HR", ln_rmssd: "HRV", nrem_hr: "non-REM HR", breathing: "breathing rate",
  skin_temp: "skin temperature", sleep_min: "sleep time", steps: "steps" };

export async function loadDashboard(args: string[]) {
  const d = args[0] || today();
  const dash = await get<DashboardResponse>(`/dashboard?date=${d}`);
  prefetch(`/dashboard?date=${addDays(d, -1)}`, ...(d < today() ? [`/dashboard?date=${addDays(d, 1)}`] : []));
  return <Dashboard d={d} dash={dash} />;
}

function Dashboard({ d, dash }: { d: string; dash: DashboardResponse }) {
  const rec = dash.recovery, sp = dash.sleep.performance, sg = dash.sleep.google;
  const st = dash.strain.today, tgt = dash.strain.target, act = dash.google_daily_activity;
  const rw = dash.running_week;
  const dc = dash.daily_check;
  const isToday = d === today();
  const go = (x: string) => { location.hash = `#/dashboard/${x}`; };

  const off = dc ? Object.entries(dc.z).filter(([, v]) => v > 1.5).map(([k]) => ZN[k] || k) : [];
  const warn = dc && dc.warning ? (
    <div className={"warn" + (dc.flag === "AMBER" ? " amber" : "")}>
      <b>{dc.flag === "RED" ? "Several recovery markers are off" : "Some recovery markers are off"}.</b> Worse than your baseline: {off.join(", ") || "several signals"}
      {dc.excluded_event ? " (event night, left out of your baseline)" : ""}. <a href="#/recovery">Details</a>
    </div>
  ) : null;

  const recCard = rec ? (
    <a className="card score hero" href="#/recovery">
      <div className="label"><Hint k="recovery">Recovery</Hint></div>
      <div className="top">
        <Ring value={rec.recovery} max={100} cls={rec.band ?? undefined}><div className="big">{rec.recovery != null ? Math.round(rec.recovery) : "–"}<small>%</small></div></Ring>
        <div className="side-kv">
          <Kv k={<Hint k="hrv">HRV</Hint>}>{n0(rec.google_rmssd ?? rec.hrv_ms) + " ms"}</Kv>
          <Kv k={<Hint k="rhr">Resting HR</Hint>}>{n0(rec.rhr) + " bpm"}</Kv>
          <Kv k={<Hint k="breathing">Breathing</Hint>}>{n1(rec.breathing) + " /min"}</Kv>
        </div>
      </div>
      {rec.provisional ? <div className="note">Provisional: baseline still short</div> : null}
    </a>
  ) : (
    <div className="card score hero">
      <div className="label"><Hint k="recovery">Recovery</Hint></div>
      <div className="top"><Ring value={null} max={100}><div className="big">–</div></Ring><div className="side-kv note">No score for this day yet.</div></div>
    </div>
  );

  const asleep = sp?.asleep_min ?? sg?.total_minutes;
  const naps = dash.sleep.naps ?? [];
  const sleepCard = (sp || sg) ? (
    <a className="card score" href="#/sleep">
      <div className="label">Last night · <Hint k="sleep_perf">Sleep Performance</Hint></div>
      <div className="top">
        <Ring value={sp?.sleep_performance} max={100} cls="data">
          <div className="big">{sp?.sleep_performance != null ? <>{Math.round(sp.sleep_performance)}<small>%</small></> : "–"}</div>
        </Ring>
        <div className="side-kv">
          <Kv k="Asleep">{fHM(asleep)}</Kv>
          <Kv k={<Hint k="need">Need</Hint>}>{fHM(sp?.need_min)}</Kv>
          <Kv k="Bed / wake">{sg ? `${fClock(sg.start_time)} – ${fClock(sg.end_time)}` : "–"}</Kv>
          {naps.map(n => <Kv key={n.start_time} k={<Hint k="naps">Nap</Hint>}>{`${fClock(n.start_time)} – ${fClock(n.end_time)}`}</Kv>)}
        </div>
      </div>
    </a>
  ) : (
    <div className="card score">
      <div className="label">Last night</div>
      <div className="top"><Ring value={null} max={100}><div className="big">–</div></Ring><div className="side-kv note">No sleep recorded.</div></div>
    </div>
  );

  const strainCard = (
    <a className="card score" href="#/training">
      <div className="label"><Hint k="strain">Strain</Hint> {isToday ? "today" : ""}</div>
      <div className="top">
        <Ring value={st?.strain} max={21} cls="data" target={tgt}><div className="big">{st ? n1(st.strain) : "–"}<small className="under">/ 21</small></div></Ring>
        <div className="side-kv"><Kv k={<Hint k="strain_target">Target</Hint>}>{tgt ? `${tgt.low}–${tgt.high} · ${tgt.label}` : "needs a recovery score"}</Kv></div>
      </div>
      {st?.partial ? <div className="note">Day still running</div> : null}
    </a>
  );

  const steps = dash.steps;
  const stepsCard = (
    <a className="card wide" href={`#/activity/${d}`}>
      <div className="label">Steps</div>
      <div className="big">{n0(steps)}</div>
      <div className="meter"><i style={{ width: `${Math.min(100, (steps || 0) / STEP_GOAL * 100)}%` }} /></div>
      <div className="kvs">
        <Kv k="Goal">{n0(STEP_GOAL)}</Kv>
        <Kv k="Distance">{act?.distance_km != null ? n1(act.distance_km) + " km" : "–"}</Kv>
        <Kv k="Floors">{n0(act?.floors)}</Kv>
      </div>
    </a>
  );

  const runCard = (
    <a className="card wide" href="#/training">
      <div className="label">Running this week</div>
      <div className="big">{n1(rw.km)}<small> km</small></div>
      <div className="kvs">
        <Kv k="Runs">{rw.runs}</Kv>
        <Kv k="Time">{fHM(rw.minutes)}</Kv>
        <Kv k={<Hint k="easy">Easy (Z1–Z2)</Hint>}>{rw.easy_pct != null ? pct(rw.easy_pct) + " of run time" : "–"}</Kv>
      </div>
    </a>
  );

  return (
    <>
      <div className="head">
        <h1>{isToday ? "Today" : fDay(d)}</h1><span className="sub">{isToday ? fDay(d) : ""}</span><span className="spacer" />
        <div className="datenav">
          <button className="btn small" id="prev" onClick={() => go(addDays(d, -1))}>‹</button>
          <input type="date" id="day" value={d} max={today()} onChange={e => e.target.value && go(e.target.value)} />
          <button className="btn small" id="next" disabled={d >= today()} onClick={() => go(addDays(d, 1))}>›</button>
          <button className="btn small" id="tod" style={isToday ? { visibility: "hidden" } : undefined} tabIndex={isToday ? -1 : undefined}
            onClick={() => { location.hash = "#/dashboard"; }}>Today</button>
        </div>
      </div>
      {warn}
      <div className="dash">{recCard}{sleepCard}{strainCard}{stepsCard}{runCard}</div>
      {dash.annotations.length ? (
        <div className="note events-note">Events: {dash.annotations.map(a => `${a.kind}${a.note ? " · " + a.note : ""}`).join("; ")}</div>
      ) : null}
    </>
  );
}
