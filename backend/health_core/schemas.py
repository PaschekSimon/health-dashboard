"""Response models of the API: the shape of every answer, as FastAPI checks it
and publishes it in /openapi.json (the web app's TypeScript types are made
from that file).

The query functions keep returning plain dicts, so the MCP server is
unchanged; app.py names one of these models per route.

Rules:
- Every model accepts keys it doesn't list (extra="allow") and passes them on,
  so a column a script adds later still reaches the web app before it is
  written down here. The published schema lists only the known keys.
- Dates and times are ISO strings, as the database stores them.
- A field that can be missing or NULL in the data has a default of None. In
  /openapi.json it is still listed as always present, because the answer
  always carries it (as null when there is no value).
- Num is int | float, so whole numbers stay whole in the JSON.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Num = int | float


def _closed(schema, _cls):
    # extra keys pass through at runtime, but the published schema lists only
    # the known ones, so generated types catch a misspelt field
    schema.pop("additionalProperties", None)


class Model(BaseModel):
    model_config = ConfigDict(extra="allow", json_schema_serialization_defaults_required=True,
                              json_schema_extra=_closed)


# ---------------------------------------------------------- annotations ----

class Annotation(Model):
    """Context the watch doesn't have (illness, alcohol, travel, ...)."""
    id: int
    kind: str
    start: str
    end: str | None = None
    target: str | None = Field(None, description="night:YYYY-MM-DD (wake date), day:YYYY-MM-DD or workout:<id>")
    attrs: dict[str, Any] = Field(default_factory=dict)
    status: Literal["suggested", "confirmed", "rejected"]
    source: Literal["user", "ai", "strava", "import"]
    confidence: float | None = None
    raw_text: str | None = None
    note: str | None = None
    created_at: str
    updated_at: str
    excludes_nights: list[str] | None = Field(
        None, description="[first, last] night (wake date) left out of the baselines, if it changes scores")


class WithAnnotationIds(Model):
    """Rows that carry the ids of the annotations touching their date."""
    annotation_ids: list[int] = Field(default_factory=list)


# ---------------------------------------------------------------- sync ----

class SyncStep(Model):
    name: str
    started: str
    status: str
    finished: str | None = None
    seconds: float | None = None
    exit: int | None = None
    output_tail: str | None = None


class SyncRun(Model):
    """One sync or recompute job (a sync_runs row)."""
    id: int
    reason: str = Field(description="open, button, mcp or recompute")
    started: str
    finished: str | None = None
    status: str = Field(description="running, queued, ok, partial, failed, skipped, busy or interrupted")
    error: str | None = None
    origin: str | None = None
    steps: list[SyncStep] = Field(default_factory=list)
    note: str | None = None


class RecentRun(Model):
    id: int
    reason: str
    status: str
    started: str
    finished: str | None = None
    error: str | None = None
    origin: str | None = None


class NewestData(Model):
    health_db: dict[str, str | None]
    mcp_cache: dict[str, str | None]


class Status(Model):
    now: str
    sync_running: bool
    last_successful_sync: str | None = None
    last_intraday_sync: str | None = None
    recent_runs: list[RecentRun]
    newest_data: NewestData
    health_sync_watermarks: dict[str, str]


# ------------------------------------------------------------ recovery ----

class RecoveryScore(Model):
    """A score_recovery_daily row: Recovery % and the z-scores behind it (z > 0 = better than baseline)."""
    date: str
    recovery: float | None = None
    band: Literal["green", "yellow", "red"] | None = None
    composite: float | None = None
    hrv_ms: float | None = None
    google_rmssd: float | None = None
    deep_hrv_ms: float | None = None
    rhr: Num | None = None
    breathing: float | None = None
    skin_rel: float | None = None
    spo2: float | None = None
    z_hrv: float | None = None
    z_rhr: float | None = None
    z_sleep: float | None = None
    z_resp: float | None = None
    penalty: float | None = None
    flags: str | None = None
    baseline_n: Num | None = None
    provisional: Num | None = None
    excluded_event: Num | None = None
    sleep_performance: float | None = None


class RecoveryDay(RecoveryScore, WithAnnotationIds):
    daily_check_flag: str | None = None
    hrv_status: str | None = None


