// The sync indicator in the top bar: last sync, a running job's progress,
// and the Sync now button. A plain store outside React (it runs polling
// loops); components read it with useSyncView().
//
// On start the app asks for a sync (reason "open", which the backend skips
// if the last one is under 15 minutes old), again when the page comes back
// into view after 15 minutes, and follows any job until it ends, then
// refreshes the view.

import { useSyncExternalStore } from "react";
import { fetchJson, get, send } from "./api/client";
import type { Meta, Status, SyncRun } from "./api/types";
import { ago } from "./lib/format";

export interface SyncView { cls: "" | "busy" | "err"; text: string; title: string; disabled: boolean }

let view: SyncView = { cls: "", text: "…", title: "", disabled: false };
const listeners = new Set<() => void>();
const paint = (patch: Partial<SyncView>) => { view = { ...view, ...patch }; listeners.forEach(l => l()); };
const subscribe = (l: () => void) => { listeners.add(l); return () => listeners.delete(l); };
export const useSyncView = () => useSyncExternalStore(subscribe, () => view);

// the app registers how to reload the current view after new data
let onRefresh = () => {};
export const setRefresh = (fn: () => void) => { onRefresh = fn; };
export const refresh = () => onRefresh();

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));
// step labels and how many steps each kind of job runs, from /meta
let meta: Meta | null = null;
const stepLabel = (step: string) => {
  for (const steps of Object.values(meta?.sync.steps || {})) {
    const s = steps.find(x => x.name === step);
    if (s) return s.label;
  }
  return step;
};
let status: Status | null = null, following = false;

function paintIdle() {
  if (!status) { paint({ cls: "", disabled: false }); return; }
  const newest = status.newest_data?.health_db?.hr_samples;
  let text = `Synced ${ago(status.last_successful_sync)}`, cls: SyncView["cls"] = "";
  let title = `Last successful sync: ${status.last_successful_sync || "never"}\nNewest heart-rate sample: ${newest || "–"}\nNewest Google sleep: ${status.newest_data?.mcp_cache?.sleep || "–"}`;
  const bad = (status.recent_runs || []).find(r => ["sync", "open", "button", "mcp"].includes(r.reason) && !["skipped", "busy"].includes(r.status));
  if (bad && ["failed", "partial", "interrupted"].includes(bad.status) &&
      (!status.last_successful_sync || (bad.finished || "") > status.last_successful_sync)) {
    cls = "err";
    text += " · last attempt " + bad.status;
    title += `\nLast attempt #${bad.id}: ${bad.status} ${bad.error || ""}`;
  }
  paint({ cls, text, title, disabled: false });
}

const paintBusy = (text: string) => paint({ cls: "busy", disabled: true, text });

async function loadStatus() {
  try { status = await fetchJson<Status>("/status"); } catch { paint({ cls: "err", text: "API not reachable" }); return null; }
  const own = (status.recent_runs || []).find(r => r.status === "running" || r.status === "queued");
  if (!following) {
    if (status.sync_running) own ? fetchJson<SyncRun>(`/sync/${own.id}`).then(followRun, paintIdle) : waitForOther();
    else paintIdle();
  }
  return status;
}

export async function startSync(reason: "open" | "button") {
  if (following) return;
  paintBusy("Starting sync…");
  try { await followRun(await send<SyncRun>("POST", "/sync", { reason })); }
  catch (e) { following = false; paint({ cls: "err", text: "Sync failed to start", title: (e as Error).message, disabled: false }); }
}

// Poll one sync/recompute job until it ends, then refresh the view. Never
// rejects: if the API stops answering, it says so and the minute-by-minute
// status check picks the job up again.
export async function followRun(run: SyncRun): Promise<void> {
  if (run.status === "skipped") { await loadStatus(); return; }
  if (run.status === "busy") return waitForOther();
  following = true;
  try {
    while (["running", "queued"].includes(run.status)) {
      const steps = run.steps || [], cur = steps.find(s => s.status === "running");
      const label = run.reason === "recompute" ? "Recomputing scores" : "Syncing", total = meta?.sync.steps[run.reason]?.length;
      paintBusy(run.status === "queued" ? `${label}: waiting for another sync` :
        `${label}${cur ? " · " + stepLabel(cur.name) : ""} (${steps.filter(s => s.status !== "running").length + 1}${total ? "/" + total : ""})`);
      await sleep(2500);
      run = await fetchJson<SyncRun>(`/sync/${run.id}`);
    }
  } catch (e) {
    paint({ cls: "err", text: "Lost track of the sync", title: (e as Error).message, disabled: false });
    return;
  } finally { following = false; }
  await loadStatus();
  if (run.status !== "ok") {
    const failed = (run.steps || []).filter(s => s.status === "failed").map(s => stepLabel(s.name));
    paint({ cls: "err", text: `Sync ${run.status}${failed.length ? ": " + failed.join(", ") : ""}`,
      title: `${run.error || ""}\n${(run.steps || []).filter(s => s.output_tail).map(s => s.name + ":\n" + s.output_tail).join("\n")}` });
  }
  refresh();
}

// Someone else (Claude's SessionStart hook, the MCP) holds the sync lock.
async function waitForOther() {
  if (following) return;
  following = true;
  paintBusy("Syncing (started elsewhere)…");
  try {
    while (true) {
      await sleep(5000);
      const s = await fetchJson<Status>("/status").catch(() => null);
      if (s && !s.sync_running) { status = s; break; }
      // recent_runs has no steps, so ask for the running job itself
      const id = s?.recent_runs?.find(r => r.status === "running")?.id;
      const run = id != null ? await fetchJson<SyncRun>(`/sync/${id}`).catch(() => null) : null;
      const cur = run?.steps.find(x => x.status === "running");
      if (cur) paintBusy(`Syncing · ${stepLabel(cur.name)}`);
    }
  } finally { following = false; }
  paintIdle();
  refresh();
}

let started = false;
export function initSync() {
  if (started) return;
  started = true;
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && status && !following &&
        (!status.last_successful_sync || Date.now() - new Date(status.last_successful_sync).getTime() > (meta?.sync.open_min_interval_min ?? 15) * 60000)) startSync("open");
  });
  get<Meta>("/meta").then(m => { meta = m; }, () => {});
  setInterval(() => { if (!following) loadStatus(); }, 60000);
  loadStatus().then(s => { if (s && !s.sync_running) startSync("open"); });
}
