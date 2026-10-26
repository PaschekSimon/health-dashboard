"""GET /meta: the fixed lists and limits the web app would otherwise copy from
the backend (annotation kinds, sync steps, score bands, trend metrics), so a
change here shows up in every client.
"""

from . import annotations as ann
from . import jobs, queries, zones

# Labels for the sync status line, one per job step in jobs.py.
STEP_LABELS = {"mcp_cache": "Google daily data", "health_sync": "watch samples", "daily_check": "daily check",
               "run_report": "run analysis", "weekly_summary": "weekly summary", "run_scores": "scores"}

# Same cut-offs as band() in scripts/recovery.py and scripts/strain.py.
RECOVERY_BANDS = [{"band": "red", "from": 0, "to": 34}, {"band": "yellow", "from": 34, "to": 67},
                  {"band": "green", "from": 67, "to": 100}]
STRAIN_BANDS = [{"band": "light", "from": 0, "to": 10}, {"band": "moderate", "from": 10, "to": 14},
                {"band": "high", "from": 14, "to": 18}, {"band": "all out", "from": 18, "to": 21}]


def _steps(steps):
    return [{"name": name, "label": STEP_LABELS.get(name, name)} for name, _ in steps]


def meta():
    derived = _steps(jobs.DERIVED_STEPS)
    return {
        "annotation": {"kinds": list(ann.KINDS), "statuses": list(ann.STATUSES), "sources": list(ann.SOURCES),
                       "target_prefixes": list(ann.TARGET_PREFIXES)},
        "sync": {"reasons": list(jobs.SYNC_REASONS),
                 # the steps a job runs, by its reason (a sync_runs row's `reason`)
                 "steps": {**{r: _steps(jobs.SYNC_STEPS) + derived for r in jobs.SYNC_REASONS},
                           "recompute": derived},
                 "open_min_interval_min": jobs.OPEN_MIN_INTERVAL.total_seconds() / 60},
        "recovery_bands": RECOVERY_BANDS,
        "strain_bands": STRAIN_BANDS,
        "strain_targets": {band: queries.strain_target(band) for band in queries.STRAIN_TARGET},
        "hr_zones": list(zones.ZONE_NAMES),
        "trend_metrics": list(queries.METRICS),
    }
