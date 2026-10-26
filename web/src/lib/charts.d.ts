// Types for charts.js, the dashboard's dependency-free SVG chart helper
// (plain JavaScript, kept from the first version of the app).

export type Num = number | null | undefined;

export interface Series {
  name?: string;
  type: "bar" | "line" | "dot" | "range";
  data?: Num[];
  color?: string;
  colors?: (string | null | undefined)[];
  stack?: string;
  lo?: Num[];
  hi?: Num[];
  fmt?: (v: number | null) => string;
  tip?: (i: number) => string;
  dash?: boolean;
  width?: number;
  r?: number;
  noDots?: boolean;
  noTip?: boolean;
  connect?: boolean;
}

export interface ChartConfig<X = string | number> {
  x: X[];
  series: Series[];
  height?: number;
  y?: { min?: number; max?: number; ticks?: number[]; fmt?: (v: number) => string | number; invert?: boolean };
  band?: { lo: number; hi: number } | null;
  refs?: { y: number; label?: string }[];
  shades?: { from: X; to: X; label?: string }[];
  xfmt?: (v: X) => string;
  tipx?: (v: X) => string;
  tipExtra?: (i: number) => string;
  onClick?: (i: number) => void;
  selected?: number | null;
  numeric?: boolean;
  xmin?: number;
  xmax?: number;
  xticks?: number[];
  gap?: number;
  sync?: string;
}

export interface HypnogramConfig {
  start: string;
  end: string;
  stages: { start: string; end: string; stage: string }[];
  brief: { start: string; end: string }[];
  lanes: [string, string, string][];
  tfmt: (t: Date) => string;
  dfmt: (minutes: number) => string;
}

export function chart<X = string | number>(root: HTMLElement, cfg: ChartConfig<X>): void;
export function hypnogram(root: HTMLElement, cfg: HypnogramConfig): void;
export function legend(items: [string, string][]): string;
export function route(root: HTMLElement, pts: [number, number][] | null): void;
