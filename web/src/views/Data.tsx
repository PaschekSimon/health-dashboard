// Data: every table in health.db and the MCP cache, and one table paged
// (#/data/<source>/<table>?offset=&from=&to=&asc=1) with a CSV of the page.

import { Fragment, type FormEvent } from "react";
import { get } from "../api/client";
import type { Catalog, TablePage } from "../api/types";
import { Table } from "../components/bits";
import { fShort, n0 } from "../lib/format";

const ORDER = ["Scores (computed)", "Analysis (computed)", "Google Health daily (MCP cache)", "Watch daily (health_sync)",
  "Watch samples (intraday)", "Google Takeout", "Events", "Sync bookkeeping"];

export async function loadData(args: string[], q: URLSearchParams) {
  if (args.length >= 2) return loadTable(args[0], args[1], q);
  return <DataCatalog cat={await get<Catalog>("/data")} />;
}

function DataCatalog({ cat }: { cat: Catalog }) {
  const groups: Record<string, Catalog["tables"]> = {};
  cat.tables.forEach(t => (groups[t.group] = groups[t.group] || []).push(t));
  const names = Object.keys(groups).sort((a, b) => (ORDER.indexOf(a) + 99) % 99 - (ORDER.indexOf(b) + 99) % 99);
  const total = cat.tables.reduce((s, t) => s + t.rows, 0);
  return (
    <>
      <div className="head"><h1>Data</h1><span className="sub">{cat.tables.length} tables · {n0(total)} rows · read-only</span></div>
      <div className="catalog">{names.map(g => <Fragment key={g}>
        <h3>{g}</h3>
        <Table><tr><th>Table</th><th className="num">Rows</th><th>From</th><th>To</th><th>Columns</th></tr>
          {groups[g].map(t => (
            <tr key={t.source + t.table} className="click" onClick={() => { location.hash = `#/data/${t.source}/${t.table}`; }}>
              <td><b>{t.table}</b> <span className="note">{t.source === "google" ? "MCP cache" : "health.db"}</span></td><td className="num">{n0(t.rows)}</td>
              <td>{t.first ? fShort(t.first) : ""}</td><td>{t.last ? fShort(t.last) : ""}</td><td className="note" style={{ whiteSpace: "normal" }}>{t.columns.join(", ")}</td></tr>
          ))}
        </Table>
      </Fragment>)}</div>
    </>
  );
}

const LIMIT = 200;

async function loadTable(source: string, table: string, q: URLSearchParams) {
  const offset = +(q.get("offset") || 0), fr = q.get("from") || "", to = q.get("to") || "", asc = q.get("asc") === "1";
  const p = new URLSearchParams({ limit: String(LIMIT), offset: String(offset), desc: String(!asc) });
  if (fr) p.set("from", fr);
  if (to) p.set("to", to);
  const t = await get<TablePage>(`/data/${source}/${table}?${p}`);
  return <DataTable source={source} table={table} t={t} offset={offset} fr={fr} to={to} asc={asc} />;
}

function DataTable({ source, table, t, offset, fr, to, asc }:
  { source: string; table: string; t: TablePage; offset: number; fr: string; to: string; asc: boolean }) {
  const link = (o: { offset?: number; from?: string; to?: string; asc?: boolean }) => {
    const n = new URLSearchParams({ offset: String(o.offset ?? offset) });
    if (o.from ?? fr) n.set("from", o.from ?? fr);
    if (o.to ?? to) n.set("to", o.to ?? to);
    if (o.asc ?? asc) n.set("asc", "1");
    return `#/data/${source}/${table}?${n}`;
  };
  const cell = (v: unknown) => v == null ? <span className="note">null</span> : typeof v === "object" ? JSON.stringify(v) : String(v);
  const filter = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const d = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>;
    location.hash = link({ from: d.from || "", to: d.to || "", offset: 0 });
  };
  const csv = () => {
    const q = (v: unknown) => v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v);
    const text = [t.columns.join(","), ...t.rows.map(r => t.columns.map(c => q(r[c])).join(","))].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    a.download = `${table}_${offset}.csv`;
    a.click();
  };
  return (
    <>
      <div className="head"><a href="#/data" className="note">‹ Data</a></div>
      <div className="head"><h1>{table}</h1><span className="sub">{source === "google" ? "Google daily cache" : "health.db"} · {n0(t.total)} rows</span></div>
      <form className="form" id="flt" style={{ marginBottom: 12 }} onSubmit={filter}>
        {t.date_column ? <>
          <label>From<input type="date" name="from" defaultValue={fr} /></label><label>To<input type="date" name="to" defaultValue={to} /></label><button className="btn">Filter</button>
          <a className="btn" href={link({ asc: !asc, offset: 0 })} style={{ textDecoration: "none" }}>{asc ? "Oldest first" : "Newest first"}</a>
        </> : null}
        <span className="spacer" />
        <span className="note">{n0(Math.min(t.total, offset + 1))}–{n0(Math.min(t.total, offset + t.rows.length))} of {n0(t.total)}</span>
        {offset > 0 ? <a className="btn" style={{ textDecoration: "none" }} href={link({ offset: Math.max(0, offset - LIMIT) })}>‹ Prev</a> : null}
        {offset + LIMIT < t.total ? <a className="btn" style={{ textDecoration: "none" }} href={link({ offset: offset + LIMIT })}>Next ›</a> : null}
        <button className="btn" type="button" id="csv" onClick={csv}>CSV (this page)</button>
      </form>
      <Table className="data" style={{ maxHeight: "70vh" }}>
        <tr>{t.columns.map(c => <th key={c}>{c}</th>)}</tr>
        {t.rows.length ? t.rows.map((r, i) => <tr key={i}>{t.columns.map(c => <td key={c}>{cell(r[c])}</td>)}</tr>) : <tr><td className="note">No rows.</td></tr>}
      </Table>
      <p className="note">Times with _local are local clock time. Long text (routes, ECG waveforms, JSON) is cut here; the full values stay in the database.</p>
    </>
  );
}
