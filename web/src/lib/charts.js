// Small SVG chart helper: bars, stacked bars, lines, dots and ranges on one
// category (dates) or numeric x axis, with a hover tooltip. No dependencies.
//
// chart(el, {
//   x: [...],                 labels (dates as YYYY-MM-DD) or numbers if numeric: true
//   series: [{ name, data, type: 'bar'|'line'|'dot'|'range', color, colors, stack, lo, hi, fmt, dash, width }],
//   height, y: { min, max, fmt, invert }, band: { lo, hi }, refs: [{ y, label }],
//   shades: [{ from, to }], xfmt, tipx, tipExtra(i), onClick(i), selected,
//   xmin, xmax, xticks,       fixed numeric x domain and tick values (charts that share a time axis)
//   sync: 'name'              charts with the same name show their hover together
// })

const NS = "http://www.w3.org/2000/svg";

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== undefined && v !== null) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

function niceTicks(lo, hi, n = 4) {
  if (lo === hi) { lo -= 1; hi += 1; }
  const raw = (hi - lo) / n;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || raw;
  const t0 = Math.floor(lo / step) * step, t1 = Math.ceil(hi / step) * step;
  const out = [];
  for (let t = t0; t <= t1 + step / 2; t += step) out.push(+t.toFixed(10));
  return out;
}

const fmtDefault = v => (v === null || v === undefined || Number.isNaN(v)) ? "–" :
  Math.abs(v) >= 100 ? Math.round(v).toLocaleString("en-GB") : (+v.toFixed(1)).toString();

const syncGroups = {};

export function chart(root, cfg) {
  root.classList.add("chart");
  if (cfg.sync) (syncGroups[cfg.sync] = syncGroups[cfg.sync] || new Set()).add(root);
  const draw = () => render(root, cfg);
  draw();
  if (!root._ro) {
    let w = root.clientWidth;
    root._ro = new ResizeObserver(() => { if (Math.abs(root.clientWidth - w) > 4) { w = root.clientWidth; draw(); } });
    root._ro.observe(root);
  }
}

