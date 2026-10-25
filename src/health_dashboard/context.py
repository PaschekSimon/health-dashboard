"""Everything the analysis and score code reads besides its arguments: paths,
the watch settings, the illness/alcohol exclusions, today's date and the two
database connections. Built once per run by `Context.open()` and passed in,
so nothing is loaded at import time and tests can point it anywhere.
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from .analysis.annotation_effects import Exclusions, load_exclusions
from .config import Paths, get_paths
from .settings import load_settings

ANALYSIS_DELAY = timedelta(days=6)  # the watch's first days calibrate and would skew baselines


@dataclass
class Context:
    paths: Paths
    watch: str                    # data source name of the watch, see settings.py
    watch_start: date             # first day of watch data
    exclusions: Exclusions
    health: sqlite3.Connection    # data/health.db
    mcp: sqlite3.Connection       # the google-health MCP's cache
    today: date = field(default_factory=date.today)
    cache: dict = field(default_factory=dict)  # per-run caches, e.g. scores.common.minute_hr

    @property
    def analysis_start(self) -> date:
        """Analyses start 6 days after the first watch day."""
        return self.watch_start + ANALYSIS_DELAY

    def is_ill(self, d):
        return self.exclusions.is_ill(d)

    def is_excluded(self, night):
        return self.exclusions.is_excluded(night)

    @classmethod
    def open(cls, paths: Paths | None = None, today: date | None = None):
        """Context for the data in `paths` (default: from the environment).
        The takeout_* tables are created empty if no Takeout was imported yet,
        so the scripts can query them."""
        from .sync import takeout_import
        paths = paths or get_paths()
        settings = load_settings(paths)
        health = sqlite3.connect(paths.health_db)
        health.executescript(takeout_import.SCHEMA)
        return cls(paths=paths, watch=settings.watch, watch_start=settings.watch_start,
                   exclusions=load_exclusions(paths.health_db), health=health,
                   mcp=sqlite3.connect(paths.mcp_db), today=today or date.today())

    def close(self):
        self.health.close()
        self.mcp.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
