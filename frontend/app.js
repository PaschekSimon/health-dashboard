// Health web app: six tabs (Dashboard, Recovery, Sleep, Activity, Training,
// Long-term) plus a raw Data browser, all on the local API served by the same
// FastAPI process (backend/app.py). Plain ES modules, no build step.

import { chart, hypnogram, legend, route as drawRoute } from "./charts.js";

const view = document.getElementById("view");
const COLORS = { green: "var(--green)", yellow: "var(--yellow)", red: "var(--red)" };
const STEP_GOAL = 8000;

// ------------------------------------------------------------------ api ----

async function fetchJson(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} → ${r.status} ${await r.text()}`);
  return r.json();
}

// Reads are kept for two minutes so stepping through days (and back) answers
// at once; a sync, recompute or edit clears them (render(true) → cache.clear).
const cache = new Map();
function get(path) {
  const hit = cache.get(path);
  if (hit && Date.now() - hit.t < 120000) return hit.p;
  const p = fetchJson(path);
  cache.set(path, { t: Date.now(), p });
  p.catch(() => cache.delete(path));
  return p;
}
// Fixed lists from the backend (annotation kinds, sync steps by job reason),
// fetched once per page load; null if the call failed.
const META = fetchJson("/meta").catch(() => null);
let meta = null;
META.then(m => { meta = m; });

const prefetch = (...paths) => setTimeout(() => paths.forEach(p => get(p).catch(() => {})), 300);

async function send(method, path, body) {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) throw new Error(`${method} ${path} → ${r.status} ${await r.text()}`);
  return r.json();
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

// ------------------------------------------------------------ formatting ----

const pad2 = n => String(n).padStart(2, "0");
const iso = d => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
const today = () => iso(new Date());
const D = s => new Date(s + "T12:00:00");
const addDays = (s, n) => { const d = D(s); d.setDate(d.getDate() + n); return iso(d); };
const mondayOf = s => { const d = D(s); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return iso(d); };
const fDay = s => s ? D(s).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" }) : "–";
const fShort = s => s ? D(s).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "–";
// a Monday as its Mon–Sun range: "19–25 Oct", "28 Sept – 4 Oct"
const fWeek = s => {
  const e = addDays(s, 6);
  return s.slice(0, 7) === e.slice(0, 7) ? `${D(s).getDate()}–${fShort(e)}` : `${fShort(s)} – ${fShort(e)}`;
};
const fMonth = s => new Date(s + "-15T12:00:00").toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
const fClock = t => t ? new Date(t).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "–";
const fHM = min => { if (min == null) return "–"; const m = Math.round(min); return `${Math.floor(m / 60)}h ${pad2(m % 60)}m`; };
const fH = min => min == null ? "–" : (min / 60).toFixed(1) + " h";
const fPace = s => { if (!s) return "–"; s = Math.round(s); return `${Math.floor(s / 60)}:${pad2(s % 60)}`; };
const fDur = s => { if (s == null) return "–"; s = Math.round(s); return s >= 3600 ? `${Math.floor(s / 3600)}:${pad2(Math.floor(s % 3600 / 60))}:${pad2(s % 60)}` : `${Math.floor(s / 60)}:${pad2(s % 60)}`; };
const PACED = new Set(["RUNNING", "WALKING", "HIKING"]);
const n0 = v => v == null ? "–" : Math.round(v).toLocaleString("en-GB");
const n1 = v => v == null ? "–" : (+v).toFixed(1);
const n2 = v => v == null ? "–" : (+v).toFixed(2);
const sgn = (v, d = 2) => v == null ? "–" : (v > 0 ? "+" : "") + (+v).toFixed(d);
const pct = v => v == null ? "–" : Math.round(v) + "%";
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ago = t => {
  if (!t) return "never";
  const m = Math.round((Date.now() - new Date(t)) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 48 * 60) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} days ago`;
};
const mean = a => { const v = a.filter(x => x != null); return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null; };
const sd = a => { const v = a.filter(x => x != null), m = mean(v); return v.length > 2 ? Math.sqrt(v.reduce((s, x) => s + (x - m) ** 2, 0) / (v.length - 1)) : null; };

// ---------------------------------------------------------- explanations ----
// A hint label explains its metric on hover (or keyboard focus): what it is,
// then how to read its values. One shared bubble, found by event delegation.

const INFO = {
  recovery: ["How ready your body is today. Last night's HRV (50 %), resting heart rate (25 %), sleep (15 %) and breathing rate (10 %), each compared with your own normal of the last 30 nights.",
    "67–100 % green: well recovered, hard training is fine. 34–66 % yellow: train moderately. 0–33 % red: your body is under strain, keep it easy."],
  hrv: ["Heart rate variability: how much the time between heartbeats varies (RMSSD), averaged over the night, as in the Google app. A sign of how relaxed your nervous system is.",
    "Compare it with your own normal, not with other people. Above your normal: well recovered. A clear drop often follows hard training, alcohol, illness, stress or short sleep."],
  deep_hrv: ["HRV measured only in deep-sleep phases. Shown for comparison; Recovery uses the night average.",
    "Usually higher than the night average. It depends on how the watch staged your sleep, so it jumps around more."],
  rhr: ["Resting heart rate: your lowest steady heart rate, measured mostly while you sleep.",
    "Lower than your normal is good. 3–5 bpm or more above it can mean fatigue, alcohol, stress or an illness coming on."],
  nrem_hr: ["Average heart rate during non-REM (light and deep) sleep, from Google.",
    "Lower is better. It rises after alcohol, late meals, hard training or when you're getting ill."],
  breathing: ["Breaths per minute while you sleep.",
    "Very stable from night to night, usually 12–20. One breath per minute or more above your normal can be an early sign of illness."],
  spo2: ["Blood oxygen saturation, averaged during sleep.",
    "95–100 % is normal. Repeated values under about 92 % can point to breathing problems during sleep."],
  skin_temp: ["Skin temperature during sleep compared with your own baseline.",
    "Around 0 is normal. +0.5 °C or more can come with illness, alcohol, late meals or a warm room; below 0 is usually a cool room. It lowers Recovery when it is clearly raised."],
  z: ["How far last night was from your normal, in standard deviations. Signs are flipped where lower is better, so positive always means better than usual.",
    "Between −1 and +1: an ordinary night. Beyond ±2: unusual."],
  contribution: ["z × weight: how much this signal pushed the score up (+) or down (−)."],
  check: ["Daily check: flags nights where several recovery markers are worse than your baseline.",
    "RED: several markers clearly off, often illness or alcohol. AMBER: some off. Empty: nothing unusual."],
  hrv_status: ["Your 7-night HRV average against your normal range.",
    "within: normal. below: HRV trending down, often accumulated fatigue, stress or illness. above: unusually high, usually fine."],
  sleep_perf: ["Sleep Performance: how well you slept compared with what your body needed. 60 % sufficiency (hours asleep vs. need), 15 % consistency, 15 % efficiency, 10 % calm.",
    "85 %+ optimal, 70–84 % sufficient, under 70 % poor."],
  need: ["Sleep need for this night: your personal baseline, plus extra after a high-strain day, plus part of the sleep debt from the last three nights, minus naps.",
    "Sleeping close to it keeps Sleep Performance high; a long run of short nights builds debt that raises it."],
  sufficiency: ["Hours asleep as a share of your sleep need, capped at 100 %. 60 % of Sleep Performance.",
    "100 % means you slept at least what you needed."],
  consistency: ["How regular your sleep is: the chance of being asleep or awake at the same clock time as on the previous days, 0–100.",
    "80+ very regular, 60–80 fair, under 60 irregular. A steady rhythm improves sleep quality."],
  efficiency: ["Share of the time in bed you were actually asleep.",
    "85 %+ is good; lower means long or many wake-ups."],
  calm: ["Share of sleep without stress signs (heart rate clearly above your usual sleep heart rate, or very low HRV).",
    "Close to 100 is a calm night; alcohol, late meals or illness lower it."],
  stages: ["Time in each sleep stage as staged by the watch. Deep sleep restores the body, REM consolidates memory, light sleep is most of the night.",
    "Typical for adults: deep 13–23 %, REM 20–25 %, light about 50–60 %. The watch's staging is an estimate."],
  google_sleep: ["Google's own sleep score, 0–100, from duration, stages and restlessness. Only in the Takeout export.",
    "90+ excellent, 80–89 good, 60–79 fair, under 60 poor."],
  strain: ["Strain: the day's cardiovascular load on a 0–21 scale, from the time your heart rate was raised. It is logarithmic: each point higher takes more effort than the last.",
    "0–9 light, 10–13 moderate, 14–17 high, 18–21 all out."],
  strain_target: ["The strain range that suits today's Recovery.",
    "Green: 14–18, hard training possible. Yellow: 10–14, moderate. Red: up to 12, easy only (Z1–Z2)."],
  easy: ["Share of run time at or below the top of Z2 (about 148 bpm for you): easy, aerobic running.",
    "Around 80 % easy is the usual target for building endurance; much lower means most runs are hard."],
  zones: ["Time in each heart-rate zone, from Google's zones. Z1–Z2 easy aerobic, Z3–Z4 hard, Z5 near your maximum.",
    "For endurance, most time belongs in Z1–Z2."],
  trimp: ["TRIMP (training impulse): a workout's load from its duration and how high your heart rate was.",
    "Higher means a harder session. Useful for comparing your own workouts and weeks."],
  drift: ["Aerobic decoupling: how much your heart rate rose relative to your pace from the first half to the second half.",
    "Under 5 %: steady, you are fit for this length and pace. Over 5 %: you tired, it was hot, or the pace was too fast for that distance."],
  ef: ["Efficiency factor: your speed divided by your heart rate (metres per minute per beat).",
    "Higher means faster at the same heart rate. Compare similar easy runs over weeks; it rises as aerobic fitness improves."],
  hrr: ["Heart-rate recovery: how many beats your heart rate dropped after you stopped.",
    "Higher is better. Over about 20 bpm in the first minute is good, under 12 is low."],
  steady: ["Whether the run was continuous enough (no long stops) to judge drift and efficiency fairly."],
  split_cv: ["How much your kilometre paces varied from each other.",
    "Under about 5 % is an even pace."],
  climb: ["Elevation gain per kilometre.",
    "Hills raise heart rate at the same pace, so drift and EF compare best between flat runs."],
  cadence: ["Steps per minute.",
    "Most runners land between 160 and 180 spm. A slightly higher cadence with shorter steps often lowers the impact on knees and shins."],
  stride: ["Average length of one step.", "Longer at higher speed; overstriding (landing far in front of you) brakes you."],
  vert_osc: ["How far your body bounces up and down with each step.",
    "Lower is more economical; about 6–10 cm is typical."],
  vert_ratio: ["Vertical oscillation divided by stride length: how much of your effort goes up instead of forward.",
    "Lower is better; under about 8 % is efficient."],
  gct: ["Ground contact time: how long each foot stays on the ground per step.",
    "Shorter is usually more efficient; 200–300 ms is typical and it drops as you run faster."],
  fitness: ["Fitness: your long-term training load, an average of daily TRIMP over about 42 days.",
    "Rises slowly with steady training and falls slowly when you stop."],
  fatigue: ["Fatigue: your short-term training load, averaged over about 7 days.",
    "Jumps after hard days and falls quickly with rest."],
  form: ["Form: fitness minus fatigue.",
    "Positive: fresh and rested. 0 to −10: normal training. Below about −20: heavy load, take care not to overdo it."],
  acwr: ["Acute:chronic workload ratio: the last week's load divided by your average weekly load over the last four weeks (Google).",
    "0.8–1.3 (shaded) is the sweet spot. Above 1.5 is a sudden jump in load with higher injury risk; below 0.8 you're doing less than you're used to."],
  azm: ["Active Zone Minutes: minutes with your heart rate in the fat-burn zone or higher. Cardio and peak minutes count double.",
    "The health guideline is 150 per week, about 22 a day."],
  active_min: ["Minutes the watch classed as light, moderate or vigorous movement.",
    "Moderate plus vigorous is what counts for health; 150 moderate or 75 vigorous minutes a week is the guideline."],
  sedentary: ["Time awake spent sitting or lying without moving much.",
    "Less is better; breaking up long sitting with short walks helps even if you train."],
  sri: ["Sleep regularity index: how consistently you're asleep or awake at the same clock times, 0–100.",
    "80+ very regular, under 60 irregular."],
  healthspan: ["An estimate of your body's age from VO2 max, resting heart rate, activity, strength training and sleep, based on how each relates to life expectancy in large studies.",
    "Below your actual age: healthier than average for your age. Above: room to improve. It moves slowly."],
  pace_of_aging: ["How fast your healthspan age is changing right now, from the last 30 days compared with all your data.",
    "1.0×: one year per year. Under 1: aging slower, recent habits are better. Over 1: faster."],
  hs_years: ["Years this factor adds to (+) or takes off (−) your healthspan age, from how far your average is from the target."],
  vo2max: ["VO2 max: the most oxygen your body can use per kilogram per minute. The strongest single marker of cardio fitness.",
    "Higher is better. For men aged 20–29, about 48 is average and 55+ is excellent. It rises with endurance training, slowly over months."],
  vo2_source: ["Google's daily VO2 max (cardio fitness score), the value the Fitbit app shows. It mostly moves after outdoor runs, so it stays flat between them.",
    "Healthspan uses the average of these daily values over its period, so it reads lower than today's value while VO2 max is rising."],
  vo2_level: ["Google's cardio fitness level: your VO2 max compared with people of your age and sex.",
    "From low to high: poor, fair, average, good, very good, excellent."],
  vo2_range: ["Lowest and highest daily value in the selected range, with the first day it was reached."],
};
const hint = (key, label) => `<span class="hint" data-hint="${key}" tabindex="0">${label}</span>`;