function render(root, cfg) {
  root.innerHTML = "";
  const W = Math.max(root.clientWidth, 240), H = cfg.height || 170;
  const m = { l: 42, r: 12, t: 10, b: 22 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const xs = cfg.x || [], n = xs.length;
  const clean = a => a && a.map(v => (typeof v === "number" && Number.isFinite(v)) ? v : null);
  const series = (cfg.series || []).map(s => ({ ...s, data: clean(s.data || []), lo: clean(s.lo), hi: clean(s.hi) }));
  if (!n) { root.innerHTML = '<div class="empty">No data in this range.</div>'; return; }

  // ---- x scale
  let X, step = iw / n;
  if (cfg.numeric) {
    const x0 = cfg.xmin ?? Math.min(...xs), x1 = cfg.xmax ?? Math.max(...xs);
    X = v => m.l + (x1 === x0 ? iw / 2 : (v - x0) / (x1 - x0) * iw);
  } else {
    X = i => m.l + step * (i + 0.5);
  }
  const xAt = i => cfg.numeric ? X(xs[i]) : X(i);

  // ---- y domain
  const vals = [];
  const stacks = {};
  for (const s of series) {
    if (s.type === "range") { (s.lo || []).forEach(v => v != null && vals.push(v)); (s.hi || []).forEach(v => v != null && vals.push(v)); continue; }
    if (s.stack) {
      const acc = stacks[s.stack] || (stacks[s.stack] = new Array(n).fill(0));
      s._base = acc.slice();
      s.data.forEach((v, i) => { acc[i] += v || 0; });
      acc.forEach(v => vals.push(v));
    } else s.data.forEach(v => v != null && vals.push(v));
    if (s.type === "bar") vals.push(0);
  }
  if (cfg.band) vals.push(cfg.band.lo, cfg.band.hi);
  (cfg.refs || []).forEach(r => vals.push(r.y));
  const y = cfg.y || {};
  let lo = y.min ?? Math.min(...vals), hi = y.max ?? Math.max(...vals);
  if (!vals.length) { lo = 0; hi = 1; }
  if (y.min === undefined && y.max === undefined && lo !== hi) { const pad = (hi - lo) * 0.08; if (lo !== 0) lo -= pad; hi += pad; }
  const ticks = y.ticks || niceTicks(lo, hi);
  if (y.min === undefined) lo = Math.min(lo, ticks[0]);
  if (y.max === undefined) hi = Math.max(hi, ticks[ticks.length - 1]);
  const Y = v => {
    v = Math.min(hi, Math.max(lo, v));   // fixed axes clip outliers to the plot
    return y.invert ? m.t + (v - lo) / (hi - lo || 1) * ih : m.t + ih - (v - lo) / (hi - lo || 1) * ih;
  };
  const yfmt = y.fmt || fmtDefault;

  const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}` }, root);
  const g = el("g", { class: "grid" }, svg);
  const ax = el("g", { class: "axis" }, svg);
  for (const t of ticks) {
    if (t < lo - 1e-9 || t > hi + 1e-9) continue;
    el("line", { x1: m.l, x2: W - m.r, y1: Y(t), y2: Y(t), class: t === 0 && !y.invert ? "base" : null }, g);
    el("text", { x: m.l - 6, y: Y(t) + 3.5, "text-anchor": "end" }, ax).textContent = yfmt(t);
  }

  // ---- shaded event ranges and the normal band
  for (const sh of cfg.shades || []) {
    let a, b;
    if (cfg.numeric) { a = X(sh.from); b = X(sh.to); }
    else {
      const ia = xs.findIndex(v => v >= sh.from), ib = xs.findLastIndex(v => v <= sh.to);
      if (ia < 0 || ib < 0 || ib < ia) continue;
      a = X(ia) - step / 2; b = X(ib) + step / 2;
    }
    const r = el("rect", { x: a, y: m.t, width: Math.max(1, b - a), height: ih, class: "shade" }, svg);
    if (sh.label) el("title", {}, r).textContent = sh.label;
  }
  if (cfg.band && cfg.band.lo != null) {
    el("rect", { x: m.l, width: iw, y: Math.min(Y(cfg.band.hi), Y(cfg.band.lo)), height: Math.abs(Y(cfg.band.lo) - Y(cfg.band.hi)), class: "bandrect" }, svg);
  }

  // ---- x ticks
  const xfmt = cfg.xfmt || (v => v);
  const want = Math.max(2, Math.floor(iw / 70));
  // keep every k-th label so the widest one (11px text) fits with a gap; fixed
  // tick lists get thinned this way too, so a 2-hour axis becomes 6-hourly on a phone
  const thin = (pos, labels) => {
    const gap = pos.length > 1 ? Math.abs(pos[1] - pos[0]) : iw;
    const need = Math.max(...labels.map(s => String(s).length)) * 6.6 + 12;
    const every = Math.max(1, Math.ceil(need / gap));
    pos.forEach((p, i) => {
      const half = String(labels[i]).length * 3.3;   // keep the end labels inside the svg
      if (i % every === 0) el("text", { x: Math.min(Math.max(p, half), W - half), y: H - 6, "text-anchor": "middle" }, ax).textContent = labels[i];
    });
  };
  if (cfg.numeric) {
    const x0 = cfg.xmin ?? Math.min(...xs), x1 = cfg.xmax ?? Math.max(...xs);
    const ts = (cfg.xticks || niceTicks(x0, x1, want)).filter(t => t >= x0 && t <= x1);
    thin(ts.map(X), ts.map(xfmt));
  } else {
    const every = Math.max(1, Math.ceil(n / want)), is = [];
    for (let i = 0; i < n; i += every) is.push(i);
    thin(is.map(X), is.map(i => xfmt(xs[i])));
  }

  // ---- series
  // History is drawn in ink; the newest value (or the selected day) of a
  // plain ink series takes the one data hue.
  const bw = Math.max(1, Math.min(step * 0.72, 26));
  const lastIdx = d => { for (let i = d.length - 1; i >= 0; i--) if (d[i] != null) return i; return -1; };
  for (const s of series) {
    const color = s.color || "var(--ink)";
    const hl = !s.color && !s.colors && !s.stack ? (cfg.selected ?? lastIdx(s.data)) : -1;
    if (s.type === "bar") {
      s.data.forEach((v, i) => {
        if (v == null) return;
        const base = s._base ? s._base[i] : 0;
        const y0 = Y(base), y1 = Y(base + v);
        el("rect", { x: X(i) - bw / 2, y: Math.min(y0, y1), width: bw, height: Math.max(v ? 1 : 0, Math.abs(y0 - y1)),
          style: `fill:${i === hl ? "var(--data)" : (s.colors && s.colors[i]) || color}`, rx: bw > 6 ? 2 : 0,
          opacity: cfg.selected != null && cfg.selected !== i && !(s.colors) ? 0.75 : 1 }, svg);
      });
    } else if (s.type === "range") {
      xs.forEach((_, i) => {
        const a = s.lo[i], b = s.hi[i];
        if (a == null || b == null) return;
        el("rect", { x: xAt(i) - bw / 2, y: Math.min(Y(a), Y(b)), width: bw, height: Math.abs(Y(a) - Y(b)), rx: 2,
          style: `fill:${(s.colors && s.colors[i]) || color}` }, svg);
      });
    } else if (s.type === "dot") {
      s.data.forEach((v, i) => {
        if (v == null) return;
        el("circle", { cx: xAt(i), cy: Y(v), r: s.r || 2.6, style: `fill:${(s.colors && s.colors[i]) || color}` }, svg);
      });
    } else {
      let d = "", pen = false;
      s.data.forEach((v, i) => {
        if (v == null) { if (!s.connect) pen = false; return; }
        if (cfg.numeric && cfg.gap && i && xs[i] - xs[i - 1] > cfg.gap) pen = false;  // no line across missing data
        d += (pen ? "L" : "M") + xAt(i).toFixed(1) + "," + Y(v).toFixed(1);
        pen = true;
      });
      el("path", { d, fill: "none", style: `stroke:${color}`, "stroke-width": s.width || 1.75, "stroke-linejoin": "round", "stroke-linecap": "round",
        "stroke-dasharray": s.dash ? "4 3" : null }, svg);
      // no point markers on a line, except a value with no neighbour (it would
      // vanish otherwise) and the newest value, ringed in the data hue
      if (!cfg.numeric && n <= 120 && !s.noDots) s.data.forEach((v, i) => {
        if (v == null || i === hl) return;
        if (!s.connect && (s.data[i - 1] ?? null) === null && (s.data[i + 1] ?? null) === null)
          el("circle", { cx: xAt(i), cy: Y(v), r: 2, style: `fill:${color}` }, svg);
      });
      if (!cfg.numeric && hl >= 0 && s.data[hl] != null)
        el("circle", { cx: xAt(hl), cy: Y(s.data[hl]), r: 4, style: "fill:var(--data);stroke:var(--surface);stroke-width:2" }, svg);
    }
  }
  for (const r of cfg.refs || []) {
    el("line", { x1: m.l, x2: W - m.r, y1: Y(r.y), y2: Y(r.y), class: "ref" }, svg);
    if (r.label) el("text", { x: W - m.r, y: Y(r.y) - 4, "text-anchor": "end" }, svg).textContent = r.label;
  }

  // ---- hover
  const hover = el("line", { y1: m.t, y2: m.t + ih, class: "hover", visibility: "hidden" }, svg);
  const tip = document.createElement("div");
  tip.className = "tip";
  root.appendChild(tip);
  const ov = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent", style: cfg.onClick ? "cursor:pointer" : "" }, svg);
  const idxAt = px => {
    if (!cfg.numeric) return Math.max(0, Math.min(n - 1, Math.floor((px - m.l) / step)));
    let best = 0, bd = Infinity;
    for (let i = 0; i < n; i++) { const d = Math.abs(X(xs[i]) - px); if (d < bd) { bd = d; best = i; } }
    return best;
  };
  const pos = e => { const b = svg.getBoundingClientRect(); return e.clientX - b.left; };
  const show = i => {
    const cx = xAt(i);
    hover.setAttribute("x1", cx); hover.setAttribute("x2", cx); hover.setAttribute("visibility", "visible");
    const rows = series.filter(s => !s.noTip).map(s => {
      const v = s.type === "range" ? (s.tip ? s.tip(i) : `${yfmt(s.lo[i])}–${yfmt(s.hi[i])}`) : (s.fmt || yfmt)(s.data[i]);
      return `<div><span>${s.name || ""}</span><b>${v}</b></div>`;
    }).join("");
    tip.innerHTML = `<div><b>${(cfg.tipx || xfmt)(xs[i])}</b></div>${rows}${cfg.tipExtra ? cfg.tipExtra(i) : ""}`;
    tip.style.display = "block";
    tip.style.left = Math.min(Math.max(cx, 70), W - 70) + "px";
    tip.style.top = (m.t + 4) + "px";
  };
  const hide = () => { tip.style.display = "none"; hover.setAttribute("visibility", "hidden"); };
  // a synced peer hovers at the same x value (nearest sample, none if out of range)
  root._hoverAt = xv => {
    if (xv == null || xv < xs[0] - (cfg.gap || 60) || xv > xs[n - 1] + (cfg.gap || 60)) return hide();
    let best = 0;
    for (let i = 1; i < n; i++) if (Math.abs(xs[i] - xv) < Math.abs(xs[best] - xv)) best = i;
    show(best);
  };
  const peers = () => {
    const g = cfg.sync && syncGroups[cfg.sync];
    if (!g) return [];
    for (const p of g) if (!p.isConnected) g.delete(p);
    return [...g].filter(p => p !== root && p._hoverAt);
  };
  ov.addEventListener("mousemove", e => {
    const i = idxAt(pos(e));
    show(i);
    for (const p of peers()) p._hoverAt(cfg.numeric ? xs[i] : null);
  });
  ov.addEventListener("mouseleave", () => { hide(); for (const p of peers()) p._hoverAt(null); });
  if (cfg.onClick) ov.addEventListener("click", e => cfg.onClick(idxAt(pos(e))));
}

// Hypnogram: one lane per sleep stage (awake on top, deep at the bottom),
// each stage a block in its lane joined by thin steps, brief wake-ups as
// ticks in the awake lane, the lane's total at the right.
// hypnogram(el, { start, end, stages: [{ start, end, stage }], brief: [{ start, end }],
//   lanes: [[stage, label, color]], tfmt(Date), dfmt(minutes) })
export function hypnogram(root, cfg) {
  root.classList.add("chart");
  const draw = () => drawHypnogram(root, cfg);
  draw();
  if (!root._ro) {
    let w = root.clientWidth;
    root._ro = new ResizeObserver(() => { if (Math.abs(root.clientWidth - w) > 4) { w = root.clientWidth; draw(); } });
    root._ro.observe(root);
  }
}

function drawHypnogram(root, cfg) {
  root.innerHTML = "";
  const ms = t => new Date(t).getTime();
  const t0 = ms(cfg.start), t1 = ms(cfg.end), lanes = cfg.lanes;
  const W = Math.max(root.clientWidth, 240), lh = 30, m = { l: 54, r: 62, t: 4, b: 22 };
  const H = m.t + lanes.length * lh + m.b, iw = W - m.l - m.r;
  const X = t => m.l + (t - t0) / (t1 - t0 || 1) * iw;
  const row = Object.fromEntries(lanes.map(([k], i) => [k, i]));
  const mid = i => m.t + i * lh + lh / 2, bh = 16;
  const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}` }, root);
  const g = el("g", { class: "grid" }, svg), ax = el("g", { class: "axis" }, svg);

  const total = {};
  for (const s of cfg.stages) total[s.stage] = (total[s.stage] || 0) + (ms(s.end) - ms(s.start)) / 60000;
  lanes.forEach(([k, label], i) => {
    el("line", { x1: m.l, x2: W - m.r, y1: mid(i), y2: mid(i) }, g);
    el("text", { x: m.l - 10, y: mid(i) + 3.5, "text-anchor": "end" }, ax).textContent = label;
    el("text", { x: W - m.r + 10, y: mid(i) + 3.5, class: "tot" }, ax).textContent = cfg.dfmt(total[k] || 0);
  });
  // whole clock hours along the bottom, thinned to fit
  const hour = 3600000, every = Math.max(1, Math.ceil((t1 - t0) / hour / Math.max(2, Math.floor(iw / 56))));
  for (let t = Math.ceil(t0 / hour) * hour, k = 0; t <= t1; t += hour, k++)
    if (k % every === 0) el("text", { x: X(t), y: H - 6, "text-anchor": "middle" }, ax).textContent = cfg.tfmt(new Date(t));

  // steps between consecutive stages, then the blocks on top
  const steps = el("g", { class: "steps" }, svg);
  cfg.stages.forEach((s, i) => {
    const n = cfg.stages[i + 1];
    if (n && row[n.stage] !== row[s.stage]) el("line", { x1: X(ms(n.start)), x2: X(ms(n.start)), y1: mid(row[s.stage]), y2: mid(row[n.stage]) }, steps);
  });
  const blocks = cfg.stages.map(s => {
    const i = row[s.stage] ?? 0, a = X(ms(s.start)), b = X(ms(s.end));
    el("rect", { x: a, y: mid(i) - bh / 2, width: Math.max(1.5, b - a), height: bh, rx: Math.min(3, (b - a) / 2), style: `fill:${lanes[i][2]}` }, svg);
    return { a, b, i, s, label: lanes[i][1] };
  });
  const aw = row.awake ?? 0;
  for (const s of cfg.brief || []) {
    const a = X(ms(s.start)), b = X(ms(s.end));
    el("rect", { x: a, y: mid(aw) - 4, width: Math.max(1.5, b - a), height: 8, rx: 1, style: `fill:${lanes[aw][2]}`, opacity: 0.55 }, svg);
    blocks.push({ a, b: Math.max(b, a + 1.5), i: aw, s, label: "Brief wake-up" });
  }

  // hover: the block under the pointer (nearest in time on its lane, else any lane)
  const hover = el("line", { y1: m.t, y2: H - m.b, class: "hover", visibility: "hidden" }, svg);
  const tip = document.createElement("div");
  tip.className = "tip";
  root.appendChild(tip);
  const ov = el("rect", { x: m.l, y: m.t, width: iw, height: H - m.t - m.b, fill: "transparent" }, svg);
  const hide = () => { tip.style.display = "none"; hover.setAttribute("visibility", "hidden"); };
  ov.addEventListener("mousemove", e => {
    const b = svg.getBoundingClientRect(), px = e.clientX - b.left, lane = Math.floor((e.clientY - b.top - m.t) / lh);
    const at = blocks.filter(k => px >= k.a - 1 && px <= k.b + 1);
    const hit = at.find(k => k.i === lane) || at.find(k => k.label !== "Brief wake-up");
    if (!hit) return hide();
    const mins = (ms(hit.s.end) - ms(hit.s.start)) / 60000;
    hover.setAttribute("x1", px); hover.setAttribute("x2", px); hover.setAttribute("visibility", "visible");
    tip.innerHTML = `<div><b>${hit.label}</b></div><div><span>${cfg.tfmt(new Date(hit.s.start))}–${cfg.tfmt(new Date(hit.s.end))}</span><b>${cfg.dfmt(mins)}</b></div>`;
    tip.style.display = "block";
    tip.style.left = Math.min(Math.max(px, 70), W - 70) + "px";
    tip.style.top = (m.t + 4) + "px";
  });
  ov.addEventListener("mouseleave", hide);
}