class RecoveryRange(Model):
    from_: str = Field(alias="from")
    to: str
    days: list[RecoveryDay]
    annotations: list[Annotation]


# --------------------------------------------------------------- sleep ----

class SleepSummary(Model):
    """Google's sleep summary for a night (MCP cache `sleep`)."""
    date: str
    total_minutes: Num | None = None
    efficiency: Num | None = None
    start_time: str | None = None
    end_time: str | None = None
    deep_minutes: Num | None = None
    light_minutes: Num | None = None
    rem_minutes: Num | None = None
    wake_minutes: Num | None = None
    sessions: Num | None = None


class SleepPerformance(Model):
    """A score_sleep_daily row."""
    date: str
    sleep_performance: float | None = None
    sufficiency: float | None = None
    consistency: float | None = None
    efficiency: float | None = None
    calm: float | None = None
    asleep_min: float | None = None
    need_min: float | None = None
    need_baseline_min: float | None = None
    need_strain_min: float | None = None
    need_debt_min: float | None = None
    nap_prev_day_min: float | None = None
    need_strain_slope: float | None = None


class SleepNight(WithAnnotationIds):
    night: str = Field(description="wake date")
    google: SleepSummary | None = None
    performance: SleepPerformance | None = None


class SleepRange(Model):
    from_: str = Field(alias="from")
    to: str
    nights: list[SleepNight]
    annotations: list[Annotation]


class Segment(Model):
    start: str
    end: str


class Stage(Segment):
    stage: str = Field(description="awake, light, deep or rem")


class Hypnogram(Model):
    """The night's main sleep as stage segments (UTC), plus the brief wake-ups."""
    start: str
    end: str
    stages: list[Stage]
    brief: list[Segment]


# ---------------------------------------------------------------- runs ----

class WorkoutFigures(Model):
    """The run_report figures a workout list shows."""
    log_id: str
    date: str
    type: str
    name: str | None = None
    minutes: float | None = None
    km: float | None = None
    avg_hr: float | None = None
    trimp: float | None = None
    min_light: float | None = None
    min_moderate: float | None = None
    min_vigorous: float | None = None
    min_peak: float | None = None
    pct_below_first: float | None = None
    pct_below_vigorous: float | None = Field(None, description="% of run time at or below the top of Z2")
    ef: float | None = None
    decoupling_pct: float | None = None
    hrr60: Num | None = None
    hrr120: Num | None = None
    steady: Num | None = None
    notes: str | None = None


class WorkoutAnalysis(WorkoutFigures):
    """A whole analysis_workouts row."""
    end_local_h: float | None = None
    rest_hr: Num | None = None
    max_hr: Num | None = None
    first_boundary: Num | None = None
    climb_m_per_km: float | None = None
    stop_s: Num | None = None
    split_cv: float | None = None
    hr_end: Num | None = None
    hrr_reliable: Num | None = None


class RunRow(WorkoutFigures, WithAnnotationIds):
    id: str = Field(description="short id, for /runs/{id}")
    strain: float | None = None


class RunningSummary(Model):
    """Totals over the runs (type RUNNING) in a list."""
    runs: int
    km: float
    minutes: float
    trimp: float
    easy_pct: float | None = Field(
        None, description="share of run time at or below the top of Z2, weighted by minutes; null without data")


class RunList(Model):
    from_: str = Field(alias="from")
    to: str
    runs: list[RunRow]
    running: RunningSummary
    annotations: list[Annotation]


class KmSplit(Model):
    km: int
    pace_s_per_km: Num | None = None
    avg_hr: Num | None = None


class GoogleExercise(Model):
    """Google's summary of a workout (MCP cache `exercises`)."""
    name: str | None = None
    duration_min: Num | None = None
    calories: Num | None = None
    avg_hr: Num | None = None
    steps: Num | None = None
    distance_km: float | None = None
    start_time: str | None = None
    end_time: str | None = None
    active_seconds: float | None = None
    metrics_summary: dict[str, Any] = Field(default_factory=dict)
    km_splits: list[KmSplit] = Field(default_factory=list)