const hintBox = document.createElement("div");
hintBox.className = "hintbox";
hintBox.setAttribute("role", "tooltip");
document.body.appendChild(hintBox);
let hintOn = null;

function showHint(t) {
  const [what, vals] = INFO[t.dataset.hint] || [];
  if (!what) return;
  hintOn = t;
  hintBox.innerHTML = `<p>${what}</p>${vals ? `<p class="vals">${vals}</p>` : ""}`;
  const r = t.getBoundingClientRect(), b = hintBox.getBoundingClientRect();
  const x = Math.max(12, Math.min(innerWidth - b.width - 12, r.left + r.width / 2 - b.width / 2));
  const y = r.bottom + 10 + b.height > innerHeight - 12 ? r.top - b.height - 10 : r.bottom + 10;
  hintBox.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  hintBox.classList.add("on");
}
function hideHint() { hintOn = null; hintBox.classList.remove("on"); }
document.addEventListener("mouseover", e => {
  const t = e.target.closest?.("[data-hint]");
  if (t) { if (t !== hintOn) showHint(t); } else if (hintOn) hideHint();
});
document.addEventListener("focusin", e => { const t = e.target.closest?.("[data-hint]"); t ? showHint(t) : hideHint(); });
document.addEventListener("scroll", () => hintOn && hideHint(), { passive: true, capture: true });

function dateList(fr, to) {
  const out = [];
  for (let d = fr; d <= to; d = addDays(d, 1)) out.push(d);
  return out;
}

// x axis from the first date that has data (so "All" doesn't start in January)
function xDates(fr, to, rows, key = "date") {
  const have = rows.map(r => r[key]).filter(Boolean).sort();
  return dateList(have.length && have[0] > fr ? have[0] : fr, to);
}

const byKey = (rows, key = "date") => Object.fromEntries(rows.map(r => [r[key], r]));
const pick = (xs, map, f) => xs.map(d => { const r = map[d]; if (!r) return null; const v = f(r); return v ?? null; });

function shades(annotations) {
  return (annotations || []).filter(a => a.status !== "rejected").map(a => {
    let from = a.start.slice(0, 10), to = (a.end || a.start).slice(0, 10);
    if (a.target && /^(night|day):/.test(a.target)) from = to = a.target.split(":")[1].slice(0, 10);
    else if (a.excludes_nights) [from, to] = a.excludes_nights;  // alcohol without target: the night after
    return { from, to, label: `${a.kind}${a.note ? ": " + a.note : ""}` };
  });
}

const eventNote = anns => {
  const kinds = [...new Set((anns || []).filter(a => a.status !== "rejected").map(a => a.kind))];
  return kinds.length ? `<span class="note">Shaded: ${kinds.map(esc).join(", ")}</span>` : "";
};

// ---------------------------------------------------------------- state ----