export function legend(items) {
  return `<div class="legend">${items.map(([name, color]) => `<span><i style="background:${color}"></i>${name}</span>`).join("")}</div>`;
}

// GPS route on an OpenStreetMap street map (Leaflet, dimmed tiles); falls
// back to a plain outline when Leaflet or the tiles aren't reachable (offline).
export function route(root, pts) {
  root.innerHTML = "";
  if (!pts || pts.length < 2) { root.innerHTML = '<div class="empty">No GPS route.</div>'; return; }
  if (window.L) {
    root.classList.add("map");
    const map = L.map(root, { scrollWheelZoom: false, attributionControl: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    L.polyline(pts, { color: "#000", weight: 7, opacity: 0.55 }).addTo(map);   // casing
    const line = L.polyline(pts, { color: "#ffffff", weight: 3.5 }).addTo(map);
    L.circleMarker(pts[0], { radius: 6, color: "#000", weight: 2, fillColor: "#fff", fillOpacity: 1 }).bindTooltip("Start").addTo(map);
    L.circleMarker(pts[pts.length - 1], { radius: 5, color: "#fff", weight: 2, fillColor: "#000", fillOpacity: 1 }).bindTooltip("Finish").addTo(map);
    map.fitBounds(line.getBounds(), { padding: [18, 18] });
    new ResizeObserver(() => map.invalidateSize()).observe(root);
    return;
  }
  const lat0 = pts.reduce((a, p) => a + p[0], 0) / pts.length, k = Math.cos(lat0 * Math.PI / 180);
  const xy = pts.map(([la, lo]) => [lo * k, -la]);
  const xs = xy.map(p => p[0]), ys = xy.map(p => p[1]);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const S = 260, pad = 10, sc = (S - 2 * pad) / Math.max(x1 - x0, y1 - y0);
  const d = xy.map(([x, y], i) => (i ? "L" : "M") + (pad + (x - x0) * sc).toFixed(1) + "," + (pad + (y - y0) * sc).toFixed(1)).join("");
  const svg = el("svg", { width: S, height: S, viewBox: `0 0 ${S} ${S}`, class: "route" }, root);
  el("path", { d }, svg);
  const [sx, sy] = xy[0];
  el("circle", { cx: pad + (sx - x0) * sc, cy: pad + (sy - y0) * sc, r: 4, style: "fill:var(--fg)" }, svg);
}