class HrZone(Model):
    zone: Literal["Z1", "Z2", "Z3", "Z4", "Z5"]
    minutes: float | None = None
    pct: float | None = Field(None, description="% of the workout's zone minutes")
    from_bpm: int | None = Field(None, description="lowest bpm in the zone; null = open")
    to_bpm: int | None = Field(None, description="first bpm above the zone (exclusive); null = open")


class HrZones(Model):
    """Minutes per zone. Z1, Z2 and Z5 are Google's light, moderate and peak
    zones; Z3 and Z4 split Google's vigorous zone at split_bpm."""
    zones: list[HrZone]
    split_bpm: int | None = None
    vigorous_split_by_hr: bool = Field(
        description="false: no HR samples in the vigorous zone, so all vigorous minutes count as Z3")


class RunDetail(Model):
    id: str
    analysis: WorkoutAnalysis
    strain: float | None = None
    google: GoogleExercise | None = None
    hr_zones: HrZones
    annotations: list[Annotation]


class SeriesPoint(Model):
    s: Num = Field(description="seconds since the workout start")
    v: Num | None = None


class HrPoint(Model):
    s: Num
    bpm: Num


class Walk(Model):
    from_: Num = Field(alias="from")
    to: Num


class WorkoutSeries(Model):
    """Time series of one workout, in seconds since its start."""
    id: str
    start: str
    duration: Num
    hr: list[HrPoint] = Field(description="10 s means")
    pace: list[SeriesPoint] = Field(description="s/km every 10 s; v null when standing")
    pace_source: Literal["watch", "gps"] | None = None
    cadence: list[SeriesPoint]
    cadence_source: str | None = None
    walks: list[Walk]
    zones: dict[str, list[Num | None]] = Field(description="Google's HR zone limits: {LIGHT: [min, max], ...}")
    dynamics: dict[str, list[SeriesPoint]] = Field(description="Takeout running dynamics by metric")
    route: list[list[float]] | None = Field(None, description="[lat, lon] points, at most ~400")


# ----------------------------------------------------------- dashboard ----

class StrainToday(Model):
    date: str
    strain: float | None = None
    band: str | None = None
    load: float | None = None
    minutes_counted: float | None = None
    partial: Num | None = None


class StrainTarget(Model):
    low: float
    high: float
    label: str
    basis: str


class DashboardSleep(Model):
    night: str
    google: SleepSummary | None = None
    performance: SleepPerformance | None = None


class DashboardStrain(Model):
    today: StrainToday | None = None
    target: StrainTarget | None = Field(None, description="from the day's Recovery band; null without a score")


class DailyActivity(Model):
    steps: Num | None = None
    distance_km: float | None = None
    active_minutes: Num | None = None


class WeekRun(Model):
    log_id: str
    id: str
    date: str
    km: float | None = None
    minutes: float | None = None
    trimp: float | None = None
    avg_hr: float | None = None
    pct_below_vigorous: float | None = None


class RunningWeek(RunningSummary):
    monday: str
    list: list[WeekRun]


class DailyCheck(Model):
    flag: str | None = Field(None, description="OK, AMBER or RED; null without a baseline")
    warning: str | None = None
    hrv_status: str | None = None
    z: dict[str, float] = Field(description="z-scores, positive = worse than baseline")
    excluded_event: Num | None = None


class Dashboard(Model):
    date: str
    recovery: RecoveryScore | None = None
    sleep: DashboardSleep
    strain: DashboardStrain
    steps: Num | None = None
    google_daily_activity: DailyActivity | None = None
    running_week: RunningWeek
    daily_check: DailyCheck | None = None
    annotations: list[Annotation]


# --------------------------------------------------------------- trend ----

class TrendPoint(WithAnnotationIds):
    date: str
    value: Num


class TrendSummary(Model):
    n: int
    mean: float
    min: Num
    max: Num
    first: Num
    last: Num


class Trend(Model):
    metric: str
    from_: str = Field(alias="from")
    to: str
    points: list[TrendPoint]
    summary: TrendSummary | None = None
    annotations: list[Annotation]


# ------------------------------------------------------- explain score ----

class RecoveryComponent(Model):
    z: float | None = None
    weight: float | None = None
    contribution: float | None = None