const store = {
  get(k, d) { try { const v = localStorage.getItem("health." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("health." + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const state = { range: store.get("range", 30), runsOnly: store.get("runsOnly", false), hsVariant: store.get("hsVariant", "all") };

function rangeDates() {
  const to = today();
  return [state.range ? addDays(to, -(state.range - 1)) : "2000-01-01", to];
}

function rangeSeg() {
  return `<div class="seg" id="range">${[[7, "7D"], [30, "30D"], [90, "90D"], [0, "All"]]
    .map(([v, l]) => `<button data-v="${v}" class="${state.range === v ? "on" : ""}">${l}</button>`).join("")}</div>`;
}

function bindRange() {
  view.querySelectorAll("#range button").forEach(b => b.onclick = () => {
    state.range = +b.dataset.v; store.set("range", state.range); render();
  });
}

// --------------------------------------------------------------- router ----

const TABS = [["dashboard", "Dashboard"], ["recovery", "Recovery"], ["sleep", "Sleep"], ["activity", "Activity"],
  ["training", "Training"], ["longterm", "Long-term"]];
const VIEWS = {};
let seq = 0;

function parseHash() {
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  return { tab: parts[0] || "dashboard", args: parts.slice(1), q: new URLSearchParams(query || "") };
}

// The nav is built once; a tab change only moves the classes and slides the
// indicator pill (.ind) under the new link, so nothing in the bar re-renders.
const tabsEl = document.getElementById("tabs");
tabsEl.innerHTML = '<span class="ind" aria-hidden="true"></span>' +
  TABS.map(([k, l], i) => `<a href="#/${k}" data-tab="${k}" style="--i:${i}">${l}</a>`).join("") +
  `<span class="sep" aria-hidden="true"></span><a href="#/data" data-tab="data" style="--i:${TABS.length}" title="Every table, raw">Data</a>`;
const ind = tabsEl.querySelector(".ind");

function moveIndicator() {
  const on = tabsEl.querySelector("a.on");
  if (!on || !on.offsetWidth) { ind.style.opacity = 0; return; }
  ind.style.opacity = 1;
  ind.style.width = on.offsetWidth + "px";
  ind.style.transform = `translateX(${on.offsetLeft}px)`;
}

function paintTabs(tab) {
  tabsEl.querySelectorAll("a[data-tab]").forEach(a => {
    const on = a.dataset.tab === tab;
    a.classList.toggle("on", on);
    on ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current");
  });
  moveIndicator();
  document.getElementById("topbar-title").textContent = (TABS.find(([k]) => k === tab) || ["", tab === "data" ? "Data" : "Dashboard"])[1];
  setMenu(false);
}
// place it without travel first, then let later moves glide; re-place when the font or width changes
document.fonts?.ready.then(moveIndicator);
window.addEventListener("resize", moveIndicator);
requestAnimationFrame(() => requestAnimationFrame(() => tabsEl.classList.add("ready")));

// phone: the island's burger morphs into an X and opens a full-screen menu
const island = document.getElementById("island"), menuBtn = document.getElementById("menu-btn");
function setMenu(open) {
  island.classList.toggle("open", open);
  document.body.classList.toggle("menu-open", open);
  menuBtn.setAttribute("aria-expanded", open);
  menuBtn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
}
menuBtn.onclick = () => setMenu(!island.classList.contains("open"));
document.addEventListener("keydown", e => { if (e.key === "Escape") setMenu(false); });

// A navigation keeps the current view on screen until the next one has its
// data, then swaps in one step. A new tab or page arrives with a short fade
// from the top; a change within the page (another day, range or filter)
// swaps in place: same scroll position, no fade, focus kept. Only a slow load
// dims the old view; only the very first load shows "Loading…".
// render(true) is a refresh after new data: cache dropped, scroll kept.
let shownTab = null, shownArgs = null;

// List tables (data-pages="key") show their newest 10 rows; arrows below page
// to older ones. The page is kept while you stay on the view.
const PAGE = 10, pages = {};
function paginate(root) {
  root.querySelectorAll("table[data-pages]").forEach(t => {
    const key = t.dataset.pages, rows = [...t.rows].slice(1);
    if (rows.length <= PAGE) return;
    const n = Math.ceil(rows.length / PAGE);
    const nav = document.createElement("div");
    nav.className = "pager";
    nav.innerHTML = '<span class="note"></span><button class="btn small" data-d="-1" aria-label="Newer entries">‹</button><button class="btn small" data-d="1" aria-label="Older entries">›</button>';
    t.closest(".tablewrap").after(nav);
    const [label, newer, older] = nav.children;
    const show = p => {
      p = pages[key] = Math.max(0, Math.min(n - 1, p));
      const end = Math.min(rows.length, (p + 1) * PAGE);
      rows.forEach((r, i) => { r.hidden = Math.floor(i / PAGE) !== p; r.classList.toggle("lastv", i === end - 1); });
      label.textContent = `${p * PAGE + 1}–${Math.min(rows.length, (p + 1) * PAGE)} of ${rows.length}`;
      newer.disabled = p === 0;
      older.disabled = p === n - 1;
    };
    nav.onclick = e => { const b = e.target.closest("button"); if (b) show(pages[key] + +b.dataset.d); };
    show(pages[key] || 0);
  });
}
async function render(keepScroll = false) {
  const { tab, args, q } = parseHash();
  const my = ++seq;
  if (keepScroll) cache.clear();
  paintTabs(tab);
  const fn = VIEWS[tab] || VIEWS.dashboard;
  const inPage = keepScroll || (shownTab === tab && (tab === "dashboard" || tab === "activity" || shownArgs === args.join("/") || (tab === "sleep" && args.length && shownArgs)));
  const slow = setTimeout(() => view.classList.add("busy"), inPage ? 400 : 250);
  if (!view.childElementCount) view.innerHTML = '<div class="empty">Loading…</div>';
  const swap = html => {
    clearTimeout(slow);
    hideHint();
    const y = window.scrollY, focus = document.activeElement?.id;
    view.classList.remove("busy", "enter");
    if (!inPage) for (const k in pages) delete pages[k];
    view.innerHTML = html.html;
    html.after && html.after();
    paginate(view);
    shownTab = tab; shownArgs = args.join("/");
    if (inPage) {
      window.scrollTo(0, y);
      if (focus) document.getElementById(focus)?.focus({ preventScroll: true });
      return;
    }
    window.scrollTo(0, 0);
    void view.offsetWidth;                  // restart the fade
    view.classList.add("enter");
  };
  try {
    const html = await fn.load(args, q);
    if (my !== seq) return;               // a newer navigation won
    swap(html);
  } catch (e) {
    if (my !== seq) return;
    swap({ html: `<div class="err-box">Couldn't load this view.<br><span class="note">${esc(e.message)}</span></div>` });
    console.error(e);
  }
}

window.addEventListener("hashchange", () => render());

// small helpers to place charts after the HTML is in the page
function mount(id, cfg) { const e = document.getElementById(id); if (e) chart(e, cfg); }
const chartBox = (id, title, now = "", sub = "") =>
  `<div class="card"><div class="chart-title"><h3>${title}</h3><span class="now">${now}</span></div>${sub ? `<div class="note csub">${sub}</div>` : ""}<div id="${id}"></div></div>`;
const kv = (k, v) => `<div class="kv"><span>${k}</span><span>${v}</span></div>`;
const bandDot = b => `<span class="band ${esc(b || "")}"></span>`;

// progress ring: value of max, an optional target arc behind it, the figure in the middle
function ring(value, max, cls, inner, target) {
  const r = 52, C = 2 * Math.PI * r, f = v => Math.max(0, Math.min(1, v / max)) * C;
  const tgt = target ? `<circle class="tgt" cx="60" cy="60" r="${r}" stroke-dasharray="${f(target.high) - f(target.low)} ${C}" stroke-dashoffset="${-f(target.low)}"/>` : "";
  const val = value != null && value > 0 ? `<circle class="val ${cls || ""}" cx="60" cy="60" r="${r}" stroke-dasharray="${Math.max(0.1, f(value))} ${C}"/>` : "";
  return `<div class="ring"><svg viewBox="0 0 120 120" aria-hidden="true"><circle class="trk" cx="60" cy="60" r="${r}"/>${tgt}${val}</svg><div class="num">${inner}</div></div>`;
}

// ============================================================ dashboard ====

VIEWS.dashboard = {
  async load(args) {
    const d = args[0] || today();
    const dash = await get(`/dashboard?date=${d}`);
    const rec = dash.recovery, sp = dash.sleep.performance, sg = dash.sleep.google;
    const st = dash.strain.today, tgt = dash.strain.target, act = dash.google_daily_activity || {};
    const rw = dash.running_week;
    const dc = dash.daily_check;

    const ZN = { rhr: "resting HR", ln_rmssd: "HRV", nrem_hr: "non-REM HR", breathing: "breathing rate", skin_temp: "skin temperature", sleep_min: "sleep time", steps: "steps" };
    const off = dc ? Object.entries(dc.z).filter(([, v]) => v > 1.5).map(([k]) => ZN[k] || k) : [];
    const warn = dc && dc.warning
      ? `<div class="warn ${dc.flag === "AMBER" ? "amber" : ""}"><b>${dc.flag === "RED" ? "Several recovery markers are off" : "Some recovery markers are off"}.</b> Worse than your baseline: ${esc(off.join(", ") || "several signals")}${dc.excluded_event ? " (event night, left out of your baseline)" : ""}. <a href="#/recovery">Details</a></div>` : "";

    const recCard = rec ? `
      <a class="card score hero" href="#/recovery"><div class="label">${hint("recovery", "Recovery")}</div>
        <div class="top">${ring(rec.recovery, 100, rec.band, `<div class="big">${Math.round(rec.recovery)}<small>%</small></div>`)}
          <div class="side-kv">${kv(hint("hrv", "HRV"), n0(rec.google_rmssd ?? rec.hrv_ms) + " ms")}${kv(hint("rhr", "Resting HR"), n0(rec.rhr) + " bpm")}${kv(hint("breathing", "Breathing"), n1(rec.breathing) + " /min")}</div></div>
        ${rec.provisional ? '<div class="note">Provisional: baseline still short</div>' : ""}</a>`
      : `<div class="card score hero"><div class="label">${hint("recovery", "Recovery")}</div><div class="top">${ring(null, 100, "", '<div class="big">–</div>')}<div class="side-kv note">No score for this day yet.</div></div></div>`;

    const asleep = sp?.asleep_min ?? sg?.total_minutes;
    const sleepCard = (sp || sg) ? `
      <a class="card score" href="#/sleep"><div class="label">Last night · ${hint("sleep_perf", "Sleep Performance")}</div>
        <div class="top">${ring(sp?.sleep_performance, 100, "data", `<div class="big">${sp?.sleep_performance != null ? Math.round(sp.sleep_performance) + "<small>%</small>" : "–"}</div>`)}
          <div class="side-kv">${kv("Asleep", fHM(asleep))}${kv(hint("need", "Need"), fHM(sp?.need_min))}
          ${kv("Bed / wake", sg ? `${fClock(sg.start_time)} – ${fClock(sg.end_time)}` : "–")}</div></div></a>`
      : `<div class="card score"><div class="label">Last night</div><div class="top">${ring(null, 100, "", '<div class="big">–</div>')}<div class="side-kv note">No sleep recorded.</div></div></div>`;

    const strainCard = `
      <a class="card score" href="#/training"><div class="label">${hint("strain", "Strain")} ${d === today() ? "today" : ""}</div>
        <div class="top">${ring(st?.strain, 21, "data", `<div class="big">${st ? n1(st.strain) : "–"}<small class="under">/ 21</small></div>`, tgt)}
          <div class="side-kv">${kv(hint("strain_target", "Target"), tgt ? `${tgt.low}–${tgt.high} · ${esc(tgt.label)}` : "needs a recovery score")}</div></div>
        ${st?.partial ? '<div class="note">Day still running</div>' : ""}</a>`;

    const steps = dash.steps;
    const stepsCard = `
      <a class="card wide" href="#/activity/${d}"><div class="label">Steps</div>
        <div class="big">${n0(steps)}</div>
        <div class="meter"><i style="width:${Math.min(100, (steps || 0) / STEP_GOAL * 100)}%"></i></div>
        <div class="kvs">${kv("Goal", n0(STEP_GOAL))}${kv("Distance", act.distance_km != null ? n1(act.distance_km) + " km" : "–")}${kv("Floors", n0(act.floors))}</div></a>`;

    const runCard = `
      <a class="card wide" href="#/training"><div class="label">Running this week</div>
        <div class="big">${n1(rw.km)}<small> km</small></div>
        <div class="kvs">${kv("Runs", rw.runs)}${kv("Time", fHM(rw.minutes))}
        ${kv(hint("easy", "Easy (Z1–Z2)"), rw.easy_pct != null ? pct(rw.easy_pct) + " of run time" : "–")}</div></a>`;

    const anns = dash.annotations.length ? `<div class="note events-note">Events: ${dash.annotations.map(a => `${esc(a.kind)}${a.note ? " · " + esc(a.note) : ""}`).join("; ")}</div>` : "";

    return {
      html: `
        <div class="head"><h1>${d === today() ? "Today" : fDay(d)}</h1><span class="sub">${d === today() ? fDay(d) : ""}</span><span class="spacer"></span>
          <div class="datenav"><button class="btn small" id="prev">‹</button><input type="date" id="day" value="${d}" max="${today()}"><button class="btn small" id="next" ${d >= today() ? "disabled" : ""}>›</button>
          <button class="btn small" id="tod" ${d === today() ? 'style="visibility:hidden" tabindex="-1"' : ""}>Today</button></div></div>
        ${warn}
        <div class="dash">${recCard}${sleepCard}${strainCard}${stepsCard}${runCard}</div>
        ${anns}`,
      after() {
        const go = x => location.hash = `#/dashboard/${x}`;
        document.getElementById("prev").onclick = () => go(addDays(d, -1));
        document.getElementById("next").onclick = () => go(addDays(d, 1));
        document.getElementById("day").onchange = e => e.target.value && go(e.target.value);
        document.getElementById("tod").onclick = () => location.hash = "#/dashboard";
        prefetch(`/dashboard?date=${addDays(d, -1)}`, ...(d < today() ? [`/dashboard?date=${addDays(d, 1)}`] : []));
      },
    };
  },
};

// ============================================================= recovery ====

const REC_PARTS = { hrv: "HRV", rhr: "Resting HR", sleep: "Sleep", resp: "Breathing rate" };

async function explainRecovery(box, date) {
  box.innerHTML = '<div class="note">Loading…</div>';
  const e = await get(`/explain/recovery?date=${date}`);
  if (e.error) { box.innerHTML = `<div class="note">${esc(e.error)}</div>`; return; }
  box.innerHTML = `<div class="panel"><h3>${fDay(date)}: ${bandDot(e.band)}${Math.round(e.value)}% recovery</h3>
    <p class="note">Each signal is compared with your own baseline (last ${n0(e.baseline_n)} nights, illness and alcohol nights left out).</p>
    <div class="tablewrap"><table><tr><th>Signal</th><th class="num">${hint("z", "vs. normal (z)")}</th><th class="num">Weight</th><th class="num">${hint("contribution", "Contribution")}</th></tr>
    ${Object.entries(e.components).map(([k, c]) => `<tr><td>${hint({ hrv: "hrv", rhr: "rhr", resp: "breathing", sleep: "sleep_perf" }[k] || k, REC_PARTS[k] || k)}</td><td class="num">${sgn(c.z)}</td><td class="num">${c.weight ?? "–"}</td><td class="num">${sgn(c.contribution, 3)}</td></tr>`).join("")}
    </table></div>
    <div class="cols" style="margin-top:10px">${kv("HRV used", n1(e.raw.hrv_ms) + " ms (" + esc({ google_night: "Google night average", last_deep: "last deep-sleep phase", deep_median: "median of deep sleep", sleep_median: "median of sleep" }[e.raw.hrv_source] || e.raw.hrv_source || "") + ")")}${kv("Deep-sleep HRV", n1(e.raw.deep_hrv_ms) + " ms (not used)")}${kv("Resting HR", n0(e.raw.rhr))}${kv("Breathing", n1(e.raw.breathing))}${kv("SpO2", n1(e.raw.spo2) + "%")}${kv("Skin temp", sgn(e.raw.skin_rel) + " °C")}</div>
    ${e.penalty ? `<p class="note">Penalty ${e.penalty}${e.flags ? " · " + esc(e.flags) : ""}</p>` : ""}
    ${e.provisional ? '<p class="note">Provisional: the baseline is still short.</p>' : ""}${e.excluded_event_night ? '<p class="note">Event night: not used in future baselines.</p>' : ""}</div>`;
}

VIEWS.recovery = {
  async load() {
    const [fr, to] = rangeDates();
    const base = addDays(to, -59) < fr ? addDays(to, -59) : fr;   // 60 days for the normal ranges
    const [rec, nrem] = await Promise.all([get(`/recovery?from=${base}&to=${to}`), get(`/trend/nrem_hr?from=${base}&to=${to}`)]);
    const all = rec.days, map = byKey(all), nmap = byKey(nrem.points);
    const shown = all.filter(r => r.date >= fr);
    const xs = xDates(fr, to, shown);
    const sh = shades(rec.annotations);
    // normal = last 30 days with a value and no event, mean ± 1 SD
    const normal = (rows, f) => {
      const v = rows.filter(r => !(r.annotation_ids || []).length).slice(-30).map(f).filter(x => x != null);
      const m = mean(v), s = sd(v);
      return m == null || s == null ? null : { lo: m - s, hi: m + s, m };
    };
    const sig = [
      ["hrv", hint("hrv", "HRV"), r => r.google_rmssd ?? r.hrv_ms, "ms", "dashed: deep-sleep HRV", all],
      ["rhr", hint("rhr", "Resting heart rate"), r => r.rhr, "bpm", "", all],
      ["nrem", hint("nrem_hr", "Non-REM heart rate"), r => r.value, "bpm", "", nrem.points],
      ["resp", hint("breathing", "Breathing rate"), r => r.breathing, "/min", "", all],
      ["spo2", hint("spo2", "SpO2"), r => r.spo2, "%", "", all],
      ["skin", hint("skin_temp", "Skin temperature"), r => r.skin_rel, "°C", "", all],
    ];
    const last = shown[shown.length - 1];
    const rows = shown.slice().reverse();
    return {
      html: `
        <div class="head"><h1>Recovery</h1><span class="spacer"></span>${rangeSeg()}</div>
        <div class="card"><div class="chart-title"><h3>${hint("recovery", "Recovery")}</h3>${eventNote(rec.annotations)}<span class="now">${last ? bandDot(last.band) + Math.round(last.recovery) + "%" : ""}</span></div>
          <div id="c-rec"></div><div class="note">Click a day to see why.</div></div>
        <div id="explain" style="margin-top:16px"></div>
        <section><h2>Signals against your normal range</h2>
          <p class="note">Shaded band: your normal (mean ± 1 SD of the last 30 nights without illness or alcohol).</p>
          <div class="grid g3">${sig.map(([k, t, f, u, sub, src]) => {
            const lv = src.filter(r => r.date >= fr).slice(-1)[0];
            return chartBox("c-" + k, t, lv && f(lv) != null ? `${k === "skin" ? sgn(f(lv)) : n1(f(lv))} ${u}` : "", sub);
          }).join("")}</div></section>
        <section><h2>Days</h2><div class="tablewrap"><table data-pages="rec-days">
          <tr><th>Night</th><th class="num">${hint("recovery", "Recovery")}</th><th class="num">${hint("hrv", "HRV")}</th><th class="num">${hint("deep_hrv", "Deep HRV")}</th><th class="num">${hint("rhr", "RHR")}</th><th class="num">${hint("breathing", "Breathing")}</th><th class="num">${hint("spo2", "SpO2")}</th><th class="num">${hint("skin_temp", "Skin °C")}</th><th class="num">${hint("sleep_perf", "Sleep perf.")}</th><th>${hint("check", "Check")}</th><th>${hint("hrv_status", "HRV status")}</th></tr>
          ${rows.map(r => `<tr class="click" data-d="${r.date}"><td>${fDay(r.date)}</td><td class="num">${bandDot(r.band)}${Math.round(r.recovery)}%</td><td class="num">${n1(r.google_rmssd ?? r.hrv_ms)}</td><td class="num">${n1(r.deep_hrv_ms)}</td><td class="num">${n0(r.rhr)}</td><td class="num">${n1(r.breathing)}</td><td class="num">${n1(r.spo2)}</td><td class="num">${sgn(r.skin_rel)}</td><td class="num">${pct(r.sleep_performance)}</td><td>${esc(r.daily_check_flag || "")}</td><td>${esc(r.hrv_status || "")}</td></tr>`).join("")}
        </table></div></section>`,
      after() {
        bindRange();
        const box = document.getElementById("explain");
        const show = d => { explainRecovery(box, d); box.scrollIntoView({ block: "nearest", behavior: "smooth" }); };
        mount("c-rec", {
          x: xs, height: 190, y: { min: 0, max: 100, ticks: [0, 33, 66, 100], fmt: v => v + "%" }, shades: sh, xfmt: fShort, tipx: fDay,
          series: [{ name: "Recovery", type: "bar", data: pick(xs, map, r => r.recovery), colors: pick(xs, map, r => COLORS[r.band]), fmt: pct }],
          onClick: i => map[xs[i]] && show(xs[i]),
        });
        for (const [k, , f, u, , src] of sig) {
          const m = k === "nrem" ? nmap : map;
          mount("c-" + k, {
            x: xs, height: 140, band: normal(src, f), shades: sh, xfmt: fShort, tipx: fDay,
            series: [{ name: k === "hrv" ? "Google" : u, type: "line", data: pick(xs, m, f), fmt: k === "skin" ? (v => sgn(v)) : n1 },
              ...(k === "hrv" ? [{ name: "Deep sleep", type: "line", dash: true, noDots: true, color: "var(--ink-2)", data: pick(xs, m, r => r.deep_hrv_ms), fmt: n1 }] : [])],
          });
        }
        view.querySelectorAll("tr[data-d]").forEach(tr => tr.onclick = () => show(tr.dataset.d));
      },
    };
  },
};

// ================================================================ sleep ====

const clockY = t => { if (!t) return null; const d = new Date(t); return ((d.getHours() + d.getMinutes() / 60) - 18 + 24) % 24; };
const fClockY = v => pad2(Math.round((v + 18) % 24)) + ":00";

const LANES = [["awake", "Awake", "var(--red)"], ["rem", "REM", "var(--ink-2)"], ["light", "Light", "var(--ink-3)"], ["deep", "Deep", "var(--ink)"]];

// Night detail (#/sleep/<wake date>): Sleep Performance and its parts first,
// then the stages against the 30 nights before, then the hypnogram.
const STAGES = [["Deep", "deep_minutes", "var(--ink)"], ["REM", "rem_minutes", "var(--ink-2)"], ["Light", "light_minutes", "var(--ink-3)"], ["Awake", "wake_minutes", "var(--red)"]];
async function nightView(d) {
  const [sl, e] = await Promise.all([get(`/sleep?from=${addDays(d, -30)}&to=${addDays(d, 7)}`), get(`/explain/sleep?date=${d}`).catch(err => ({ error: err.message }))]);
  const i = sl.nights.findIndex(r => r.night === d);
  const r = sl.nights[i] || {}, g = r.google || {}, p = r.performance || {}, N = e.need;
  const prev = i > 0 ? sl.nights[i - 1].night : (i < 0 ? sl.nights.filter(x => x.night < d).pop()?.night : null);
  const next = i >= 0 ? sl.nights[i + 1]?.night : sl.nights.find(x => x.night > d)?.night;
  const before = sl.nights.filter(x => x.night < d && x.google);
  const avg = k => before.length ? mean(before.map(x => x.google[k]).filter(v => v != null)) : null;
  const inBed = STAGES.reduce((t, [, k]) => t + (g[k] || 0), 0);
  const asleep = p.asleep_min ?? g.total_minutes;
  // the night an annotation lands on (alcohol: the night after the evening)
  const anns = sl.annotations.filter(a => a.status !== "rejected" && (a.target === `night:${d}` || (!a.target && (a.excludes_nights || []).includes(d))));
  const stat = (label, big, sub) => `<div class="card"><div class="label">${label}</div><div class="big">${big}</div><div class="note">${sub}</div></div>`;
  return {
    html: `
      <div class="head"><a href="#/sleep" class="note">‹ Sleep</a></div>
      <div class="head"><h1>${fDay(d)}</h1><span class="sub">${g.start_time ? `${fClock(g.start_time)}–${fClock(g.end_time)} · ` : ""}night ending on this day</span>
        <div class="datenav"><button class="btn small" id="prev" aria-label="Previous night" ${prev ? "" : "disabled"}>‹</button><button class="btn small" id="next" aria-label="Next night" ${next ? "" : "disabled"}>›</button></div></div>
      <div class="grid g5 trio">
        ${stat(hint("sleep_perf", "Sleep Performance"), pct(e.value ?? p.sleep_performance), p.sufficiency != null ? `${hint("sufficiency", "sufficiency")} ${pct(p.sufficiency)}` : "")}
        ${stat("Asleep", fHM(asleep), N ? `${hint("need", "need")} ${fHM(N.total_min)}` : "")}
        ${stat("In bed", fHM(inBed || null), g.start_time ? `${fClock(g.start_time)}–${fClock(g.end_time)}` : "")}
      </div>
      <section><div class="card"><h3>${hint("sleep_perf", "Sleep Performance")}</h3>${e.error ? `<p class="note">${esc(e.error)}</p>` : `
        <div class="cols">
          <div style="flex:1;min-width:240px"><div class="tablewrap"><table><tr><th>Part</th><th class="num">Score</th><th class="num">Points</th></tr>
            ${Object.entries(e.components).map(([k, c]) => `<tr><td>${hint(k, k[0].toUpperCase() + k.slice(1))}</td><td class="num">${n1(c.value)}</td><td class="num">${n1(c.contribution)}</td></tr>`).join("")}</table></div></div>
          <div style="flex:1;min-width:220px">${kv("Asleep", fHM(e.asleep_min))}${kv(hint("need", "Need"), fHM(N.total_min))}${kv("· baseline", fHM(N.baseline_min))}${kv("· from strain", "+" + fHM(N.from_strain_min))}${kv("· from sleep debt", "+" + fHM(N.from_sleep_debt_min))}${kv("· minus naps", "−" + fHM(N.minus_naps_min))}</div>
        </div>`}</div></section>
      <section><h2>${hint("stages", "Stages")}</h2><div class="tablewrap"><table>
        <tr><th>Stage</th><th class="num">Time</th><th class="num">Share</th><th class="num">Usual</th></tr>
        ${STAGES.map(([n, k, c]) => `<tr><td><span class="band" style="background:${c}"></span>${n}</td><td class="num">${fHM(g[k])}</td><td class="num">${inBed && g[k] != null ? pct(g[k] / inBed * 100) : "–"}</td><td class="num">${fHM(avg(k))}</td></tr>`).join("")}
        <tr><td><b>Asleep</b></td><td class="num"><b>${fHM(asleep)}</b></td><td class="num">${inBed && asleep != null ? pct(asleep / inBed * 100) : "–"}</td><td class="num">${fHM(avg("total_minutes"))}</td></tr>
      </table></div><p class="note">Share of the time in bed. Usual is the average of the ${before.length} nights before this one.</p></section>
      <section><div class="card"><h3>Sleep phases</h3>${e.hypnogram ? `<div class="note csub">Stages through the night, ${fClock(e.hypnogram.start)}–${fClock(e.hypnogram.end)}${e.hypnogram.brief.length ? ` · thin marks: ${e.hypnogram.brief.length} brief wake-ups` : ""}</div><div id="c-hypno"></div>` : '<p class="note">No stage data for this night.</p>'}</div></section>
      ${anns.length ? `<section><h2>Notes</h2>${anns.map(x => `<p class="note">${esc(x.kind)} (${esc(x.status)}): ${esc(x.note || "")}</p>`).join("")}</section>` : ""}`,
    after() {
      const go = n => location.hash = `#/sleep/${n}`;
      document.getElementById("prev").onclick = () => prev && go(prev);
      document.getElementById("next").onclick = () => next && go(next);
      prefetch(...[prev, next].filter(Boolean).flatMap(n => [`/sleep?from=${addDays(n, -30)}&to=${addDays(n, 7)}`, `/explain/sleep?date=${n}`]));
      const hb = document.getElementById("c-hypno");
      if (hb) hypnogram(hb, { ...e.hypnogram, lanes: LANES, tfmt: t => fClock(t), dfmt: fHM });
    },
  };
}

VIEWS.sleep = {
  async load(args) {
    if (args[0]) return nightView(args[0]);
    const [fr, to] = rangeDates();
    const [sl, gs] = await Promise.all([get(`/sleep?from=${fr}&to=${to}`), get(`/trend/google_sleep_score?from=${fr}&to=${to}`)]);
    const nights = sl.nights, map = byKey(nights, "night");
    const xs = xDates(fr, to, nights, "night");
    const sh = shades(sl.annotations);
    const gmap = {};
    gs.points.forEach(p => { (gmap[p.date] = gmap[p.date] || []).push(p.value); });
    const gx = xs.filter(d => d <= (gs.points.slice(-1)[0]?.date || ""));
    const last = nights[nights.length - 1];
    const asleep = r => r.performance?.asleep_min ?? r.google?.total_minutes;
    const cons = nights.map(r => r.performance?.consistency).filter(v => v != null);
    return {
      html: `
        <div class="head"><h1>Sleep</h1><span class="spacer"></span>${rangeSeg()}</div>
        <div class="grid g2">
          ${chartBox("c-perf", hint("sleep_perf", "Sleep Performance"), last ? pct(last.performance?.sleep_performance) : "")}
          ${chartBox("c-dur", `Asleep vs. ${hint("need", "need")}`, last ? fHM(asleep(last)) : "", "bars asleep, dashed line need")}
        </div>
        <section><div class="grid g2">
          <div class="card"><div class="chart-title"><h3>${hint("stages", "Stages")}</h3></div>${legend([["Deep", "var(--ink)"], ["REM", "var(--ink-2)"], ["Light", "var(--ink-3)"], ["Awake", "var(--red)"]])}<div id="c-stages"></div></div>
          ${chartBox("c-times", "Bed and wake times", cons.length ? hint("consistency", "consistency") + " " + Math.round(mean(cons.slice(-7))) : "", "regularity")}
        </div></section>
        <section><div class="grid g2">
          ${chartBox("c-gscore", hint("google_sleep", "Google sleep score"), "", "from the latest Takeout export")}
          <div></div></div></section>
        <section><h2>Nights</h2><p class="note">Click a night, or a day on the charts, for its detail.</p><div class="tablewrap"><table data-pages="sleep-nights">
          <tr><th>Night (wake date)</th><th class="num">${hint("sleep_perf", "Performance")}</th><th>Bed</th><th>Wake</th><th class="num">Asleep</th><th class="num">${hint("need", "Need")}</th><th class="num">Deep</th><th class="num">REM</th><th class="num">Light</th><th class="num">Awake</th></tr>
          ${nights.slice().reverse().map(r => { const g = r.google || {}, p = r.performance || {}; return `<tr class="click" data-d="${r.night}"><td>${fDay(r.night)}</td><td class="num"><b>${pct(p.sleep_performance)}</b></td><td>${fClock(g.start_time)}</td><td>${fClock(g.end_time)}</td><td class="num">${fHM(asleep(r))}</td><td class="num" title="${p.need_min != null ? `your baseline ${fHM(p.need_baseline_min)} + after strain ${fHM(p.need_strain_min)} + sleep debt ${fHM(p.need_debt_min)} − naps ${fHM(p.nap_prev_day_min)}` : ""}">${fHM(p.need_min)}</td><td class="num">${fHM(g.deep_minutes)}</td><td class="num">${fHM(g.rem_minutes)}</td><td class="num">${fHM(g.light_minutes)}</td><td class="num">${fHM(g.wake_minutes)}</td></tr>`; }).join("")}
        </table></div></section>`,
      after() {
        bindRange();
        const show = d => location.hash = `#/sleep/${d}`;
        const hfmt = v => v == null ? "–" : (v).toFixed(1) + " h";
        mount("c-dur", {
          x: xs, height: 180, y: { min: 0, fmt: v => v + "h" }, shades: sh, xfmt: fShort, tipx: fDay, onClick: i => map[xs[i]] && show(xs[i]),
          series: [
            { name: "Asleep", type: "bar", data: pick(xs, map, r => asleep(r) / 60), fmt: hfmt },
            { name: "Need", type: "line", dash: true, color: "var(--ink-2)", noDots: true, data: pick(xs, map, r => r.performance?.need_min / 60), fmt: hfmt },
          ],
        });
        mount("c-perf", {
          x: xs, height: 180, y: { min: 0, max: 100, fmt: v => v + "%" }, shades: sh, xfmt: fShort, tipx: fDay, onClick: i => map[xs[i]] && show(xs[i]),
          series: [{ name: "Performance", type: "line", data: pick(xs, map, r => r.performance?.sleep_performance), fmt: pct },
            { name: "Sufficiency", type: "line", color: "var(--ink-3)", noDots: true, data: pick(xs, map, r => r.performance?.sufficiency), fmt: pct }],
        });
        const st = k => pick(xs, map, r => r.google?.[k] != null ? r.google[k] / 60 : null);
        mount("c-stages", {
          x: xs, height: 170, y: { min: 0, fmt: v => v + "h" }, xfmt: fShort, tipx: fDay, onClick: i => map[xs[i]] && show(xs[i]),
          series: [["Deep", "deep_minutes", "var(--ink)"], ["REM", "rem_minutes", "var(--ink-2)"], ["Light", "light_minutes", "var(--ink-3)"], ["Awake", "wake_minutes", "var(--red)"]]
            .map(([n, k, c]) => ({ name: n, type: "bar", stack: "s", color: c, data: st(k), fmt: v => fHM(v * 60) })),
        });
        mount("c-times", {
          x: xs, height: 170, y: { min: 0, max: 18, invert: true, ticks: [0, 3, 6, 9, 12, 15, 18], fmt: fClockY }, xfmt: fShort, tipx: fDay, onClick: i => map[xs[i]] && show(xs[i]),
          series: [{ name: "In bed", type: "range", color: "var(--ink)", lo: pick(xs, map, r => clockY(r.google?.start_time)), hi: pick(xs, map, r => { const a = clockY(r.google?.start_time), b = clockY(r.google?.end_time); return b != null && a != null && b < a ? b + 24 : b; }),
            tip: i => { const g = map[xs[i]]?.google; return g ? `${fClock(g.start_time)} – ${fClock(g.end_time)}` : "–"; } }],
        });
        mount("c-gscore", {
          x: gx, height: 140, y: { fmt: v => Math.round(v) }, xfmt: fShort, tipx: fDay,
          series: [{ name: "Score", type: "line", data: gx.map(d => gmap[d] ? Math.max(...gmap[d]) : null), fmt: v => v == null ? "–" : Math.round(v) }],
        });
        view.querySelectorAll("tr[data-d]").forEach(tr => {
          tr.onclick = () => show(tr.dataset.d);
          tr.onpointerenter = () => get(`/explain/sleep?date=${tr.dataset.d}`).catch(() => {});   // warm it before the click
        });
      },
    };
  },
};

// ============================================================= activity ====

VIEWS.activity = {
  async load(args) {
    const [fr, to] = rangeDates();
    const day = args[0] || today();
    const [ac, dd] = await Promise.all([get(`/activity?from=${fr}&to=${to}`), get(`/day/${day}`)]);
    const map = byKey(ac.days), xs = xDates(fr, to, ac.days), sh = shades(ac.annotations);
    // weekly totals
    const weeks = {};
    for (const r of ac.days) {
      const w = weeks[mondayOf(r.date)] = weeks[mondayOf(r.date)] || { days: 0, steps: 0, azm: 0, active: 0, km: 0, floors: 0 };
      w.days++; w.steps += r.steps || 0; w.azm += r.azm || 0; w.active += r.active_min || 0; w.km += r.distance_km || 0; w.floors += r.floors || 0;
    }
    const wk = Object.entries(weeks).sort().reverse();
    const avg = mean(ac.days.filter(r => r.date < today()).map(r => r.steps));
    const dsum = dd.steps_by_hour.reduce((s, h) => s + h.steps, 0);
    const bpm = dd.hr_5min.map(p => p.bpm).filter(v => v != null);
    const hrNow = bpm.length ? `${n0(Math.min(...bpm))}–${n0(Math.max(...dd.hr_5min.map(p => p.max ?? p.bpm).filter(v => v != null)))} bpm` : "";
    return {
      html: `
        <div class="head"><h1>Activity</h1><span class="spacer"></span>${rangeSeg()}</div>
        <div class="card"><div class="chart-title"><h3>Steps</h3><span class="note">avg ${n0(avg)} / day · goal ${n0(STEP_GOAL)} · click a day for detail</span>${eventNote(ac.annotations)}</div><div id="c-steps"></div></div>
        <section><div class="grid g2">
          <div class="card"><div class="chart-title"><h3>${hint("azm", "Active Zone Minutes")}</h3></div>${legend([["Fat burn", "var(--ink-3)"], ["Cardio", "var(--ink-2)"], ["Peak", "var(--ink)"]])}<div id="c-azm"></div></div>
          <div class="card"><div class="chart-title"><h3>${hint("active_min", "Active minutes")}</h3><span class="note">watch</span></div>${legend([["Light", "var(--ink-3)"], ["Moderate", "var(--ink-2)"], ["Vigorous", "var(--ink)"]])}<div id="c-act"></div></div>
          ${chartBox("c-floors", "Floors")}
          ${chartBox("c-sed", hint("sedentary", "Sitting time"), "", "sedentary periods, hours")}
        </div></section>
        <section><h2>${fDay(day)}</h2>
          <div class="head" style="margin-bottom:10px"><div class="datenav"><button class="btn small" id="prev">‹</button><input type="date" id="day" value="${day}" max="${today()}"><button class="btn small" id="next" ${day >= today() ? "disabled" : ""}>›</button></div>
          <span class="note">${n0(dsum)} steps${dd.sleep ? ` · slept ${fClock(dd.sleep.start_time)}–${fClock(dd.sleep.end_time)}` : ""}${dd.workouts.length ? " · " + dd.workouts.map(w => `<a href="#/training/w/${w.id}">${esc(w.name)}</a>`).join(", ") : ""}</span></div>
          <div class="grid">${chartBox("c-hr", "Heart rate", hrNow, "5-minute averages; thin line: highest in each 5 minutes; shaded: sleep")}${chartBox("c-hours", "Steps per hour")}</div></section>
        <section><h2>Weeks</h2><div class="tablewrap"><table data-pages="act-weeks">
          <tr><th>Week</th><th class="num">Steps / day</th><th class="num">${hint("azm", "AZM")}</th><th class="num">${hint("active_min", "Active min")}</th><th class="num">km</th><th class="num">Floors</th></tr>
          ${wk.map(([m, w]) => `<tr><td>${fWeek(m)}</td><td class="num">${n0(w.steps / w.days)}</td><td class="num">${n0(w.azm)}</td><td class="num">${n0(w.active)}</td><td class="num">${n1(w.km)}</td><td class="num">${n0(w.floors)}</td></tr>`).join("")}
        </table></div></section>`,
      after() {
        bindRange();
        const go = d => location.hash = `#/activity/${d}`;
        document.getElementById("prev").onclick = () => go(addDays(day, -1));
        document.getElementById("next").onclick = () => go(addDays(day, 1));
        document.getElementById("day").onchange = e => e.target.value && go(e.target.value);
        prefetch(`/day/${addDays(day, -1)}`, ...(day < today() ? [`/day/${addDays(day, 1)}`] : []));
        const base = { x: xs, xfmt: fShort, tipx: fDay, shades: sh };
        mount("c-steps", { ...base, height: 190, refs: [{ y: STEP_GOAL, label: "8,000" }], selected: xs.indexOf(day) >= 0 ? xs.indexOf(day) : null,
          series: [{ name: "Steps", type: "bar", data: pick(xs, map, r => r.steps), fmt: n0 }], onClick: i => go(xs[i]) });
        mount("c-azm", { ...base, height: 160, series: [
          { name: "Fat burn", type: "bar", stack: "a", color: "var(--ink-3)", data: pick(xs, map, r => r.azm_fat_burn), fmt: n0 },
          { name: "Cardio ×2", type: "bar", stack: "a", color: "var(--ink-2)", data: pick(xs, map, r => r.azm_cardio != null ? r.azm_cardio * 2 : null), fmt: n0 },
          { name: "Peak ×2", type: "bar", stack: "a", color: "var(--ink)", data: pick(xs, map, r => r.azm_peak != null ? r.azm_peak * 2 : null), fmt: n0 }] });
        mount("c-act", { ...base, height: 160, series: [
          { name: "Light", type: "bar", stack: "a", color: "var(--ink-3)", data: pick(xs, map, r => r.active_light), fmt: n0 },
          { name: "Moderate", type: "bar", stack: "a", color: "var(--ink-2)", data: pick(xs, map, r => r.active_moderate), fmt: n0 },
          { name: "Vigorous", type: "bar", stack: "a", color: "var(--ink)", data: pick(xs, map, r => r.active_vigorous), fmt: n0 }] });
        mount("c-floors", { ...base, height: 140, series: [{ name: "Floors", type: "bar", data: pick(xs, map, r => r.floors), fmt: n0 }] });
        mount("c-sed", { ...base, height: 140, series: [{ name: "Sitting", type: "bar", color: "var(--ink-2)", data: pick(xs, map, r => r.sedentary_min != null ? r.sedentary_min / 60 : null), fmt: v => fHM(v * 60) }] });
        const mins = t => { const [h, m] = t.slice(11, 16).split(":"); return +h * 60 + +m; };
        const hrx = dd.hr_5min.map(p => mins(p.t));
        const sleepSh = [];
        if (dd.sleep) {
          const a = new Date(dd.sleep.start_time), b = new Date(dd.sleep.end_time);
          const from = iso(a) < day ? 0 : a.getHours() * 60 + a.getMinutes();
          sleepSh.push({ from, to: b.getHours() * 60 + b.getMinutes(), label: "sleep" });
        }
        mount("c-hr", { x: hrx, numeric: true, xmin: 0, xmax: 1440, xticks: niceTicksBy(0, 1440, 120), gap: 20, height: 260, shades: sleepSh, xfmt: v => pad2(Math.floor(v / 60)) + ":" + pad2(v % 60), series: [
          { name: "bpm", type: "line", width: 1.2, data: dd.hr_5min.map(p => p.bpm), fmt: n0 },
          { name: "max", type: "line", width: 0.8, color: "var(--ink-3)", data: dd.hr_5min.map(p => p.max), fmt: n0 }] });
        mount("c-hours", { x: dd.steps_by_hour.map(h => h.hour), height: 170, xfmt: h => pad2(h), tipx: h => `${pad2(h)}:00–${pad2(h + 1)}:00`,
          series: [{ name: "Steps", type: "bar", data: dd.steps_by_hour.map(h => h.steps), fmt: n0 }] });
      },
    };
  },
};

// ============================================================= training ====

const TYPE = { RUNNING: "Run", WALKING: "Walk", STRENGTH_TRAINING: "Strength", TABLE_TENNIS: "Table tennis", SWIMMING: "Swim", BIKING: "Ride", HIKING: "Hike" };
const typeName = t => TYPE[t] || (t || "").toLowerCase().replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase());

VIEWS.training = {
  async load(args) {
    if (args[0] === "w" && args[1]) return workoutView(args[1]);
    const [fr, to] = rangeDates();
    const [rs, tr] = await Promise.all([get(`/runs?from=${fr}&to=${to}&all_types=true`), get(`/training?from=${fr}&to=${to}`)]);
    const runs = rs.runs.filter(r => r.type === "RUNNING");
    const list = (state.runsOnly ? runs : rs.runs).slice().reverse();
    const smap = byKey(tr.strain), xs = xDates(fr, to, tr.strain), sh = shades(tr.annotations);
    const rn = rs.running;
    const weeks = tr.weeks;
    const ac = tr.acwr, amap = byKey(ac), ax = xDates(fr, ac.length ? ac[ac.length - 1].date : to, ac);
    return {
      html: `
        <div class="head"><h1>Training</h1><span class="spacer"></span>${rangeSeg()}</div>
        <div class="grid g5">
          <div class="card"><div class="label">Runs</div><div class="big">${rn.runs}</div></div>
          <div class="card"><div class="label">Distance</div><div class="big">${n1(rn.km)}<small> km</small></div></div>
          <div class="card"><div class="label">Run time</div><div class="big">${fHM(rn.minutes)}</div></div>
          <div class="card"><div class="label">${hint("easy", "Easy (Z1–Z2)")}</div><div class="big">${rn.easy_pct != null ? Math.round(rn.easy_pct) : "–"}<small>%</small></div></div>
          <div class="card"><div class="label">Workouts</div><div class="big">${rs.runs.length}</div><div class="note">all types</div></div>
        </div>
        <section><div class="card"><div class="chart-title"><h3>${hint("strain", "Daily Strain")}</h3><span class="note">dashed: moderate 10, high 14, all out 18</span>${eventNote(tr.annotations)}</div><div id="c-strain"></div></div></section>
        <section><h2>Workouts</h2>
          <div class="head" style="margin-bottom:10px"><div class="seg" id="wt"><button data-v="0" class="${!state.runsOnly ? "on" : ""}">All</button><button data-v="1" class="${state.runsOnly ? "on" : ""}">Runs</button></div></div>
          <div class="tablewrap"><table data-pages="workouts">
          <tr><th>Date</th><th>Type</th><th>Name</th><th class="num">Time</th><th class="num">km</th><th class="num">Pace</th><th class="num">Avg HR</th><th class="num">${hint("easy", "Z1–Z2")}</th><th class="num">${hint("drift", "Drift")}</th><th class="num">${hint("ef", "EF")}</th><th class="num">${hint("trimp", "TRIMP")}</th><th class="num">${hint("strain", "Strain")}</th></tr>
          ${list.map(r => `<tr class="click" data-id="${r.id}"><td>${fDay(r.date)}</td><td>${typeName(r.type)}</td><td>${esc(r.name)}</td><td class="num">${fHM(r.minutes)}</td><td class="num">${r.km ? n2(r.km) : "–"}</td><td class="num">${r.km && PACED.has(r.type) ? fPace(r.minutes * 60 / r.km) : "–"}</td><td class="num">${n0(r.avg_hr)}</td><td class="num">${pct(r.pct_below_vigorous)}</td><td class="num">${r.decoupling_pct != null ? sgn(r.decoupling_pct, 1) + "%" : "–"}</td><td class="num">${n2(r.ef)}</td><td class="num">${n0(r.trimp)}</td><td class="num">${n1(r.strain)}</td></tr>`).join("") || '<tr><td colspan="12" class="note">No workouts in this range.</td></tr>'}
          </table></div></section>
        <section><h2>Weekly load</h2><div class="grid g2">
          <div class="card"><div class="chart-title"><h3>${hint("fitness", "Fitness")} and ${hint("fatigue", "fatigue")}</h3></div>${legend([["Fitness (42 d)", "var(--ink)"], ["Fatigue (7 d)", "var(--ink-2)"], ["Weekly TRIMP", "var(--line)"]])}<div id="c-load"></div></div>
          ${chartBox("c-acwr", hint("acwr", "Acute:chronic load"), ac.length ? n2(ac[ac.length - 1].acwr) : "", "from the latest Takeout export")}
        </div>
        <div class="tablewrap" style="margin-top:16px"><table data-pages="tr-weeks"><tr><th>Week</th><th class="num">Workouts</th><th class="num">${hint("trimp", "TRIMP")}</th><th class="num">${hint("fitness", "Fitness")}</th><th class="num">${hint("fatigue", "Fatigue")}</th><th class="num">${hint("form", "Form")}</th><th class="num">${hint("easy", "Z1–Z2 (easy)")}</th><th class="num">Z1 only</th><th class="num">${hint("sri", "Sleep regularity")}</th></tr>
          ${weeks.slice().reverse().map(w => `<tr><td>${fWeek(w.monday)}${w.partial ? ' <span class="pill">so far</span>' : ""}</td><td class="num">${w.workouts}</td><td class="num">${n0(w.trimp)}</td><td class="num">${n1(w.fitness)}</td><td class="num">${n1(w.fatigue)}</td><td class="num">${sgn(w.form, 1)}</td><td class="num">${pct(w.pct_below_vigorous)}</td><td class="num">${pct(w.pct_below_first)}</td><td class="num">${n0(w.sri)}</td></tr>`).join("")}
        </table></div></section>
        <section><h2>Personal records</h2><p class="note">From Google (latest Takeout export).</p><div class="tablewrap"><table data-pages="records"><tr><th>Record</th><th class="num">Value</th><th class="num">Pace</th><th>Date</th></tr>
          ${tr.records.map(p => `<tr><td>${esc(p.record)}</td><td class="num">${p.seconds != null ? fDur(p.seconds) : n2(p.km) + " km"}</td><td class="num">${p.seconds != null && p.km ? fPace(p.seconds / p.km) + " /km" : "–"}</td><td>${fDay(p.date)}</td></tr>`).join("")}
        </table></div></section>`,
      after() {
        bindRange();
        view.querySelectorAll("#wt button").forEach(b => b.onclick = () => { state.runsOnly = b.dataset.v === "1"; store.set("runsOnly", state.runsOnly); render(true); });
        view.querySelectorAll("tr[data-id]").forEach(tr => tr.onclick = () => location.hash = `#/training/w/${tr.dataset.id}`);
        mount("c-strain", { x: xs, height: 180, y: { min: 0, max: 21, ticks: [0, 7, 14, 21] }, shades: sh, xfmt: fShort, tipx: fDay,
          refs: [{ y: 10 }, { y: 14 }, { y: 18 }],
          series: [{ name: "Strain", type: "bar", data: pick(xs, smap, r => r.strain), fmt: n1 }] });
        const wx = weeks.map(w => w.monday);
        mount("c-load", { x: wx, height: 170, xfmt: fShort, tipx: fWeek, series: [
          { name: "TRIMP / 7", type: "bar", color: "var(--line)", data: weeks.map(w => w.trimp / 7), fmt: n1 },
          { name: "Fitness", type: "line", data: weeks.map(w => w.fitness), fmt: n1 },
          { name: "Fatigue", type: "line", color: "var(--ink-2)", data: weeks.map(w => w.fatigue), fmt: n1 }] });
        mount("c-acwr", { x: ax, height: 170, band: { lo: 0.8, hi: 1.3 }, xfmt: fShort, tipx: fDay,
          series: [{ name: "ACWR", type: "line", data: pick(ax, amap, r => r.acwr), fmt: n2 }] });
      },
    };
  },
};

const niceTicksBy = (lo, hi, step) => Array.from({ length: Math.round((hi - lo) / step) + 1 }, (_, k) => lo + k * step);

async function workoutView(id) {
  const [w, s] = await Promise.all([get(`/runs/${id}`), get(`/workout-series/${id}`).catch(() => null)]);
  const a = w.analysis, g = w.google || {}, ms = g.metrics_summary || {}, mob = ms.mobilityMetrics || {};
  // the API also sends day/night annotations (alcohol, illness); only notes on the run itself belong here
  const runAnns = w.annotations.filter(x => (x.target || "").startsWith("workout:"));
  // minutes per zone come from the backend (health_core/zones.py): Z3 and Z4
  // split Google's vigorous zone in the middle; to_bpm is exclusive
  const zoneBpm = z => z.from_bpm == null && z.to_bpm == null ? "" : z.from_bpm == null ? `< ${z.to_bpm}`
    : z.to_bpm == null ? `${z.from_bpm}+` : `${z.from_bpm}–${z.to_bpm - 1}`;
  const pace = a.km && PACED.has(a.type) ? a.minutes * 60 / a.km : null;
  const splitHr = (g.km_splits || []).some(k => k.avg_hr != null);
  // pace, heart rate and cadence share one time axis; walking breaks (cadence
  // under a jog) are shaded on all three and left out of the running figures
  const hr = s ? s.hr.filter(p => p.s >= 0).map(p => ({ s: p.s, v: p.bpm })) : [];
  const paceS = s?.pace || [], cadS = s?.cadence || [], walks = s?.walks || [];
  const walking = x => walks.some(w => x >= w.from && x < w.to);
  const walkS = walks.reduce((t, w) => t + w.to - w.from, 0);
  const runPace = paceS.filter(p => p.v && !walking(p.s)).map(p => 1000 / p.v);   // speeds, averaged as speeds
  const runCad = cadS.filter(p => !walking(p.s)).map(p => p.v);
  const pv = paceS.map(p => p.v).filter(v => v).sort((x, y) => x - y);
  const pq = q => pv[Math.min(pv.length - 1, Math.floor(q * pv.length))];
  const pLo = pv.length ? Math.floor(pq(0.02) / 60) * 60 : 0, pHi = pv.length ? Math.ceil(pq(0.98) / 60) * 60 : 0;
  const pStep = pHi - pLo > 360 ? 120 : 60;
  const fT = v => `${Math.floor(v / 60)}:${pad2(Math.round(v % 60))}`;
  const PACE_SRC = { watch: "from the watch's distance", gps: "from the GPS track" };
  const CAD_SRC = { watch: "30 s averages", "steps per minute": "watch steps per minute" };
  const runOnly = walks.length ? `, walking breaks shaded (${fDur(walkS)} in total) and left out of the average` : "";
  return {
    html: `
      <div class="head"><a href="#/training" class="note">‹ Training</a></div>
      <div class="head"><h1>${esc(a.name)}</h1><span class="sub">${typeName(a.type)} · ${fDay(a.date)}${g.start_time ? " · " + fClock(g.start_time) : ""}</span></div>
      <div class="grid g5">
        <div class="card"><div class="label">Distance</div><div class="big">${a.km ? n2(a.km) : "–"}<small>${a.km ? " km" : ""}</small></div></div>
        <div class="card"><div class="label">Time</div><div class="big">${fDur(a.minutes * 60)}</div></div>
        <div class="card"><div class="label">Pace</div><div class="big">${fPace(pace)}<small>${pace ? " /km" : ""}</small></div></div>
        <div class="card"><div class="label">Avg HR</div><div class="big">${n0(a.avg_hr)}<small> bpm</small></div><div class="note">max ${n0(a.max_hr)}</div></div>
        <div class="card"><div class="label">${hint("strain", "Strain")}</div><div class="big">${n1(w.strain)}</div><div class="note">${hint("trimp", "TRIMP")} ${n0(a.trimp)}</div></div>
      </div>
      <section><div class="card runcharts">
        <div class="chart-title"><h3>Pace</h3><span class="now">${runPace.length ? fPace(1000 / mean(runPace)) + " /km" : ""}</span></div>
        <div class="note csub">${paceS.length ? `min/km over 30 s, ${PACE_SRC[s.pace_source] || ""}${runOnly}` : "No distance samples for this workout."}</div>
        ${paceS.length ? '<div id="c-pace"></div>' : ""}
        <div class="chart-title"><h3>Heart rate</h3><span class="now">${hr.length ? n0(mean(hr.map(p => p.v))) + " bpm" : ""}</span></div>
        <div id="c-whr"></div>
        ${cadS.length ? `<div class="chart-title"><h3>${hint("cadence", "Cadence")}</h3><span class="now">${runCad.length ? n0(mean(runCad)) + " spm" : ""}</span></div>
        <div class="note csub">steps/min, ${CAD_SRC[s.cadence_source] || ""}${walks.length ? ", running only" : ""}</div>
        <div id="c-cad"></div>` : ""}
      </div></section>
      <section><div class="grid g3">
        <div class="card"><h3>${hint("zones", "Heart rate zones")}</h3>
          ${w.hr_zones.zones.map(z => kv(`${z.zone}${zoneBpm(z) ? ` <span class="note">${zoneBpm(z)} bpm</span>` : ""}`, z.minutes != null ? `${n1(z.minutes)} min · ${pct(z.pct)}` : "–")).join("")}</div>
        <div class="card"><h3>Efficiency</h3>
          ${kv(hint("drift", "Aerobic decoupling"), a.decoupling_pct != null ? sgn(a.decoupling_pct, 1) + "%" : "–")}${kv(hint("ef", "Efficiency factor"), n2(a.ef))}
          ${kv(hint("hrr", "HR recovery 60 s"), a.hrr60 != null ? n0(a.hrr60) + " bpm" : "–")}${kv(hint("hrr", "HR recovery 120 s"), a.hrr120 != null ? n0(a.hrr120) + " bpm" : "–")}
          ${kv(hint("steady", "Steady pace"), a.steady == null ? "–" : a.steady ? "yes" : "no")}${kv(hint("split_cv", "Split variation"), a.split_cv != null ? n1(a.split_cv) + "%" : "–")}
          ${kv(hint("climb", "Climb"), a.climb_m_per_km != null ? n1(a.climb_m_per_km) + " m/km" : "–")}</div>
        <div class="card"><h3>Running form</h3>
          ${kv(hint("cadence", "Cadence"), mob.avgCadenceStepsPerMinute ? mob.avgCadenceStepsPerMinute + " spm" : "–")}${kv(hint("stride", "Stride"), mob.avgStrideLengthMillimeters ? n0(mob.avgStrideLengthMillimeters / 10) + " cm" : "–")}
          ${kv(hint("vert_osc", "Vertical oscillation"), mob.avgVerticalOscillationMillimeters ? n1(mob.avgVerticalOscillationMillimeters / 10) + " cm" : "–")}${kv(hint("vert_ratio", "Vertical ratio"), mob.avgVerticalRatio ? n1(mob.avgVerticalRatio) + "%" : "–")}
          ${kv(hint("gct", "Ground contact"), mob.avgGroundContactTimeDuration ? n0(parseFloat(mob.avgGroundContactTimeDuration) * 1000) + " ms" : "–")}
          ${kv("Elevation gain", ms.elevationGainMillimeters ? n0(ms.elevationGainMillimeters / 1000) + " m" : "–")}${kv("Calories", n0(g.calories))}${kv("Steps", n0(g.steps))}</div>
      </div></section>
      ${s?.route ? `<section><div class="card"><h3>Route</h3><div id="c-route"></div></div></section>` : ""}
      ${(g.km_splits || []).length ? `<section><h2>Kilometre splits</h2><div class="tablewrap"><table><tr><th>km</th><th class="num">Pace</th>${splitHr ? '<th class="num">Avg HR</th>' : ""}</tr>
        ${g.km_splits.map(k => `<tr><td>${k.km}</td><td class="num">${fPace(k.pace_s_per_km)}</td>${splitHr ? `<td class="num">${n0(k.avg_hr)}</td>` : ""}</tr>`).join("")}</table></div></section>` : ""}
      ${a.notes || runAnns.length ? `<section><h2>Notes</h2>${a.notes ? `<p>${esc(a.notes)}</p>` : ""}${runAnns.map(x => `<p class="note">${esc(x.kind)} (${esc(x.status)}): ${esc(x.note || "")}</p>`).join("")}</section>` : ""}`,
    after() {
      // time ticks on whole minutes, about one per 90 px
      const tStep = [60, 120, 300, 600, 900, 1800].find(t => (s?.duration || 0) / t <= document.querySelector(".runcharts").clientWidth / 90) || 3600;
      const axis = { numeric: true, xmin: 0, xmax: s?.duration, height: 150, xfmt: fT, sync: "run",
        xticks: Array.from({ length: Math.floor((s?.duration || 0) / tStep) + 1 }, (_, k) => k * tStep),
        shades: walks.map(w => ({ ...w, label: "walking" })) };
      const tipWalk = xs => i => walking(xs[i]) ? '<div><span>walking</span></div>' : "";
      if (paceS.length) {
        const xs = paceS.map(p => p.s);
        mount("c-pace", { ...axis, x: xs, gap: 30, tipExtra: tipWalk(xs),
          y: { invert: true, fmt: fPace, min: pLo, max: pHi, ticks: Array.from({ length: (pHi - pLo) / pStep + 1 }, (_, k) => pLo + k * pStep) },
          series: [{ name: "pace", type: "line", width: 1.3, data: paceS.map(p => p.v), fmt: v => v ? fPace(v) + " /km" : "stopped" }] });
      }
      if (hr.length) {
        const xs = hr.map(p => p.s);
        const hv = hr.map(p => p.v), hLo = Math.floor(Math.min(...hv) / 20) * 20, hHi = Math.ceil(Math.max(...hv) / 20) * 20;
        mount("c-whr", { ...axis, x: xs, gap: 120, tipExtra: tipWalk(xs), y: { min: hLo, max: hHi, fmt: n0, ticks: niceTicksBy(hLo, hHi, hHi - hLo > 100 ? 40 : 20) }, series: [{ name: "heart rate", type: "line", width: 1.3, data: hr.map(p => p.v), fmt: v => n0(v) + " bpm" }] });
      } else document.getElementById("c-whr").innerHTML = '<div class="empty">No heart-rate samples.</div>';
      if (cadS.length) {
        const xs = cadS.map(p => p.s);
        mount("c-cad", { ...axis, x: xs, tipExtra: i => walking(xs[i]) ? `<div><span>walking</span><b>${cadS[i].v} spm</b></div>` : "",
          series: [{ name: "cadence", type: "line", width: 1.3, data: cadS.map(p => walking(p.s) ? null : p.v), fmt: v => v != null ? v + " spm" : "–" }] });
      }
      if (s?.route) drawRoute(document.getElementById("c-route"), s.route);
    },
  };
}

// ============================================================ long-term ====

const HS = { vo2max: "VO2 max", rhr: "Resting HR", steps: "Steps", moderate: "Moderate activity", vigorous: "Vigorous activity",
  strength: "Strength training", sleep_h: "Sleep", sri: "Sleep regularity" };
const MONTHLY = [["recovery", "Recovery %", n0], ["hrv_ms", "HRV ms", n1], ["rhr", "Resting HR", n1], ["sleep_h", "Sleep h", n1],
  ["sleep_performance", "Sleep Performance", n0], ["strain", "Strain", n1], ["steps", "Steps / day", n0], ["vo2max", "VO2 max", n1], ["weight", "Weight kg", n1]];
const HS_HINT = { vo2max: "vo2max", rhr: "rhr", moderate: "active_min", vigorous: "active_min", sri: "sri" };
const MONTHLY_HINT = { recovery: "recovery", hrv_ms: "hrv", rhr: "rhr", sleep_performance: "sleep_perf", strain: "strain", vo2max: "vo2max" };

VIEWS.longterm = {
  async load(args) {
    if (args[0] === "vo2max") return vo2View();
    const [lt, m] = await Promise.all([get("/longterm"), META]);
    const kinds = m?.annotation.kinds || ["note"];
    const hs = lt.healthspan, variants = Object.keys(hs.variants);
    const v = variants.includes(state.hsVariant) ? state.hsVariant : variants[0];
    const rows = (hs.variants[v] || []);
    const age = rows.find(r => r.metric === "healthspan_age"), poa = rows.find(r => r.metric === "pace_of_aging");
    const parts = rows.filter(r => HS[r.metric]).sort((a, b) => (b.years_adjusted ?? 0) - (a.years_adjusted ?? 0));
    const monthN = {};
    Object.values(lt.monthly).flat().forEach(r => { monthN[r.month] = Math.max(monthN[r.month] || 0, r.n); });
    const months = Object.keys(monthN).filter(m => monthN[m] >= 3).sort();   // drop months with a stray day or two
    const vo2 = lt.vo2max, vx = vo2.map(r => r.date), rv = byKey(lt.run_vo2max);
    const wt = lt.weight, sh = shades(lt.annotations);
    const VN = { all: "All days", excl_illness: "Without illness", last30: "Last 30 days", last30_excl_illness: "Last 30, without illness" };
    return {
      html: `
        <div class="head"><h1>Long-term</h1></div>
        <div class="grid g2">
          <div class="card hs"><div class="label">${hint("healthspan", "Healthspan age")}</div>
            <div class="big">${age ? n1(age.value) : "–"}<small> years${age?.referent != null ? ` · actual ${n0(age.referent)}` : ""}</small></div>
            ${poa ? kv(hint("pace_of_aging", "Pace of aging"), n2(poa.value) + "× " + `<span class="note">(${esc(poa.note || "")})</span>`) : ""}
            ${kv("Based on", esc(age?.note || "–"))}
            <div class="spacer"></div><div class="seg" id="hsv" style="margin-top:10px">${variants.map(x => `<button data-v="${x}" class="${x === v ? "on" : ""}">${VN[x] || x}</button>`).join("")}</div>
            <p class="note">Computed ${fDay(hs.computed_on)}. Each line: your average over the period above and the target. Averages lag behind today's values (VO2 max today: ${vo2.length ? n1(vo2[vo2.length - 1].vo2_max) : "–"}).</p></div>
          <div class="card"><h3>What moves it</h3><div class="tablewrap"><table><tr><th>Factor</th><th class="num">Your average</th><th class="num">Target</th><th class="num">${hint("hs_years", "Years")}</th></tr>
            ${parts.map(r => `<tr><td>${HS_HINT[r.metric] ? hint(HS_HINT[r.metric], HS[r.metric]) : HS[r.metric]}</td><td class="num">${r.metric === "steps" ? n0(r.value) : n1(r.value)} <span class="note">${esc(r.unit)}</span></td><td class="num">${esc(String(r.referent ?? "–").replace(/[()]/g, "").replace(", ", "–"))}</td><td class="num">${sgn(r.years_adjusted, 2)}</td></tr>`).join("")}
          </table></div></div>
        </div>
        <section><div class="grid g2">
          <a class="card" href="#/longterm/vo2max"><div class="chart-title"><h3>${hint("vo2max", "VO2 max")} ›</h3><span class="note">line: Google daily · dots: run estimates · dashed: healthspan target</span><span class="now">${vo2.length ? "today " + n1(vo2[vo2.length - 1].vo2_max) : ""}</span></div><div id="c-vo2"></div></a>
          <div class="card"><div class="chart-title"><h3>Weight</h3>${lt.height_cm ? `<span class="note">height ${lt.height_cm} cm</span>` : ""}<span class="now">${wt.length ? n1(wt[wt.length - 1].weight_kg) + " kg" : ""}</span></div><div id="c-wt"></div></div>
        </div></section>
        <section><h2>Monthly averages</h2><div class="tablewrap"><table><tr><th></th>${months.map(m => `<th class="num">${fMonth(m)}</th>`).join("")}</tr>
          ${MONTHLY.map(([k, l, f]) => { const mm = byKey(lt.monthly[k] || [], "month"); return `<tr><td>${MONTHLY_HINT[k] ? hint(MONTHLY_HINT[k], l) : l}</td>${months.map(m => `<td class="num" title="${mm[m] ? mm[m].n + " days" : ""}">${mm[m] ? f(mm[m].mean) : ""}</td>`).join("")}</tr>`; }).join("")}
        </table></div><p class="note">Hover a value for the number of days behind it. Months with only a few days of data are less reliable.</p></section>
        <section><h2>Events</h2><p class="note">Context the watch can't know. Confirmed illness and alcohol nights are left out of your baselines (scores recompute automatically).</p>
          <div class="tablewrap"><table data-pages="events"><tr><th>Kind</th><th>From</th><th>To</th><th>Status</th><th>Note</th><th></th></tr>
            ${lt.annotations.map(a => `<tr><td>${esc(a.kind)}</td><td>${fDay(a.start.slice(0, 10))}</td><td>${a.end ? fDay(a.end.slice(0, 10)) : ""}</td><td>${esc(a.status)}</td><td style="white-space:normal">${esc(a.note || "")}</td>
              <td>${a.status !== "confirmed" ? `<button class="btn small" data-a="confirm" data-id="${a.id}">Confirm</button> ` : ""}${a.status === "suggested" ? `<button class="btn small" data-a="reject" data-id="${a.id}">Reject</button> ` : ""}<button class="btn small" data-a="delete" data-id="${a.id}">Delete</button></td></tr>`).join("") || '<tr><td colspan="6" class="note">No events.</td></tr>'}
          </table></div>
          <form class="form" id="addev" style="margin-top:12px">
            <label>Kind<select name="kind">${kinds.map(k => `<option>${esc(k)}</option>`).join("")}</select></label>
            <label>From<input type="date" name="start" required max="${today()}"></label>
            <label>To (optional)<input type="date" name="end"></label>
            <label>Note<input name="note" placeholder="what happened"></label>
            <button class="btn primary cta">Add<span class="ico" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg></span></button>
            <span class="note" id="addmsg"></span>
          </form><p class="note">Alcohol: pick the evening you drank; the night after it is the one left out.</p></section>
        <section><h2>ECG recordings</h2><div class="tablewrap"><table data-pages="ecg"><tr><th>Date</th><th>Time</th><th class="num">Avg HR</th><th>Result</th><th class="num">Length</th></tr>
          ${lt.ecg.map(e => `<tr class="click" data-ecg="${esc(e.reading_id.split("/").pop())}"><td>${fDay(e.date)}</td><td>${fClock(e.start_time)}</td><td class="num">${e.avg_bpm}</td><td>${esc((e.classification || "").replace(/_/g, " ").toLowerCase())}</td><td class="num">${n0(e.duration_sec)} s</td></tr>`).join("") || '<tr><td colspan="5" class="note">No ECGs.</td></tr>'}
        </table></div><div id="ecg" style="margin-top:12px"></div></section>
        ${lt.assessments.length ? `<section><h2>Google assessments</h2><p class="note">From Takeout.</p><div class="tablewrap"><table data-pages="assess"><tr><th>Date</th><th>Assessment</th><th>Result</th></tr>
          ${lt.assessments.map(a => `<tr><td>${fDay(a.date)}</td><td>${esc(a.kind.replace(/_/g, " "))}</td><td>${esc((a.classification || "").replace(/_/g, " ").toLowerCase())}</td></tr>`).join("")}</table></div></section>` : ""}`,
      after() {
        view.querySelectorAll("#hsv button").forEach(b => b.onclick = () => { state.hsVariant = b.dataset.v; store.set("hsVariant", b.dataset.v); render(true); });
        const target = (hs.variants[v] || []).find(r => r.metric === "vo2max")?.referent;
        mount("c-vo2", { x: vx, height: 180, xfmt: fShort, tipx: fDay, shades: sh, refs: target ? [{ y: target, label: "target " + target }] : [],
          series: [{ name: "Google", type: "line", noDots: true, data: vo2.map(r => r.vo2_max), fmt: n1 },
            { name: "Run", type: "dot", color: "var(--ink-2)", r: 3, data: vx.map(d => rv[d]?.vo2max ?? null), fmt: n1 }] });
        const wx = wt.length ? dateList(wt[0].date, today()) : [];
        const wm = byKey(wt);
        mount("c-wt", { x: wx, height: 180, xfmt: fShort, tipx: fDay,
          series: [{ name: "kg", type: "line", connect: true, noDots: true, color: "var(--ink-3)", data: wx.map(d => wm[d]?.weight_kg ?? null), noTip: true },
            { name: "kg", type: "dot", r: 3.5, data: wx.map(d => wm[d]?.weight_kg ?? null), fmt: n1 }] });
        view.querySelectorAll("button[data-a]").forEach(b => b.onclick = async () => {
          if (b.dataset.a === "delete" && !confirm("Delete this event?")) return;
          const r = b.dataset.a === "delete" ? await send("DELETE", `/annotations/${b.dataset.id}`) : await send("POST", `/annotations/${b.dataset.id}/${b.dataset.a}`);
          if (r.recompute) followRun(r.recompute);
          render(true);
        });
        document.getElementById("addev").onsubmit = async e => {
          e.preventDefault();
          const f = Object.fromEntries(new FormData(e.target));
          const body = { kind: f.kind, start: f.start, end: f.end || null, note: f.note || null, status: "confirmed", source: "user" };
          if (f.kind === "alcohol") { body.end = addDays(f.start, 1); body.target = "night:" + addDays(f.start, 1); }
          try {
            const r = await send("POST", "/annotations", body);
            if (r.recompute) followRun(r.recompute);
            render(true);
          } catch (err) { document.getElementById("addmsg").textContent = err.message; }
        };
        view.querySelectorAll("tr[data-ecg]").forEach(tr => tr.onclick = () => showEcg(tr.dataset.ecg));
      },
    };
  },
};

// VO2 max detail: one source only, Google's daily value (cardio_fitness), so
// current, lowest and highest always agree with the chart. Run estimates stay
// on the Long-term card; Healthspan averages the same daily values.
async function vo2View() {
  const lt = await get("/longterm");
  const [fr, to] = rangeDates();
  const all = lt.vo2max.filter(r => r.vo2_max != null);
  const rows = all.filter(r => r.date >= fr && r.date <= to);
  const xs = xDates(fr, to, rows), map = byKey(rows);
  const last = all[all.length - 1], first = rows[0];
  const ext = cmp => rows.reduce((b, r) => b && !cmp(r.vo2_max, b.vo2_max) ? b : r, null);   // first day reaching it
  const lo = ext((a, b) => a < b), hi = ext((a, b) => a > b);
  const changes = all.map((r, i) => ({ ...r, d: i ? r.vo2_max - all[i - 1].vo2_max : null }))
    .filter((r, i) => r.date >= fr && (i === 0 || Math.abs(r.d) >= 0.005)).reverse();
  const hsv = lt.healthspan.variants, v = hsv[state.hsVariant] ? state.hsVariant : Object.keys(hsv)[0];
  const hs = (hsv[v] || []).find(r => r.metric === "vo2max");
  const level = s => esc((s || "").replace(/_/g, " ").toLowerCase());
  const stat = (label, r, sub) => `<div class="card"><div class="label">${label}</div><div class="big">${r ? n1(r.vo2_max) : "–"}</div><div class="note">${sub}</div></div>`;
  return {
    html: `
      <div class="head"><a href="#/longterm" class="note">‹ Long-term</a></div>
      <div class="head"><h1>VO2 max</h1><span class="sub">${hint("vo2_source", "Google daily estimate")} · ml/kg/min</span><span class="spacer"></span>${rangeSeg()}</div>
      <div class="grid g5 three">
        ${stat(hint("vo2max", "Current"), last, last ? `${fShort(last.date)}${last.cardio_fitness_level ? " · " + hint("vo2_level", level(last.cardio_fitness_level)) : ""}` : "")}
        ${stat(hint("vo2_range", "Lowest"), lo, lo ? fShort(lo.date) : "")}
        ${stat(hint("vo2_range", "Highest"), hi, hi ? fShort(hi.date) : "")}
      </div>
      <section><div class="card"><div class="chart-title"><h3>Course</h3>${eventNote(lt.annotations)}<span class="now">${first && last && rows.length > 1 ? sgn(last.vo2_max - first.vo2_max, 1) : ""}</span></div>
        <div class="note csub">${rows.length ? `${rows.length} days since ${fShort(first.date)}; the change on the right is first day to today.` : ""}</div><div id="c-vo2d"></div></div></section>
      <section><h2>Changes</h2><p class="note">Days the value moved.</p><div class="tablewrap"><table data-pages="vo2">
        <tr><th>Day</th><th class="num">VO2 max</th><th class="num">Change</th><th>${hint("vo2_level", "Level")}</th></tr>
        ${changes.map(r => `<tr><td>${fDay(r.date)}</td><td class="num">${n2(r.vo2_max)}</td><td class="num">${r.d == null ? "first" : sgn(r.d)}</td><td>${level(r.cardio_fitness_level)}</td></tr>`).join("") || '<tr><td colspan="4" class="note">No values in this range.</td></tr>'}
      </table></div>
      <p class="note">${hs ? `${hint("healthspan", "Healthspan")} uses the average of these daily values (${n1(hs.value)} for "${esc(v.replace(/_/g, " "))}"), target ${esc(hs.referent)}. ` : ""}Run-by-run estimates are on the Long-term tab.</p></section>`,
    after() {
      bindRange();
      // a flat week would get ticks a few hundredths apart; keep at least two units on the axis
      const y = lo && hi.vo2_max - lo.vo2_max < 1 ? { min: Math.floor(lo.vo2_max - 0.5), max: Math.ceil(hi.vo2_max + 0.5) } : undefined;
      mount("c-vo2d", { x: xs, height: 220, y, xfmt: fShort, tipx: fDay, shades: shades(lt.annotations),
        series: [{ name: "VO2 max", type: "line", connect: true, noDots: true, data: pick(xs, map, r => r.vo2_max), fmt: n1 }] });
    },
  };
}

async function showEcg(id) {
  const box = document.getElementById("ecg");
  box.innerHTML = '<div class="note">Loading…</div>';
  const e = await get(`/ecg/${id}`);
  const hz = e.sampling_hz || 250, wf = e.waveform, per = hz * 10;
  const strips = [];
  for (let i = 0; i < wf.length; i += per) strips.push(wf.slice(i, i + per));
  box.innerHTML = `<div class="card"><h3>ECG ${fDay(e.date)} ${fClock(e.start_time)} · ${e.avg_bpm} bpm · ${esc((e.classification || "").replace(/_/g, " ").toLowerCase())}</h3>
    ${strips.map((_, i) => `<div id="ecg-${i}"></div>`).join("")}<p class="note">Three 10-second strips, ${hz} Hz.</p></div>`;
  const lo = Math.min(...wf), hi = Math.max(...wf);
  strips.forEach((s, i) => mount(`ecg-${i}`, { x: s.map((_, j) => i * 10 + j / hz), numeric: true, height: 110, y: { min: lo, max: hi, ticks: [], fmt: () => "" },
    xfmt: v => v.toFixed(0) + " s", series: [{ name: "", type: "line", width: 1, data: s, noTip: true }] }));
}

// ================================================================= data ====

VIEWS.data = {
  async load(args, q) {
    if (args.length >= 2) return tableView(args[0], args[1], q);
    const cat = await get("/data");
    const groups = {};
    cat.tables.forEach(t => (groups[t.group] = groups[t.group] || []).push(t));
    const order = ["Scores (computed)", "Analysis (computed)", "Google Health daily (MCP cache)", "Watch daily (health_sync)", "Watch samples (intraday)", "Google Takeout", "Events", "Sync bookkeeping"];
    const names = Object.keys(groups).sort((a, b) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99);
    const total = cat.tables.reduce((s, t) => s + t.rows, 0);
    return {
      html: `<div class="head"><h1>Data</h1><span class="sub">${cat.tables.length} tables · ${n0(total)} rows · read-only</span></div>
        <div class="catalog">${names.map(g => `<h3>${esc(g)}</h3><div class="tablewrap"><table><tr><th>Table</th><th class="num">Rows</th><th>From</th><th>To</th><th>Columns</th></tr>
          ${groups[g].map(t => `<tr class="click" data-href="#/data/${t.source}/${t.table}"><td><b>${esc(t.table)}</b> <span class="note">${t.source === "google" ? "MCP cache" : "health.db"}</span></td><td class="num">${n0(t.rows)}</td><td>${t.first ? fShort(t.first) : ""}</td><td>${t.last ? fShort(t.last) : ""}</td><td class="note" style="white-space:normal">${esc(t.columns.join(", "))}</td></tr>`).join("")}
          </table></div>`).join("")}</div>`,
      after() { view.querySelectorAll("tr[data-href]").forEach(tr => tr.onclick = () => location.hash = tr.dataset.href); },
    };
  },
};

async function tableView(source, table, q) {
  const limit = 200, offset = +(q.get("offset") || 0), fr = q.get("from") || "", to = q.get("to") || "", asc = q.get("asc") === "1";
  const p = new URLSearchParams({ limit, offset, desc: !asc });
  if (fr) p.set("from", fr);
  if (to) p.set("to", to);
  const t = await get(`/data/${source}/${table}?${p}`);
  const link = o => { const n = new URLSearchParams({ offset: o.offset ?? offset }); if ((o.from ?? fr)) n.set("from", o.from ?? fr); if ((o.to ?? to)) n.set("to", o.to ?? to); if (o.asc ?? asc) n.set("asc", "1"); return `#/data/${source}/${table}?${n}`; };
  const cell = v => v == null ? '<span class="note">null</span>' : esc(typeof v === "object" ? JSON.stringify(v) : v);
  return {
    html: `<div class="head"><a href="#/data" class="note">‹ Data</a></div>
      <div class="head"><h1>${esc(table)}</h1><span class="sub">${source === "google" ? "google-health MCP cache" : "health.db"} · ${n0(t.total)} rows</span></div>
      <form class="form" id="flt" style="margin-bottom:12px">
        ${t.date_column ? `<label>From<input type="date" name="from" value="${fr}"></label><label>To<input type="date" name="to" value="${to}"></label><button class="btn">Filter</button>
        <a class="btn" href="${link({ asc: !asc, offset: 0 })}" style="text-decoration:none">${asc ? "Oldest first" : "Newest first"}</a>` : ""}
        <span class="spacer"></span>
        <span class="note">${n0(Math.min(t.total, offset + 1))}–${n0(Math.min(t.total, offset + t.rows.length))} of ${n0(t.total)}</span>
        ${offset > 0 ? `<a class="btn" style="text-decoration:none" href="${link({ offset: Math.max(0, offset - limit) })}">‹ Prev</a>` : ""}
        ${offset + limit < t.total ? `<a class="btn" style="text-decoration:none" href="${link({ offset: offset + limit })}">Next ›</a>` : ""}
        <button class="btn" type="button" id="csv">CSV (this page)</button>
      </form>
      <div class="tablewrap" style="max-height:70vh"><table class="data"><tr>${t.columns.map(c => `<th>${esc(c)}</th>`).join("")}</tr>
        ${t.rows.map(r => `<tr>${t.columns.map(c => `<td>${cell(r[c])}</td>`).join("")}</tr>`).join("") || `<tr><td class="note">No rows.</td></tr>`}</table></div>
      <p class="note">Times with _local are local clock time. Long text (routes, ECG waveforms, JSON) is cut here; the full values stay in the database.</p>`,
    after() {
      const f = document.getElementById("flt");
      f.onsubmit = e => { e.preventDefault(); const d = Object.fromEntries(new FormData(f)); location.hash = link({ from: d.from || "", to: d.to || "", offset: 0 }); };
      document.getElementById("csv").onclick = () => {
        const q = v => v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);
        const csv = [t.columns.join(","), ...t.rows.map(r => t.columns.map(c => q(r[c])).join(","))].join("\n");
        const a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
        a.download = `${table}_${offset}.csv`;
        a.click();
      };
    },
  };
}

// ================================================================= sync ====

const syncEl = document.getElementById("sync"), syncText = document.getElementById("sync-text"), syncBtn = document.getElementById("sync-btn");
// a sync step's label and the number of steps a job runs, from /meta
const stepLabel = name => Object.values(meta?.sync.steps || {}).flat().find(s => s.name === name)?.label || name;
const stepCount = reason => meta?.sync.steps[reason]?.length;
let status = null, following = false;
// the phone top bar mirrors the sidebar's sync state in its dot
const topDot = document.getElementById("top-dot");
new MutationObserver(() => { topDot.className = "dot " + [...syncEl.classList].filter(c => c !== "sync").join(" "); topDot.title = syncText.textContent; })
  .observe(syncEl, { attributes: true, attributeFilter: ["class"] });

function paintIdle() {
  syncEl.className = "sync";
  syncBtn.disabled = false;
  if (!status) return;
  const newest = status.newest_data?.health_db?.hr_samples;
  syncText.textContent = `Synced ${ago(status.last_successful_sync)}`;
  syncText.title = `Last successful sync: ${status.last_successful_sync || "never"}\nNewest heart-rate sample: ${newest || "–"}\nNewest Google sleep: ${status.newest_data?.mcp_cache?.sleep || "–"}`;
  const bad = (status.recent_runs || []).find(r => ["sync", "open", "button", "mcp"].includes(r.reason) && !["skipped", "busy"].includes(r.status));
  if (bad && ["failed", "partial", "interrupted"].includes(bad.status) &&
      (!status.last_successful_sync || (bad.finished || "") > status.last_successful_sync)) {
    syncEl.classList.add("err");
    syncText.textContent += " · last attempt " + bad.status;
    syncText.title += `\nLast attempt #${bad.id}: ${bad.status} ${bad.error || ""}`;
  }
}

function paintBusy(text) {
  syncEl.className = "sync busy";
  syncBtn.disabled = true;
  syncText.textContent = text;
}

async function loadStatus() {
  try { status = await fetchJson("/status"); } catch (e) { syncEl.className = "sync err"; syncText.textContent = "API not reachable"; return null; }
  const own = (status.recent_runs || []).find(r => r.status === "running" || r.status === "queued");
  if (!following) status.sync_running ? (own ? fetchJson(`/sync/${own.id}`).then(followRun) : waitForOther()) : paintIdle();
  return status;
}

async function startSync(reason) {
  if (following) return;
  paintBusy("Starting sync…");
  try { await followRun(await send("POST", "/sync", { reason })); }
  catch (e) { following = false; syncEl.className = "sync err"; syncText.textContent = "Sync failed to start"; syncText.title = e.message; syncBtn.disabled = false; }
}

// Poll one sync/recompute job until it ends, then refresh the view.
async function followRun(run) {
  if (run.status === "skipped") { await loadStatus(); return; }
  if (run.status === "busy") return waitForOther();
  following = true;
  try {
    while (["running", "queued"].includes(run.status)) {
      const steps = run.steps || [], cur = steps.find(s => s.status === "running");
      const label = run.reason === "recompute" ? "Recomputing scores" : "Syncing";
      paintBusy(run.status === "queued" ? `${label}: waiting for another sync` :
        `${label}${cur ? " · " + stepLabel(cur.name) : ""} (${steps.filter(s => s.status !== "running").length + 1}${stepCount(run.reason) ? "/" + stepCount(run.reason) : ""})`);
      await sleep(2500);
      run = await fetchJson(`/sync/${run.id}`);
    }
  } finally { following = false; }
  await loadStatus();
  if (run.status !== "ok") {
    syncEl.className = "sync err";
    const failed = (run.steps || []).filter(s => s.status === "failed").map(s => stepLabel(s.name));
    syncText.textContent = `Sync ${run.status}${failed.length ? ": " + failed.join(", ") : ""}`;
    syncText.title = `${run.error || ""}\n${(run.steps || []).filter(s => s.output_tail).map(s => s.name + ":\n" + s.output_tail).join("\n")}`;
  }
  render(true);
}

// Someone else (Claude's SessionStart hook, the MCP) holds the sync lock.
async function waitForOther() {
  if (following) return;
  following = true;
  paintBusy("Syncing (started elsewhere)…");
  try {
    while (true) {
      await sleep(5000);
      const s = await fetchJson("/status").catch(() => null);
      if (s && !s.sync_running) { status = s; break; }
      // recent_runs has no steps, so ask for the running job itself
      const id = s?.recent_runs?.find(r => r.status === "running")?.id;
      const run = id != null ? await fetchJson(`/sync/${id}`).catch(() => null) : null;
      const cur = run?.steps?.find(x => x.status === "running");
      if (cur) paintBusy(`Syncing · ${stepLabel(cur.name)}`);
    }
  } finally { following = false; }
  paintIdle();
  render(true);
}

syncBtn.onclick = () => startSync("button");
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && status && !following &&
      (!status.last_successful_sync || Date.now() - new Date(status.last_successful_sync) > 15 * 60000)) startSync("open");
});
setInterval(() => { if (!following) loadStatus(); }, 60000);

// ---------------------------------------------------------------- start ----

render();
loadStatus().then(s => { if (s && !s.sync_running) startSync("open"); });
