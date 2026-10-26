// The shell: the floating top bar (brand, tabs, sync) and the routed view.
// Routes are hash URLs, the same as the old app's: #/<tab>/<args>?<query>.

import { cloneElement, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactElement } from "react";
import { clearCache } from "./api/client";
import { HintBox, hideHint } from "./components/Hint";
import { initSync, setRefresh, startSync, useSyncView } from "./sync";
import { useUi, type Ui } from "./ui";
import { VIEWS } from "./views";

export const TABS: [string, string][] = [["dashboard", "Dashboard"], ["recovery", "Recovery"], ["sleep", "Sleep"],
  ["activity", "Activity"], ["training", "Training"], ["longterm", "Long-term"]];

export type Loader = (args: string[], q: URLSearchParams, ui: Ui) => Promise<ReactElement>;

const hash = () => location.hash;
const onHash = (l: () => void) => { addEventListener("hashchange", l); return () => removeEventListener("hashchange", l); };

function parse(h: string) {
  const [path, query] = h.replace(/^#\/?/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  return { tab: parts[0] || "dashboard", args: parts.slice(1), q: new URLSearchParams(query || "") };
}

interface Shown { tab: string; args: string; el: ReactElement; key: number; inPage: boolean; y: number; focus?: string }

export function App() {
  const h = useSyncExternalStore(onHash, hash);
  const { tab, args, q } = parse(h);
  const [ui] = useUi();
  const [shown, setShown] = useState<Shown | null>(null);
  const [tick, setTick] = useState(0);
  const main = useRef<HTMLElement>(null);
  const last = useRef<Shown | null>(null);
  const seq = useRef(0);
  const keepScroll = useRef(false);
  const [menuOpen, setMenu] = useState(false);

  // refresh after new data: cache dropped, scroll kept
  useEffect(() => setRefresh(() => { keepScroll.current = true; setTick(t => t + 1); }), []);
  useEffect(initSync, []);

  // A navigation keeps the current view on screen until the next one has its
  // data, then swaps in one step. A new tab or page arrives with a short fade
  // from the top; a change within the page (another day, range or filter)
  // swaps in place: same scroll position, no fade, focus kept. Only a slow load
  // dims the old view; only the very first load shows "Loading…".
  useEffect(() => {
    const refreshing = keepScroll.current;
    keepScroll.current = false;
    const my = ++seq.current;
    if (refreshing) clearCache();
    setMenu(false);
    const prev = last.current, argKey = args.join("/");
    const inPage = refreshing || (!!prev && prev.tab === tab && (tab === "dashboard" || tab === "activity" ||
      prev.args === argKey || (tab === "sleep" && args.length > 0 && !!prev.args)));
    const slow = setTimeout(() => main.current?.classList.add("busy"), inPage ? 400 : 250);
    const swap = (el: ReactElement) => {
      if (my !== seq.current) return;   // a newer navigation won
      clearTimeout(slow);
      hideHint();
      const next = { tab, args: argKey, el, inPage, key: (prev?.key ?? 0) + (inPage ? 0 : 1),
        y: window.scrollY, focus: document.activeElement?.id };
      last.current = next;
      setShown(next);
    };
    (VIEWS[tab] || VIEWS.dashboard)(args, q, ui).then(swap, (e: Error) => {
      swap(<div className="err-box">Couldn't load this view.<br /><span className="note">{e.message}</span></div>);
      console.error(e);
    });
    return () => clearTimeout(slow);
  }, [h, ui.range, tick]);   // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    const m = main.current;
    if (!shown || !m) return;
    m.classList.remove("busy", "enter");
    if (shown.inPage) {
      window.scrollTo(0, shown.y);
      if (shown.focus && document.activeElement?.id !== shown.focus) document.getElementById(shown.focus)?.focus({ preventScroll: true });
      return;
    }
    window.scrollTo(0, 0);
    void m.offsetWidth;   // restart the fade
    m.classList.add("enter");
  }, [shown]);

  useEffect(() => {
    document.body.classList.toggle("menu-open", menuOpen);
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [menuOpen]);

  const title = (TABS.find(([k]) => k === tab) || ["", tab === "data" ? "Data" : "Dashboard"])[1];
  return (
    <>
      <header className="island-wrap">
        <div className={"island" + (menuOpen ? " open" : "")} id="island">
          <a className="brand" href="#/dashboard" aria-label="Health, dashboard">
            <svg className="mark" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" className="mark-track" /><path d="M12 3a9 9 0 0 1 8.2 12.7" className="mark-arc" /></svg>
            <span>Health</span>
          </a>
          <span className="topbar-title" id="topbar-title">{title}</span>
          <div className="menu" id="side">
            <Tabs tab={tab} />
            <SyncBox />
          </div>
          <TopDot />
          <button className="burger" id="menu-btn" aria-label={menuOpen ? "Close menu" : "Open menu"} aria-expanded={menuOpen}
            aria-controls="side" onClick={() => setMenu(o => !o)}><span /><span /></button>
        </div>
      </header>
      <main id="view" ref={main}>
        {shown ? cloneElement(shown.el, { key: shown.key }) : <div className="empty">Loading…</div>}
      </main>
      <HintBox />
    </>
  );
}

// The nav is drawn once; a tab change only moves the classes and slides the
// indicator pill (.ind) under the new link.
function Tabs({ tab }: { tab: string }) {
  const nav = useRef<HTMLElement>(null), ind = useRef<HTMLSpanElement>(null);
  const [ready, setReady] = useState(false);
  useLayoutEffect(() => {
    const move = () => {
      const on = nav.current?.querySelector<HTMLElement>("a.on"), i = ind.current!;
      if (!on || !on.offsetWidth) { i.style.opacity = "0"; return; }
      i.style.opacity = "1";
      i.style.width = on.offsetWidth + "px";
      i.style.transform = `translateX(${on.offsetLeft}px)`;
    };
    move();
    // place it without travel first, then let later moves glide; re-place when the font or width changes
    document.fonts?.ready.then(move);
    addEventListener("resize", move);
    return () => removeEventListener("resize", move);
  }, [tab]);
  useEffect(() => { requestAnimationFrame(() => requestAnimationFrame(() => setReady(true))); }, []);
  const link = (k: string, l: string, i: number, title?: string) =>
    <a key={k} href={`#/${k}`} data-tab={k} style={{ "--i": i } as CSSProperties} title={title}
      className={k === tab ? "on" : undefined} aria-current={k === tab ? "page" : undefined}>{l}</a>;
  return (
    <nav id="tabs" aria-label="Sections" ref={nav} className={ready ? "ready" : undefined}>
      <span className="ind" aria-hidden="true" ref={ind} />
      {TABS.map(([k, l], i) => link(k, l, i))}
      <span className="sep" aria-hidden="true" />
      {link("data", "Data", TABS.length, "Every table, raw")}
    </nav>
  );
}

function SyncBox() {
  const s = useSyncView();
  return (
    <div className={"sync" + (s.cls ? " " + s.cls : "")} id="sync">
      <span className="dot" /><span className="txt" id="sync-text" title={s.title}>{s.text}</span>
      <button className="btn primary cta" id="sync-btn" title="Fetch new data from Google Health now" disabled={s.disabled}
        onClick={() => startSync("button")}>
        Sync now<span className="ico" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v4.5h-4.5" /></svg></span>
      </button>
    </div>
  );
}

// the phone top bar mirrors the sync state in its dot
function TopDot() {
  const s = useSyncView();
  return <span className={"dot" + (s.cls ? " " + s.cls : "")} id="top-dot" title={s.text} />;
}
