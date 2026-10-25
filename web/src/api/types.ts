// Response shapes of the local API (backend/app.py), written by hand from
// backend/health_core/queries.py and explore.py. They cover the fields the
// app reads. Once the backend has Pydantic response models these get
// generated from /openapi.json with openapi-typescript instead.

export type Band = "green" | "yellow" | "red";
export type ISODate = string;   // YYYY-MM-DD, local
export type ISOTime = string;   // ISO datetime

export interface Annotation {
  id: number;
  kind: string;
  start: string;
  end: string | null;
  target: string | null;
  attrs: Record<string, unknown>;
  status: "suggested" | "confirmed" | "rejected";
  source: "user" | "ai" | "strava" | "import";
  confidence: number | null;
  raw_text: string | null;
  note: string | null;
  created_at: string;
  updated_at: string;
  excludes_nights: [ISODate, ISODate] | null;
}

interface Annotated { annotation_ids: number[] }

// ---------------------------------------------------------------- status ----

export interface SyncStep { name: string; status: string; output_tail?: string | null }
export interface SyncRun {
  id: number;
  reason: string;
  status: string;   // queued running ok partial failed skipped busy interrupted
  started?: string | null;
  finished?: string | null;
  error?: string | null;
  origin?: string | null;
  steps?: SyncStep[];
}
export interface Status {
  now: string;
  sync_running: boolean;
  last_successful_sync: string | null;
  last_intraday_sync: string | null;
  recent_runs: SyncRun[];
  newest_data: { health_db: Record<string, string | null>; mcp_cache: Record<string, string | null> };
}

// -------------------------------------------------------------- recovery ----

export interface RecoveryRow {
  date: ISODate;
  recovery: number;
  band: Band;
  composite: number | null;
  hrv_ms: number | null;
  google_rmssd: number | null;
  deep_hrv_ms: number | null;
  rhr: number | null;
  breathing: number | null;
  skin_rel: number | null;
  spo2: number | null;
  z_hrv: number | null;
  z_rhr: number | null;
  z_sleep: number | null;
  z_resp: number | null;
  penalty: number | null;
  flags: string | null;
  baseline_n: number | null;
  provisional: number | boolean | null;
  excluded_event: number | boolean | null;
  sleep_performance: number | null;
}
export interface RecoveryDay extends RecoveryRow, Annotated {
  daily_check_flag: string | null;
  hrv_status: string | null;
}
export interface RecoveryResponse { from: ISODate; to: ISODate; days: RecoveryDay[]; annotations: Annotation[] }

export interface TrendPoint extends Annotated { date: ISODate; value: number }
export interface TrendResponse {
  metric: string; from: ISODate; to: ISODate; points: TrendPoint[];
  summary: { n: number; mean: number; min: number; max: number; first: number; last: number } | null;
  annotations: Annotation[];
}

// ----------------------------------------------------------------- sleep ----

export interface GoogleSleep {
  date?: ISODate;
  total_minutes: number | null;
  efficiency: number | null;
  start_time: ISOTime | null;
  end_time: ISOTime | null;
  deep_minutes: number | null;
  light_minutes: number | null;
  rem_minutes: number | null;
  wake_minutes: number | null;
}
export interface SleepPerformance {
  sleep_performance: number | null;
  sufficiency: number | null;
  consistency: number | null;
  efficiency: number | null;
  calm: number | null;
  asleep_min: number | null;
  need_min: number | null;
  need_baseline_min: number | null;
  need_strain_min: number | null;
  need_debt_min: number | null;
  nap_prev_day_min: number | null;
}
export interface Night extends Annotated { night: ISODate; google: GoogleSleep | null; performance: SleepPerformance | null }
export interface SleepResponse { from: ISODate; to: ISODate; nights: Night[]; annotations: Annotation[] }

export interface Hypnogram {
  start: ISOTime; end: ISOTime;
  stages: { start: ISOTime; end: ISOTime; stage: string }[];
  brief: { start: ISOTime; end: ISOTime }[];
}

// --------------------------------------------------------------- explain ----

export interface ExplainError { date: ISODate; score: string; error: string; hypnogram?: Hypnogram | null }
export interface ExplainRecovery {
  date: ISODate; score: "recovery"; value: number; band: Band; error?: undefined;
  components: Record<string, { z: number | null; weight: number | null; contribution: number | null }>;
  penalty: number | null; flags: string | null;
  raw: { hrv_ms: number | null; hrv_source: string | null; google_rmssd: number | null; deep_hrv_ms: number | null;
    rhr: number | null; breathing: number | null; skin_rel: number | null; spo2: number | null };
  baseline_n: number | null; provisional: boolean; excluded_event_night: boolean;
}
export interface ExplainSleep {
  date: ISODate; score: "sleep"; value: number | null; error?: undefined;
  components: Record<string, { value: number | null; weight: number; contribution: number | null }>;
  need: { total_min: number | null; baseline_min: number | null; from_strain_min: number | null;
    from_sleep_debt_min: number | null; minus_naps_min: number | null };
  asleep_min: number | null;
  hypnogram: Hypnogram | null;
}

// ------------------------------------------------------------- dashboard ----

export interface StrainDay { date: ISODate; strain: number; band: string | null; load: number | null; minutes_counted?: number | null; partial: number | boolean | null }
export interface DashboardResponse {
  date: ISODate;
  recovery: RecoveryRow | null;
  sleep: { night: ISODate; google: GoogleSleep | null; performance: SleepPerformance | null };
  strain: { today: StrainDay | null; target: { low: number; high: number; label: string; basis: string } | null };
  steps: number | null;
  google_daily_activity: { steps: number | null; distance_km: number | null; active_minutes: number | null; floors?: number | null } | null;
  running_week: { monday: ISODate; runs: number; km: number; minutes: number; trimp: number; easy_pct?: number | null };
  daily_check: { flag: string; warning: string | null; hrv_status: string | null; z: Record<string, number>; excluded_event: number | boolean | null } | null;
  annotations: Annotation[];
}

