// View settings that stay between visits (kept in localStorage): the date
// range, the Training workout filter and the Healthspan variant.

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { addDays, today } from "./lib/format";

export interface Ui { range: number; runsOnly: boolean; hsVariant: string }

const store = {
  get<T>(k: string, d: T): T {
    try { const v = localStorage.getItem("health." + k); return v === null ? d : JSON.parse(v); } catch { return d; }
  },
  set(k: string, v: unknown) {
    try { localStorage.setItem("health." + k, JSON.stringify(v)); } catch { /* private mode */ }
  },
};

const initial = (): Ui => ({ range: store.get("range", 30), runsOnly: store.get("runsOnly", false), hsVariant: store.get("hsVariant", "all") });

type Ctx = [Ui, (patch: Partial<Ui>) => void];
const UiContext = createContext<Ctx>([initial(), () => {}]);

export function UiProvider({ children }: { children: ReactNode }) {
  const [ui, setState] = useState(initial);
  const set = useCallback((patch: Partial<Ui>) => {
    for (const [k, v] of Object.entries(patch)) store.set(k, v);
    setState(u => ({ ...u, ...patch }));
  }, []);
  return <UiContext.Provider value={[ui, set]}>{children}</UiContext.Provider>;
}

export const useUi = () => useContext(UiContext);

export function rangeDates(ui: Ui): [string, string] {
  const to = today();
  return [ui.range ? addDays(to, -(ui.range - 1)) : "2000-01-01", to];
}
