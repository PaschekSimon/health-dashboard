// Small building blocks shared by the views.

import { useState, type CSSProperties, type ReactNode } from "react";
import { useUi } from "../ui";

export const Kv = ({ k, children }: { k: ReactNode; children?: ReactNode }) =>
  <div className="kv"><span>{k}</span><span>{children}</span></div>;

export const BandDot = ({ band }: { band?: string | null }) => <span className={`band ${band || ""}`} />;

// progress ring: value of max, an optional target arc behind it, the figure in the middle
export function Ring({ value, max, cls, target, children }:
  { value: number | null | undefined; max: number; cls?: string; target?: { low: number; high: number } | null; children: ReactNode }) {
  const r = 52, C = 2 * Math.PI * r, f = (v: number) => Math.max(0, Math.min(1, v / max)) * C;
  return (
    <div className="ring">
      <svg viewBox="0 0 120 120" aria-hidden="true">
        <circle className="trk" cx="60" cy="60" r={r} />
        {target ? <circle className="tgt" cx="60" cy="60" r={r} strokeDasharray={`${f(target.high) - f(target.low)} ${C}`} strokeDashoffset={-f(target.low)} /> : null}
        {value != null && value > 0 ? <circle className={`val ${cls || ""}`} cx="60" cy="60" r={r} strokeDasharray={`${Math.max(0.1, f(value))} ${C}`} /> : null}
      </svg>
      <div className="num">{children}</div>
    </div>
  );
}

const RANGES: [number, string][] = [[7, "7D"], [30, "30D"], [90, "90D"], [0, "All"]];

export function RangeSeg() {
  const [ui, setUi] = useUi();
  return (
    <div className="seg" id="range">
      {RANGES.map(([v, l]) => <button key={v} className={ui.range === v ? "on" : ""} onClick={() => setUi({ range: v })}>{l}</button>)}
    </div>
  );
}

// A list table: its newest 10 rows, arrows below to page to older ones. The
// page is kept while you stay on the view (the view keeps its state).
const PAGE = 10;

export function Paged({ head, rows, style }: { head: ReactNode; rows: ReactNode[]; style?: CSSProperties }) {
  const [want, setPage] = useState(0);
  const n = Math.ceil(rows.length / PAGE);
  const p = Math.max(0, Math.min(n - 1, want));
  const paged = rows.length > PAGE;
  const shown = paged ? rows.slice(p * PAGE, (p + 1) * PAGE) : rows;
  return (
    <>
      <div className="tablewrap" style={style}>
        <table><tbody>{head}{shown}</tbody></table>
      </div>
      {paged ? (
        <div className="pager">
          <span className="note">{p * PAGE + 1}–{Math.min(rows.length, (p + 1) * PAGE)} of {rows.length}</span>
          <button className="btn small" aria-label="Newer entries" disabled={p === 0} onClick={() => setPage(p - 1)}>‹</button>
          <button className="btn small" aria-label="Older entries" disabled={p === n - 1} onClick={() => setPage(p + 1)}>›</button>
        </div>
      ) : null}
    </>
  );
}

export const Table = ({ children, className, style }: { children: ReactNode; className?: string; style?: CSSProperties }) =>
  <div className="tablewrap" style={style}><table className={className}><tbody>{children}</tbody></table></div>;

// "Shaded: illness, alcohol" next to a chart title when events are drawn on it
export function EventNote({ anns }: { anns: { status: string; kind: string }[] | undefined }) {
  const kinds = [...new Set((anns || []).filter(a => a.status !== "rejected").map(a => a.kind))];
  return kinds.length ? <span className="note">Shaded: {kinds.join(", ")}</span> : null;
}
