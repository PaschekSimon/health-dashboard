// Dates, numbers and durations as the app shows them. Dates are local
// YYYY-MM-DD strings; a night is named by its wake date.

import type { Annotation } from "../api/types";

type N = number | null | undefined;

export const pad2 = (n: number) => String(n).padStart(2, "0");
export const iso = (d: Date) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
export const today = () => iso(new Date());
export const D = (s: string) => new Date(s + "T12:00:00");
export const addDays = (s: string, n: number) => { const d = D(s); d.setDate(d.getDate() + n); return iso(d); };
export const mondayOf = (s: string) => { const d = D(s); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return iso(d); };
export const fDay = (s: string | null | undefined) =>
  s ? D(s).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" }) : "–";
export const fShort = (s: string | null | undefined) =>
  s ? D(s).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "–";
// a Monday as its Mon–Sun range: "19–25 Oct", "28 Sept – 4 Oct"
export const fWeek = (s: string) => {
  const e = addDays(s, 6);
  return s.slice(0, 7) === e.slice(0, 7) ? `${D(s).getDate()}–${fShort(e)}` : `${fShort(s)} – ${fShort(e)}`;
};
export const fMonth = (s: string) =>
  new Date(s + "-15T12:00:00").toLocaleDateString("en-GB", { month: "short", year: "2-digit" });
export const fClock = (t: string | number | Date | null | undefined) =>
  t ? new Date(t).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "–";
export const fHM = (min: N) => {
  if (min == null) return "–";
  const m = Math.round(min);
  return `${Math.floor(m / 60)}h ${pad2(m % 60)}m`;
};
export const fPace = (s: N) => {
  if (!s) return "–";
  s = Math.round(s);
  return `${Math.floor(s / 60)}:${pad2(s % 60)}`;
};
export const fDur = (s: N) => {
  if (s == null) return "–";
  s = Math.round(s);
  return s >= 3600 ? `${Math.floor(s / 3600)}:${pad2(Math.floor(s % 3600 / 60))}:${pad2(s % 60)}` : `${Math.floor(s / 60)}:${pad2(s % 60)}`;
};
export const PACED = new Set(["RUNNING", "WALKING", "HIKING"]);
export const n0 = (v: N) => v == null ? "–" : Math.round(v).toLocaleString("en-GB");
export const n1 = (v: N) => v == null ? "–" : (+v).toFixed(1);
export const n2 = (v: N) => v == null ? "–" : (+v).toFixed(2);
export const sgn = (v: N, d = 2) => v == null ? "–" : (v > 0 ? "+" : "") + (+v).toFixed(d);
export const pct = (v: N) => v == null ? "–" : Math.round(v) + "%";
export const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
export const ago = (t: string | null | undefined) => {
  if (!t) return "never";
  const m = Math.round((Date.now() - new Date(t).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 48 * 60) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} days ago`;
};
export const mean = (a: N[]) => {
  const v = a.filter((x): x is number => x != null);
  return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null;
};
export const sd = (a: N[]) => {
  const v = a.filter((x): x is number => x != null), m = mean(v);
  return v.length > 2 && m != null ? Math.sqrt(v.reduce((s, x) => s + (x - m) ** 2, 0) / (v.length - 1)) : null;
};

export function dateList(fr: string, to: string) {
  const out: string[] = [];
  for (let d = fr; d <= to; d = addDays(d, 1)) out.push(d);
  return out;
}

// x axis from the first date that has data (so "All" doesn't start in January)
export function xDates<R>(fr: string, to: string, rows: R[], key: keyof R = "date" as keyof R) {
  const have = rows.map(r => r[key] as unknown as string).filter(Boolean).sort();
  return dateList(have.length && have[0] > fr ? have[0] : fr, to);
}

export function byKey<R>(rows: R[], key: keyof R = "date" as keyof R): Record<string, R> {
  return Object.fromEntries(rows.map(r => [r[key] as unknown as string, r]));
}

export const pick = <R>(xs: string[], map: Record<string, R>, f: (r: R) => N): (number | null)[] =>
  xs.map(d => { const r = map[d]; if (!r) return null; return f(r) ?? null; });

export const niceTicksBy = (lo: number, hi: number, step: number) =>
  Array.from({ length: Math.round((hi - lo) / step) + 1 }, (_, k) => lo + k * step);

export interface Shade { from: string; to: string; label: string }

export function shades(annotations: Annotation[] | undefined): Shade[] {
  return (annotations || []).filter(a => a.status !== "rejected").map(a => {
    let from = a.start.slice(0, 10), to = (a.end || a.start).slice(0, 10);
    if (a.target && /^(night|day):/.test(a.target)) from = to = a.target.split(":")[1].slice(0, 10);
    else if (a.excludes_nights) [from, to] = a.excludes_nights;  // alcohol without target: the night after
    return { from, to, label: `${a.kind}${a.note ? ": " + a.note : ""}` };
  });
}

export const COLORS: Record<string, string> = { green: "var(--green)", yellow: "var(--yellow)", red: "var(--red)" };
export const STEP_GOAL = 8000;
