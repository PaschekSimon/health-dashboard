"""Shared fixtures: the synthetic database (tests/fixture_db.py) and the scores
computed on it.

The score code reads everything through a Context (paths, watch settings,
exclusions, connections, today). `fixture_context` builds one for the
fixture, with the calibration file (scores_config.json) in the fixture's
folder, so the tests never read or write your real data.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from fixture_db import make_fixture  # tests/ is on sys.path (pyproject.toml)

from health_dashboard.analysis.annotation_effects import load_exclusions
from health_dashboard.config import Paths
from health_dashboard.context import Context
from health_dashboard.scores import healthspan, recovery, strain

SCORE_TABLES = {   # table -> ORDER BY
    "score_strain_daily": "date",
    "score_strain_workout": "log_id",
    "score_sleep_daily": "date",
    "score_recovery_daily": "date",
    "score_healthspan": "variant, metric",
}


def fixture_context(fx, data_dir=None):
    """A Context on the fixture's databases, frozen at fx.today. The
    calibration (scores_config.json) lives in data_dir (default fx.dir)."""
    paths = Paths(data=Path(data_dir or fx.dir), mcp_db=fx.mcp_db)
    return Context(paths=paths, watch=fx.watch, watch_start=fx.start,
                   exclusions=load_exclusions(fx.health_db),
                   health=sqlite3.connect(fx.health_db), mcp=sqlite3.connect(fx.mcp_db), today=fx.today)


def run_scores(fx, data_dir=None):
    """Run Strain, Recovery + Sleep and Healthspan on the fixture, like
    `health scores` does. Returns {"tables": {table: [row dicts]}, "config": {...}}."""
    with fixture_context(fx, data_dir) as ctx:
        strain.compute(ctx, verbose=False)
        recovery.compute(ctx, verbose=False)
        healthspan.compute(ctx, verbose=False)
        config = json.loads(ctx.paths.scores_config.read_text(encoding="utf-8"))
    return {"tables": read_scores(fx.health_db), "config": config}


def read_scores(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY {order}")]
                for t, order in SCORE_TABLES.items()}
    finally:
        conn.close()


@pytest.fixture(scope="session")
def fixture_data(tmp_path_factory):
    """The synthetic database, built once per test run (a few seconds)."""
    return make_fixture(tmp_path_factory.mktemp("fixture"))


@pytest.fixture(scope="session")
def scored(fixture_data):
    """All scores computed once on the fixture: {"tables": ..., "config": ...}."""
    return run_scores(fixture_data)
