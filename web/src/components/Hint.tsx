// A hint label explains its metric on hover (or keyboard focus): what it is,
// then how to read its values. One shared bubble, found by event delegation,
// so a hint works anywhere: card labels, table headers, chart titles.

import { useEffect, useRef, type ReactNode } from "react";
import { INFO } from "../lib/info";

export function Hint({ k, children }: { k: string; children: ReactNode }) {
  return <span className="hint" data-hint={k} tabIndex={0}>{children}</span>;
}

let hideCurrent = () => {};
export const hideHint = () => hideCurrent();

export function HintBox() {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const hintBox = box.current!;
    let on: HTMLElement | null = null;
    const show = (t: HTMLElement) => {
      const [what, vals] = INFO[t.dataset.hint!] || [];
      if (!what) return;
      on = t;
      hintBox.replaceChildren();
      const p = document.createElement("p");
      p.textContent = what;
      hintBox.append(p);
      if (vals) {
        const v = document.createElement("p");
        v.className = "vals";
        v.textContent = vals;
        hintBox.append(v);
      }
      const r = t.getBoundingClientRect(), b = hintBox.getBoundingClientRect();
      const x = Math.max(12, Math.min(innerWidth - b.width - 12, r.left + r.width / 2 - b.width / 2));
      const y = r.bottom + 10 + b.height > innerHeight - 12 ? r.top - b.height - 10 : r.bottom + 10;
      hintBox.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
      hintBox.classList.add("on");
    };
    const hide = () => { on = null; hintBox.classList.remove("on"); };
    const target = (e: Event) => (e.target as Element).closest?.<HTMLElement>("[data-hint]") ?? null;
    const over = (e: MouseEvent) => {
      const t = target(e);
      if (t) { if (t !== on) show(t); } else if (on) hide();
    };
    const focus = (e: FocusEvent) => { const t = target(e); if (t) show(t); else hide(); };
    const scroll = () => { if (on) hide(); };
    hideCurrent = hide;
    document.addEventListener("mouseover", over);
    document.addEventListener("focusin", focus);
    document.addEventListener("scroll", scroll, { passive: true, capture: true });
    return () => {
      document.removeEventListener("mouseover", over);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("scroll", scroll, { capture: true });
      hideCurrent = () => {};
    };
  }, []);
  return <div className="hintbox" role="tooltip" ref={box} />;
}
