"""Shared helpers for the WHOOP-style scores (strain.py, recovery.py,
healthspan.py). Builds on baseline.py (data loaders, event exclusions) and
adds what the scores need on top: per-minute HR, robust 30-night baselines,
the frozen config file, simple statistics and the score tables.

Spec: docs/scores.md. Pure standard library, so it runs with the
MCP's Python:
    %APPDATA%\\uv\\tools\\google-health-mcp\\Scripts\\python.exe -I run_scores.py
"""

import json
import math
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import baseline as bl
from baseline import WATCH, is_excluded, parse_time  # noqa: F401  (re-exported)

# Frozen calibration (Strain k, Recovery weights), fitted on your own history
# the first time the scores run; personal, so it lives with the data.
CONFIG = Path(__file__).resolve().parent.parent / "data" / "scores_config.json"
START = bl.WATCH_START           # back-fill start
BASE_NIGHTS = 30                 # robust baseline: last 30 eligible nights
BASE_MIN = 7                     # fewer -> no score
BASE_PROVISIONAL = 14            # fewer -> score marked provisional
PHI = statistics.NormalDist().cdf


def connect():
    return bl.connect()


def today_local():
    return datetime.now().date()


def local_date(t, offset):
    return datetime.fromtimestamp(t + (offset or 0), tz=timezone.utc).date()


def local_midnight_utc(d, offset):
    """UTC epoch of local midnight starting day d."""
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) - (offset or 0)


# ------------------------------------------------------------- config ----

def load_config():
    if CONFIG.exists():
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    return {}


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, indent=2, default=str), encoding="utf-8")


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

_MINUTE_HR = None


def minute_hr(health):
    """{utc_minute: (median_bpm, utc_offset)} for minutes with >= 10 samples."""
    global _MINUTE_HR
    if _MINUTE_HR is not None:
        return _MINUTE_HR
    out, cur, vals, off = {}, None, [], 0
    for t, o, bpm in health.execute(
            "SELECT t, utc_offset, bpm FROM hr_samples WHERE source=? ORDER BY t", (WATCH,)):
        m = t // 60
        if m != cur:
            if cur is not None and len(vals) >= 10:
                out[cur] = (statistics.median(vals), off)
            cur, vals, off = m, [], o
        vals.append(bpm)
    if cur is not None and len(vals) >= 10:
        out[cur] = (statistics.median(vals), off)
    _MINUTE_HR = out
    return out


# ------------------------------------------------------ robust stats -----

def robust_center_spread(vals):
    """(median, 1.4826*MAD); spread falls back to the SD when MAD is 0."""
    med = statistics.median(vals)
    spread = 1.4826 * statistics.median(abs(v - med) for v in vals)
    if spread == 0 and len(vals) > 1:
        spread = statistics.stdev(vals)
    return med, spread


def prior_eligible(series, night, n=BASE_NIGHTS):
    """Values of up to n most recent nights before `night` that have a value
    and are not illness/alcohol nights. `series` = {date: value}."""
    out = []
    for d in sorted((d for d in series if d < night), reverse=True):
        if is_excluded(d) or series[d] is None:
            continue
        out.append(series[d])
        if len(out) == n:
            break
    return out


def robust_z(series, night, sign=+1, floor=0.0):
    """(z, n_baseline). Positive z = better when sign is chosen accordingly.
    `floor` is a minimum spread, for coarse signals (integer RHR)."""
    x = series.get(night)
    vals = prior_eligible(series, night)
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