class RecoveryExplanation(Model):
    date: str
    score: Literal["recovery"]
    error: str | None = Field(None, description="set (and the rest null) when there is no score for the date")
    value: float | None = None
    band: str | None = None
    formula: str | None = None
    components: dict[str, RecoveryComponent] | None = None
    composite: float | None = None
    s: float | None = None
    penalty: float | None = None
    flags: str | None = None
    raw: dict[str, Num | str | None] | None = None
    baseline_n: Num | None = None
    provisional: bool | None = None
    excluded_event_night: bool | None = None
    annotations: list[Annotation] = Field(default_factory=list)


class SleepComponent(Model):
    value: float | None = None
    weight: float
    contribution: float | None = None


class SleepNeed(Model):
    total_min: float | None = None
    baseline_min: float | None = None
    from_strain_min: float | None = None
    from_sleep_debt_min: float | None = None
    minus_naps_min: float | None = None


class SleepExplanation(Model):
    date: str
    score: Literal["sleep"]
    error: str | None = None
    value: float | None = None
    formula: str | None = None
    components: dict[str, SleepComponent] | None = None
    need: SleepNeed | None = None
    asleep_min: float | None = None
    hypnogram: Hypnogram | None = None
    annotations: list[Annotation] = Field(default_factory=list)


class StrainWorkout(Model):
    log_id: str
    id: str
    name: str | None = None
    log_type: str | None = None
    minutes: float | None = None
    cardio_load: float | None = None
    muscular_load: float | None = None
    load: float | None = None
    strain: float | None = None


class StrainExplanation(Model):
    date: str
    score: Literal["strain"]
    error: str | None = None
    value: float | None = None
    band: str | None = None
    formula: str | None = None
    k: float | None = None
    load: float | None = None
    cardio_load: float | None = None
    muscular_load: float | None = None
    minutes_counted: float | None = None
    valid_minutes: float | None = None
    rhr_used: float | None = None
    hrmax_used: float | None = None
    partial_day: bool | None = None
    workouts: list[StrainWorkout] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)


Explanation = Annotated[RecoveryExplanation | SleepExplanation | StrainExplanation, Field(discriminator="score")]


# ------------------------------------------------------------ activity ----

class ActivityDay(WithAnnotationIds):
    """One day of activity. active_<level> keys appear for every activity
    level the watch reports (light, moderate, vigorous, ...)."""
    date: str
    steps: Num | None = Field(None, description="watch steps, else Google's daily steps")
    watch_steps: Num | None = None
    google_steps: Num | None = None
    calories: Num | None = None
    floors: Num | None = None
    distance_km: float | None = None
    azm: Num | None = None
    azm_fat_burn: Num | None = None
    azm_cardio: Num | None = None
    azm_peak: Num | None = None
    active_light: Num | None = None
    active_moderate: Num | None = None
    active_vigorous: Num | None = None
    active_min: Num | None = None
    sedentary_min: Num | None = None


class ActivityRange(Model):
    from_: str = Field(alias="from")
    to: str
    days: list[ActivityDay]
    annotations: list[Annotation]


class HrBucket(Model):
    t: str = Field(description="local clock time of the 5-minute bucket")
    bpm: float
    min: Num
    max: Num


class HourSteps(Model):
    hour: int
    steps: Num


class DaySleep(Model):
    date: str
    start_time: str | None = None
    end_time: str | None = None
    total_minutes: Num | None = None


class DayWorkout(Model):
    log_id: str
    id: str
    type: str
    name: str | None = None
    minutes: float | None = None
    km: float | None = None
    avg_hr: float | None = None


class DayDetail(Model):
    date: str
    hr_5min: list[HrBucket]
    steps_by_hour: list[HourSteps]
    sedentary_periods: list[Segment]
    sleep: DaySleep | None = None
    workouts: list[DayWorkout]
    annotations: list[Annotation]


# ------------------------------------------------------------ training ----

class StrainDay(WithAnnotationIds):
    date: str
    strain: float | None = None
    band: str | None = None
    load: float | None = None
    cardio_load: float | None = None
    muscular_load: float | None = None
    partial: Num | None = None


class TrainingWeek(Model):
    """An analysis_weekly row."""
    monday: str
    week: str | None = None
    workouts: Num | None = None
    trimp: float | None = None
    fitness: float | None = None
    fatigue: float | None = None
    form: float | None = None
    pct_below_first: float | None = None
    pct_below_vigorous: float | None = None
    sri: float | None = None
    sri_p_same: float | None = None
    partial: Num | None = None


