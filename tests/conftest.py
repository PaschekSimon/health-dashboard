"""Shared fixtures: the synthetic database (tests/fixture_db.py) and the scores
computed on it.

The analysis scripts read their settings at import time (data/settings.json,
the annotations in data/health.db) and write the frozen calibration to
data/scores_config.json. `scoring_env` points all of that at the fixture
instead, so the tests never read or write your real data. It is the only
place that knows about those module globals: when the scripts take their
settings as arguments, this is the one function to change.
"""

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

# scripts/ and tests/ are on sys.path (pytest.ini)
import annotation_effects
import baseline as bl
import healthspan
import recovery
import scores_common as sc
import strain
from fixture_db import make_fixture

SCORE_TABLES = {   # table -> ORDER BY
    "score_strain_daily": "date",
    "score_strain_workout": "log_id",
    "score_sleep_daily": "date",
    "score_recovery_daily": "date",
    "score_healthspan": "variant, metric",
}


def scoring_env(mp, fx, config_path):
    """Patch the scripts' import-time settings to the fixture (mp = a
    pytest.MonkeyPatch)."""
    ex = annotation_effects.load_exclusions(fx.health_db)
    for mod in (bl, sc):
        mp.setattr(mod, "WATCH", fx.watch)
    mp.setattr(bl, "WATCH_START", fx.start)
    mp.setattr(bl, "ANALYSIS_START", fx.start + timedelta(days=6))
    mp.setattr(bl, "ILLNESS_PERIODS", ex["illness"])
    mp.setattr(bl, "ALCOHOL_NIGHTS", ex["alcohol"])
    mp.setattr(sc, "START", fx.start)
    mp.setattr(sc, "CONFIG", config_path)
    mp.setattr(sc, "_MINUTE_HR", None)  # per-process cache of per-minute HR
    mp.setattr(sc, "today_local", lambda: fx.today)
    mp.setattr(healthspan, "START", bl.ANALYSIS_START)


def run_scores(fx, config_path=None):
    """Run Strain, Recovery + Sleep and Healthspan on the fixture, like
    run_scores.py does. Returns {"tables": {table: [row dicts]}, "config": {...}}."""
    config_path = Path(config_path or fx.dir / "scores_config.json")
    with pytest.MonkeyPatch.context() as mp:
        scoring_env(mp, fx, config_path)
        health, mcp = sqlite3.connect(fx.health_db), sqlite3.connect(fx.mcp_db)
        try:
            strain.compute(health, mcp, verbose=False)
            recovery.compute(health, mcp, verbose=False)
            healthspan.compute(health, mcp, verbose=False)
        finally:
            mcp.close()
            health.close()
    return {"tables": read_scores(fx.health_db), "config": json.loads(config_path.read_text(encoding="utf-8"))}


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
