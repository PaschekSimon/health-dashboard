// Names for the API's response shapes. They come from the backend's
// Pydantic models: schema.d.ts is generated from /openapi.json with
// openapi-typescript (`npm run gen:types`, see web/README.md), so a backend
// change that breaks the app shows up as a type error here.

import type { components } from "./schema";

type S = components["schemas"];

export type Annotation = S["Annotation"];
export type Meta = S["Meta"];

export type Status = S["Status"];
export type SyncRun = S["SyncRun"];

export type RecoveryResponse = S["RecoveryRange"];
export type RecoveryDay = S["RecoveryDay"];
export type TrendResponse = S["Trend"];
export type TrendPoint = S["TrendPoint"];

export type SleepResponse = S["SleepRange"];
export type Night = S["SleepNight"];

export type ExplainRecovery = S["RecoveryExplanation"];
export type ExplainSleep = S["SleepExplanation"];

export type DashboardResponse = S["Dashboard"];

export type RunsResponse = S["RunList"];
export type Workout = S["RunRow"];
export type RunDetail = S["RunDetail"];
export type WorkoutSeries = S["WorkoutSeries"];

export type ActivityResponse = S["ActivityRange"];
export type DayDetail = S["DayDetail"];

export type TrainingResponse = S["Training"];

export type LongtermResponse = S["LongTerm"];
export type Ecg = S["Ecg"];

export type Catalog = S["Catalog"];
export type TablePage = S["TableRows"];

export type AnnotationResult = S["AnnotationChange"];