class AcwrDay(Model):
    date: str
    acwr: float | None = None
    acwr_label: str | None = None


class PersonalRecord(Model):
    record: str
    km: float | None = None
    seconds: Num | None = Field(None, description="null for distance records")
    date: str
    exercise_id: str


class Training(Model):
    from_: str = Field(alias="from")
    to: str
    strain: list[StrainDay]
    weeks: list[TrainingWeek]
    acwr: list[AcwrDay]
    records: list[PersonalRecord]
    annotations: list[Annotation]


# ----------------------------------------------------------- long-term ----

class HealthspanRow(Model):
    """A score_healthspan row."""
    computed_on: str
    variant: str
    metric: str
    value: float | None = None
    unit: str | None = None
    referent: Num | str | None = Field(None, description='the guideline value, or a range as text: "(7.0, 9.0)"')
    years: float | None = None
    years_adjusted: float | None = None
    note: str | None = None


class HealthspanPoint(Model):
    computed_on: str
    variant: str
    value: float | None = None


class Healthspan(Model):
    computed_on: str | None = None
    variants: dict[str, list[HealthspanRow]]
    history: list[HealthspanPoint]


class MonthMean(Model):
    month: str
    mean: float
    n: int


class Vo2Day(Model):
    date: str
    vo2_max: float | None = None
    cardio_fitness_level: str | None = None


class RunVo2(Model):
    date: str
    vo2max: float


class WeightDay(Model):
    date: str
    weight_kg: float | None = None
    bmi: float | None = None
    fat_pct: float | None = None


class EcgSummary(Model):
    reading_id: str
    date: str
    start_time: str | None = None
    avg_bpm: Num | None = None
    classification: str | None = None
    duration_sec: float | None = None


class Ecg(EcgSummary):
    sampling_hz: Num | None = None
    scaling_factor: Num | None = None
    waveform: list[Num]


class Assessment(Model):
    date: str
    kind: str
    classification: str | None = None
    details: str | None = None


class LongTerm(Model):
    healthspan: Healthspan
    monthly: dict[str, list[MonthMean]]
    vo2max: list[Vo2Day]
    run_vo2max: list[RunVo2]
    weight: list[WeightDay]
    height_cm: float | None = None
    ecg: list[EcgSummary]
    assessments: list[Assessment]
    annotations: list[Annotation]


# ------------------------------------------------------- data browser ----

class TableInfo(Model):
    source: Literal["health", "google"]
    table: str
    group: str
    rows: int
    columns: list[str]
    date_column: str | None = None
    first: str | None = None
    last: str | None = None


class Catalog(Model):
    tables: list[TableInfo]


class TableRows(Model):
    source: Literal["health", "google"]
    table: str
    columns: list[str]
    date_column: str | None = None
    total: int
    limit: int
    offset: int
    rows: list[dict[str, Any]] = Field(description="raw rows; long text cut, epoch columns also as <col>_local")


# ---------------------------------------------------------- annotations ----

class AnnotationChange(Model):
    annotation: Annotation
    recompute: SyncRun | None = Field(None, description="the recompute job started by this change, if any")


# ---------------------------------------------------------------- meta ----

class NamedStep(Model):
    name: str
    label: str


class AnnotationMeta(Model):
    kinds: list[str]
    statuses: list[str]
    sources: list[str]
    target_prefixes: list[str]


class SyncMeta(Model):
    reasons: list[str] = Field(description="reasons a sync job can have; a recompute job has reason recompute")
    steps: dict[str, list[NamedStep]] = Field(description="the steps a job runs, by its reason")
    open_min_interval_min: float = Field(description="reason=open skips if the last sync is younger than this")


class Band(Model):
    band: str
    from_: Num = Field(alias="from")
    to: Num


class Meta(Model):
    annotation: AnnotationMeta
    sync: SyncMeta
    recovery_bands: list[Band]
    strain_bands: list[Band]
    strain_targets: dict[str, StrainTarget]
    hr_zones: list[str]
    trend_metrics: list[str]
