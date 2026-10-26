"""Shared helpers for the WHOOP-style scores (strain.py, recovery.py,
healthspan.py). Builds on baseline.py (data loaders, event exclusions) and
adds what the scores need on top: per-minute HR, robust 30-night baselines,
the frozen config file, simple statistics and the score tables.

Spec: docs/scores.md. Pure standard library (no numpy). Run all scores with
    python -m health_dashboard scores
"""

import json
import math
import statistics
from datetime import datetime, timezone

from ..analysis.baseline import parse_time  # noqa: F401  (re-exported)
from . import cache as score_cache

BASE_NIGHTS = 30                 # robust baseline: last 30 eligible nights
BASE_MIN = 7                     # fewer -> no score
BASE_PROVISIONAL = 14            # fewer -> score marked provisional
PHI = statistics.NormalDist().cdf


def local_date(t, offset):
    return datetime.fromtimestamp(t + (offset or 0), tz=timezone.utc).date()


def local_midnight_utc(d, offset):
    """UTC epoch of local midnight starting day d."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) - (offset or 0)


# ------------------------------------------------------------- config ----
# Frozen calibration (Strain k, Recovery weights), fitted on your own history
# the first time the scores run; personal, so it lives with the data
# (ctx.paths.scores_config).

def load_config(ctx):
    path = ctx.paths.scores_config
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_config(ctx, cfg):
    ctx.paths.scores_config.write_text(json.dumps(cfg, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------- age ----

def age_years(mcp):
    """Age from the cached Google profile (the API exposes age, not the birth
    date). Returns (age, source)."""
    row = mcp.execute("SELECT body FROM account WHERE resource='profile'").fetchone()
    if row:
        body = json.loads(row[0])
        if body.get("age"):
            return float(body["age"]), "Google profile (cached)"
    return None, "missing"


# ----------------------------------------------------- per-minute HR -----

def cache(ctx, full=False):
    """Bring the raw-data cache (scores/cache.py) up to date, once per Context
    (ctx.cache). full=True (`health scores --full`) rebuilds it from scratch."""
    if "score_cache" not in ctx.cache:
        ctx.cache["score_cache"] = score_cache.refresh(ctx.health, ctx.watch, full=full)
    return ctx.cache["score_cache"]


def minute_hr(ctx):
    """{utc_minute: (median_bpm, utc_offset)} for minutes with >= 10 samples.
    Read from the cache once per Context (ctx.cache)."""
    if "minute_hr" not in ctx.cache:
        cache(ctx)
        ctx.cache["minute_hr"] = score_cache.minute_hr(ctx.health)
    return ctx.cache["minute_hr"]


# ------------------------------------------------------ robust stats -----

def robust_center_spread(vals):
    """(median, 1.4826*MAD); spread falls back to the SD when MAD is 0."""
    med = statistics.median(vals)
    spread = 1.4826 * statistics.median(abs(v - med) for v in vals)
    if spread == 0 and len(vals) > 1:
        spread = statistics.stdev(vals)
    return med, spread


def prior_eligible(ctx, series, night, n=BASE_NIGHTS):
    """Values of up to n most recent nights before `night` that have a value
    and are not illness/alcohol nights. `series` = {date: value}."""
    out = []
    for d in sorted((d for d in series if d < night), reverse=True):
        if ctx.is_excluded(d) or series[d] is None:
            continue
        out.append(series[d])
        if len(out) == n:
            break
    return out


def robust_z(ctx, series, night, sign=+1, floor=0.0):
    """(z, n_baseline). Positive z = better when sign is chosen accordingly.
    `floor` is a minimum spread, for coarse signals (integer RHR)."""
    x = series.get(night)
    vals = prior_eligible(ctx, series, night)
    if x is None or len(vals) < BASE_MIN:
        return None, len(vals)
    med, spread = robust_center_spread(vals)
    spread = max(spread, floor)
    if not spread:
        return None, len(vals)
    return sign * (x - med) / spread, len(vals)


def percentile(vals, p):
    """Linear-interpolated percentile, p in 0..100."""
    v = sorted(vals)
    if not v:
        return None
    k = (len(v) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None, len(pairs)
    a, b = zip(*pairs)
    try:
        return statistics.correlation(a, b), len(pairs)
    except statistics.StatisticsError:
        return None, len(pairs)


def _ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None, len(pairs)
    a, b = zip(*pairs)
    return statistics.correlation(_ranks(list(a)), _ranks(list(b))), len(pairs)


# ---------------------------------------------------------- tables -------

def write_rows(health, table, columns, key, rows):
    """Create `table` if needed (first column(s) in `key` form the primary key)
    and upsert rows (list of dicts)."""
    cols = ", ".join(f"{c} {'TEXT' if c in key or c in ('band', 'flags', 'hrv_source', 'variant', 'metric', 'unit', 'name', 'note') else 'REAL'}"
                     for c in columns)
    health.execute(f"CREATE TABLE IF NOT EXISTS {table} ({cols}, PRIMARY KEY ({', '.join(key)}))")
    ph = ", ".join("?" for _ in columns)
    health.executemany(f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({ph})",
                       [[r.get(c) for c in columns] for r in rows])
    health.commit()


def fmt(x, nd=1):
    return "-" if x is None else f"{x:.{nd}f}"
