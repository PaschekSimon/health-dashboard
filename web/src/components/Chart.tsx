// React wrappers around the old app's SVG chart helper (legacy/charts.js).
// The helper draws into a div and redraws itself on resize; these hand it a
// div React leaves alone and draw again whenever the config object changes,
// so build configs with useMemo where a view re-renders for other reasons.

import { useLayoutEffect, useRef, type ReactNode } from "react";
import { chart, hypnogram, route, type ChartConfig, type HypnogramConfig } from "legacy/charts.js";

type Drawn = HTMLDivElement & { _ro?: ResizeObserver };

function reset(el: Drawn) {
  el._ro?.disconnect();
  delete el._ro;   // the helper only adds a resize observer when there is none
  el.replaceChildren();
}

export function Chart<X>({ id, cfg }: { id?: string; cfg: ChartConfig<X> }) {
  const ref = useRef<Drawn>(null);
  useLayoutEffect(() => {
    const el = ref.current!;
    chart(el, cfg);
    return () => reset(el);
  }, [cfg]);
  return <div id={id} ref={ref} />;
}

export function Hypnogram({ id, cfg }: { id?: string; cfg: HypnogramConfig }) {
  const ref = useRef<Drawn>(null);
  useLayoutEffect(() => {
    const el = ref.current!;
    hypnogram(el, cfg);
    return () => reset(el);
  }, [cfg]);
  return <div id={id} ref={ref} />;
}

// Leaflet keeps state on its container, so each route gets a fresh one.
export function Route({ pts }: { pts: [number, number][] }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const box = document.createElement("div");
    ref.current!.appendChild(box);
    route(box, pts);
    return () => box.remove();
  }, [pts]);
  return <div id="c-route" ref={ref} />;
}

export function Legend({ items }: { items: [string, string][] }) {
  return (
    <div className="legend">
      {items.map(([name, color]) => <span key={name}><i style={{ background: color }} />{name}</span>)}
    </div>
  );
}

// a card with a title, the current value on the right, an optional subline and the chart
export function ChartBox<X>({ id, title, now, sub, cfg }:
  { id?: string; title: ReactNode; now?: ReactNode; sub?: ReactNode; cfg: ChartConfig<X> }) {
  return (
    <div className="card">
      <div className="chart-title"><h3>{title}</h3><span className="now">{now}</span></div>
      {sub ? <div className="note csub">{sub}</div> : null}
      <Chart id={id} cfg={cfg} />
    </div>
  );
}
