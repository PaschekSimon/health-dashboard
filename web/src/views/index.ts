import type { Loader } from "../App";
import { loadActivity } from "./Activity";
import { loadDashboard } from "./Dashboard";
import { loadData } from "./Data";
import { loadLongterm } from "./Longterm";
import { loadRecovery } from "./Recovery";
import { loadSleep } from "./Sleep";
import { loadTraining } from "./Training";

export const VIEWS: Record<string, Loader> = {
  dashboard: loadDashboard, recovery: loadRecovery, sleep: loadSleep, activity: loadActivity,
  training: loadTraining, longterm: loadLongterm, data: loadData,
};
