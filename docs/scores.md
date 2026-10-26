# Scores

The dashboard's four scores follow what WHOOP has published about its own
(Strain, Recovery, Sleep Performance, Healthspan age), rebuilt from Google
Health data with published research where WHOOP gives no formula. They are
computed by `health scores` (`src/health_dashboard/scores/`) and stored in the `score_*` tables of
`data/health.db`. A night is always keyed by the date you wake up on.

All baselines are personal and robust: median and MAD over earlier nights,
skipping the last two nights and every night excluded by an annotation
(illness, alcohol; see `analysis/annotation_effects.py`). Fewer than 7 baseline
nights means no score; fewer than 14 marks it provisional.

## Strain (0 to 21)

How hard the day (or one workout) was on your cardiovascular system.

1. Median heart rate per minute (minutes with at least 10 samples).
2. Heart-rate reserve fraction `HRr = (HR − RHR) / (HRmax − RHR)`, where RHR is
   the 14-night median of your non-REM heart rate and HRmax is the highest
   30-second workout average of the last 180 days, never below `208 − 0.7 × age`.
3. Load per minute (Banister TRIMP weighting) `0.64 × HRr × e^(1.92 × HRr)`,
   counted only when `HRr ≥ 0.30`, so sitting and standing don't add up all day.
   Strength workouts add `minutes × 0.5`, because heart rate underrates lifting.
4. `Strain = 21 × (1 − e^(−L / k))`, with `k` fitted once so your
   95th-percentile day lands at 17.5. `k` is then frozen in
   `data/scores_config.json`; rerun with `--recalibrate` to refit it.

The day's **Strain target** depends on Recovery: green 14–18, yellow 10–14,
red 0–12 (easy only). The idea that you should train hard when recovered and
easy when not comes from HRV-guided training studies (Kiviniemi 2007,
Vesterinen 2016, Javaloyes 2019). The bands follow WHOOP's Strain categories.

## Recovery (1 to 99 %)

How ready your body is today, from last night.

- **HRV**: ln of Google's night-average RMSSD (deep-sleep windows as a fallback).
- **Resting HR**: Google's daily resting heart rate.
- **Sleep**: Sleep Performance, `z = (SP − 85) / 10`.
- **Breathing rate**: only counts as a penalty when it rises.

Each signal becomes a z-score against a 30-night baseline, with the sign set so
that positive means better. They are combined as
`C = 0.50 z_HRV + 0.25 z_RHR + 0.15 z_sleep + 0.10 z_resp`. Skin temperature
≥ +0.5 °C or SpO2 ≥ 2 points below baseline subtracts 0.5 as an illness signal.
`Recovery = 1 + 98 × Φ(C / s)`, where s is the spread of C over the baseline,
so a normal night sits near 50 %.

The **early warning** (`health daily-check`) is separate. It flags AMBER
when one physiological signal is more than 2 robust z away from your baseline,
or two signals are more than 1.5 away, and RED when AMBER holds two nights running.

## Sleep Performance (0 to 100 %)

`0.60 Sufficiency + 0.15 Consistency + 0.15 Efficiency + 0.10 Calm`

- **Sufficiency**: hours asleep compared with the night's sleep need, capped at 100 %.
- **Sleep need**:
  - Baseline: starts at 8.2 h and is then personalised over 60 nights, kept between 7.0 and 9.5 h.
  - Plus extra minutes for yesterday's Strain above your median day. The rate is
    learned from your own nights, 0 to 4 min per point, capped at 45 min.
  - Plus 0.5 / 0.25 / 0.125 of the shortfall of the previous three nights.
  - Minus yesterday's naps.
- **Consistency**: the Sleep Regularity Index over the last 5 nights.
- **Efficiency**: time asleep as a share of the sleep session.
- **Calm**: the share of sleep without stress signs. A stress sign is an HRV
  window below your 10th percentile, or a heart rate more than 2 SD above your
  non-REM baseline.

No validated formula for sleep need exists anywhere, so treat the need as an estimate.

## Healthspan age and Pace of Aging

Each metric's all-cause mortality hazard ratio against a guideline value
becomes years with the Gompertz rule `years = 10 × ln(HR)`, and the years are
added to your age. The metrics are VO2 max, resting HR, steps, moderate and
vigorous minutes, strength minutes, sleep duration and sleep regularity.
Correlated pairs (VO2 max + resting HR, steps + moderate minutes) are weighted
0.7 so they don't count twice. Age comes from your Google profile.

The VO2 max referent (48, capped at 60) is the FRIEND norm for men aged
20-29 (`REF` and `VO2_CAP` in `scores/healthspan.py`). For other ages or for
women, change those two values or the Healthspan age will be off.

- **Healthspan age** uses all available data (WHOOP uses 6 months).
- **Pace of Aging** compares the last 30 days with that:
  `(0.5 + (age_30d − age_all)) / 0.5`, clamped to −1.0× … 3.0×.

The years per metric are the useful part: they show which habit costs the most.
The absolute age is only good to within several years.

## Other analyses

- `run_report.py` covers every run.
  - Minutes per heart-rate zone.
  - Efficiency factor (speed ÷ HR) on steady runs.
  - Aerobic decoupling (first half against second half).
  - Heart-rate recovery 60 and 120 s after the run.
  - TRIMP for every workout.
- `weekly_summary.py` covers every week.
  - Training load.
  - Fitness (42-day average), fatigue (7-day average) and form.
  - Share of easy time.
  - Sleep Regularity Index.
  - Next-night effects of annotated events.

These are personal trends modelled on published methods, not medical measurements.

## Fast recompute

Reading every raw heart-rate sample (one every 2 to 3 s) was most of
`health scores`' time, and it grew with every day of data. So
`scores/cache.py` keeps the aggregates the scores need in health.db
(per-minute median HR, HR samples and watch steps per day) and rebuilds only
the days whose raw rows changed. SQLite triggers on `hr_samples` and
`steps_minute` mark those days, whichever script writes them. The score math
itself still runs over the whole history, because a rolling baseline or the
sleep-debt chain lets one changed day move later days, so the numbers are
exactly those of a full recompute (`tests/test_score_cache.py` checks this).
`health scores --full` rebuilds the cache from scratch; it also rebuilds by
itself when the watch in `settings.json` changes or a trigger is missing.