// ------------------------------------------------------------------ runs ----

export interface Workout extends Annotated {
  id: string;
  log_id: string;
  date: ISODate;
  type: string;
  name: string;
  minutes: number;
  km: number | null;
  avg_hr: number | null;
  trimp: number | null;
  min_light: number | null;
  min_moderate: number | null;
  min_vigorous: number | null;
  min_peak: number | null;
  pct_below_first: number | null;
  pct_below_vigorous: number | null;
  ef: number | null;
  decoupling_pct: number | null;
  hrr60: number | null;
  hrr120: number | null;
  steady: number | boolean | null;
  notes: string | null;
  strain: number | null;
}
export interface RunsResponse { from: ISODate; to: ISODate; runs: Workout[]; annotations: Annotation[] }

export interface WorkoutAnalysis extends Omit<Workout, "id" | "strain" | "annotation_ids"> {
  max_hr: number | null;
  split_cv: number | null;
  climb_m_per_km: number | null;
}
export interface MetricsSummary {
  mobilityMetrics?: {
    avgCadenceStepsPerMinute?: number; avgStrideLengthMillimeters?: number;
    avgVerticalOscillationMillimeters?: number; avgVerticalRatio?: number; avgGroundContactTimeDuration?: string;
  };
  elevationGainMillimeters?: number;
}
export interface RunDetail {
  id: string;
  analysis: WorkoutAnalysis;
  strain: number | null;
  google: {
    name: string; calories: number | null; steps: number | null; start_time: ISOTime | null;
    metrics_summary: MetricsSummary; km_splits: { km: number; pace_s_per_km: number | null; avg_hr: number | null }[];
  } | null;
  annotations: Annotation[];
}

export interface Sample { s: number; v: number }
export interface WorkoutSeries {
  id: string;
  start: ISOTime;
  duration: number;
  hr: { s: number; bpm: number }[];
  pace: { s: number; v: number | null }[];
  pace_source: "watch" | "gps" | null;
  cadence: Sample[];
  cadence_source: string | null;
  walks: { from: number; to: number }[];
  zones: Record<string, [number, number]>;
  route: [number, number][] | null;
}

// -------------------------------------------------------------- activity ----

export interface ActivityDay extends Annotated {
  date: ISODate;
  steps: number | null;
  floors: number | null;
  distance_km: number | null;
  azm: number | null;
  azm_fat_burn: number | null;
  azm_cardio: number | null;
  azm_peak: number | null;
  active_light?: number | null;
  active_moderate?: number | null;
  active_vigorous?: number | null;
  active_min: number | null;
  sedentary_min?: number | null;
}
export interface ActivityResponse { from: ISODate; to: ISODate; days: ActivityDay[]; annotations: Annotation[] }

export interface DayDetail {
  date: ISODate;
  hr_5min: { t: string; bpm: number; min: number; max: number }[];
  steps_by_hour: { hour: number; steps: number }[];
  sleep: { start_time: ISOTime; end_time: ISOTime; total_minutes: number | null } | null;
  workouts: { id: string; name: string; type: string }[];
}

// -------------------------------------------------------------- training ----

export interface TrainingWeek {
  monday: ISODate; partial: number | boolean | null; workouts: number; trimp: number;
  fitness: number | null; fatigue: number | null; form: number | null;
  pct_below_vigorous: number | null; pct_below_first: number | null; sri: number | null;
}
export interface TrainingResponse {
  from: ISODate; to: ISODate;
  strain: (StrainDay & Annotated)[];
  weeks: TrainingWeek[];
  acwr: { date: ISODate; acwr: number; acwr_label: string | null }[];
  records: { record: string; km: number | null; seconds: number | null; date: ISODate }[];
  annotations: Annotation[];
}

// ------------------------------------------------------------- long-term ----

export interface HealthspanRow {
  metric: string; value: number; unit: string | null; referent: number | string | null;
  years_adjusted: number | null; note: string | null;
}
export interface LongtermResponse {
  healthspan: { computed_on: ISODate | null; variants: Record<string, HealthspanRow[]> };
  monthly: Record<string, { month: string; mean: number; n: number }[]>;
  vo2max: { date: ISODate; vo2_max: number | null; cardio_fitness_level: string | null }[];
  run_vo2max: { date: ISODate; vo2max: number }[];
  weight: { date: ISODate; weight_kg: number }[];
  height_cm: number | null;
  ecg: { reading_id: string; date: ISODate; start_time: ISOTime; avg_bpm: number; classification: string | null; duration_sec: number }[];
  assessments: { date: ISODate; kind: string; classification: string | null }[];
  annotations: Annotation[];
}
export interface Ecg {
  date: ISODate; start_time: ISOTime; avg_bpm: number; classification: string | null;
  sampling_hz: number | null; waveform: number[];
}

// ------------------------------------------------------------------ data ----

export interface Catalog {
  tables: { source: "health" | "google"; table: string; group: string; rows: number; columns: string[];
    date_column: string | null; first: string | null; last: string | null }[];
}
export interface TablePage {
  source: string; table: string; columns: string[]; date_column: string | null; total: number;
  limit: number; offset: number; rows: Record<string, unknown>[];
}

// ---------------------------------------------------------------- writes ----

export interface AnnotationResult { annotation: Annotation; recompute: SyncRun | null }
