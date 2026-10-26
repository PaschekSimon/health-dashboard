"""What each annotation kind does to the analyses: the one place that decides
which nights a piece of context removes from the baselines.

Annotations (table `annotations` in health.db, written by the backend in
core/annotations.py) carry context the watch doesn't have:
illness, alcohol, travel, ... Only *confirmed* ones count here.

Nights are keyed by the local wake date, like everywhere else in the analysis
(night 2026-10-04 = the night from 3 to 4 October).

To make a new kind change the numbers, add one entry to EFFECTS:
    "travel": Effect(excludes="nights_in_range", group="illness"),
Kinds that aren't listed are stored and shown, but exclude nothing.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

KINDS = ("alcohol", "illness", "injury", "travel", "late_meal", "stress", "run_intent",
         "post_run_protocol", "bad_sleep_env", "note")


@dataclass(frozen=True)
class Effect:
    # "nights_in_range": every night from start to end (inclusive) is excluded.
    # "night": one night, the one after the evening of `start` (or the
    #          `night:` target if given).
    excludes: str
    # which exclusion set the nights go into: "illness" -> Exclusions.illness
    # (also drops days from Healthspan), "alcohol" -> Exclusions.alcohol
    # (also the "after alcohol" condition in weekly_summary). Both are
    # left out of every baseline.
    group: str


EFFECTS = {
    "illness": Effect(excludes="nights_in_range", group="illness"),
    "alcohol": Effect(excludes="night", group="alcohol"),
}


def _day(s):
    return date.fromisoformat(s[:10]) if s else None


def nights_of(kind, start, end=None, target=None):
    """(first night, last night) an annotation excludes, or None."""
    eff = EFFECTS.get(kind)
    if eff is None:
        return None
    if eff.excludes == "nights_in_range":
        d0 = _day(start)
        return d0, _day(end) or d0
    if eff.excludes == "night":
        if target and target.startswith("night:"):
            d = date.fromisoformat(target[6:16])
        elif len(start) > 10 and int(start[11:13]) < 12:
            d = _day(start)  # a time after midnight is already the night before that wake date
        else:
            d = _day(start) + timedelta(days=1)  # drinks in the evening -> next wake date
        return d, d
    raise ValueError(f"unknown effect {eff.excludes!r}")


def affects_scores(kind):
    """True if confirming/changing this kind changes analysis numbers."""
    return kind in EFFECTS


@dataclass
class Exclusions:
    """Illness periods and alcohol nights, left out of every baseline."""
    illness: list = field(default_factory=list)   # [(first night, last night)]
    alcohol: set = field(default_factory=set)     # {night}

    def is_ill(self, d):
        """True if d falls in a confirmed illness period."""
        return any(a <= d <= b for a, b in self.illness)

    def is_excluded(self, night):
        """True for illness and alcohol nights."""
        return self.is_ill(night) or night in self.alcohol


def load_exclusions(db_path):
    """Exclusions from the confirmed annotations in health.db; empty if the
    database or the table doesn't exist yet."""
    out = Exclusions()
    try:
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return out
    try:
        rows = conn.execute(
            "SELECT kind, start, \"end\", target FROM annotations WHERE status = 'confirmed' "
            "ORDER BY start, id").fetchall()
    except sqlite3.Error:
        return out
    finally:
        conn.close()
    for kind, start, end, target in rows:
        span = nights_of(kind, start, end, target)
        if span is None:
            continue
        group = EFFECTS[kind].group
        if group == "alcohol":
            d = span[0]
            while d <= span[1]:
                out.alcohol.add(d)
                d += timedelta(days=1)
        else:
            out.illness.append(span)
    return out
