"""The daily cache (google_health.db): schema, writers and the sync log.

Derived from google-health-mcp 1.8.0 (db.py), GPL-3.0-or-later.
Modified 2026-10-28: the default path comes from health_dashboard.config; the
tables nothing here fills any more (core_temperature, food_log, irn,
weight_readings, body_fat_readings) and the MCP's query helpers are gone. A
cache written by google-health-mcp opens unchanged; its extra tables are just
no longer updated.
"""

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

# Changing this? Every column added to a table an existing cache already has
# needs an ALTER in MIGRATIONS below, since CREATE TABLE IF NOT EXISTS leaves
# an existing table as it is.
SCHEMA = """
CREATE TABLE IF NOT EXISTS heart_rate (
    date TEXT PRIMARY KEY,
    resting_hr INTEGER,
    zones TEXT,
    provider TEXT,
    calculation_method TEXT,
    data_source TEXT
);

CREATE TABLE IF NOT EXISTS activity (
    date TEXT PRIMARY KEY,
    steps INTEGER,
    calories_out INTEGER,
    active_minutes INTEGER,
    very_active_minutes INTEGER,
    fairly_active_minutes INTEGER,
    lightly_active_minutes INTEGER,
    sedentary_minutes INTEGER,
    floors INTEGER,
    distance_km REAL,
    provider TEXT
);

CREATE TABLE IF NOT EXISTS exercises (
    log_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    name TEXT,
    duration_min INTEGER,
    calories INTEGER,
    avg_hr INTEGER,
    steps INTEGER,
    distance_km REAL,
    distance_unit TEXT,
    start_time TEXT,
    source TEXT,
    log_type TEXT,
    provider TEXT,
    end_time TEXT,
    start_utc_offset TEXT,
    end_utc_offset TEXT,
    active_seconds REAL,
    notes TEXT,
    create_time TEXT,
    update_time TEXT,
    metrics_summary TEXT,
    exercise_metadata TEXT,
    exercise_events TEXT,
    splits TEXT,
    split_summaries TEXT,
    data_source TEXT
);

CREATE INDEX IF NOT EXISTS idx_exercises_date ON exercises(date);

CREATE TABLE IF NOT EXISTS sleep (
    date TEXT PRIMARY KEY,
    total_minutes INTEGER,
    efficiency INTEGER,
    start_time TEXT,
    end_time TEXT,
    deep_minutes INTEGER,
    light_minutes INTEGER,
    rem_minutes INTEGER,
    wake_minutes INTEGER,
    sessions INTEGER,
    sleep_period_minutes INTEGER,
    provider TEXT
);

CREATE TABLE IF NOT EXISTS weight (
    date TEXT PRIMARY KEY,
    weight_kg REAL,
    bmi REAL,
    fat_pct REAL,
    provider TEXT
);

CREATE TABLE IF NOT EXISTS spo2 (
    date TEXT PRIMARY KEY,
    avg REAL,
    min REAL,
    max REAL,
    avg_ci_low REAL,
    avg_ci_high REAL,
    provider TEXT,
    std_dev REAL,
    data_source TEXT
);

CREATE TABLE IF NOT EXISTS hrv (
    date TEXT PRIMARY KEY,
    daily_rmssd REAL,
    deep_rmssd REAL,
    provider TEXT,
    entropy REAL,
    non_rem_hr INTEGER,
    data_source TEXT
);

CREATE TABLE IF NOT EXISTS azm (
    date TEXT PRIMARY KEY,
    total_minutes INTEGER,
    fat_burn_minutes INTEGER,
    cardio_minutes INTEGER,
    peak_minutes INTEGER,
    provider TEXT
);

CREATE TABLE IF NOT EXISTS breathing_rate (
    date TEXT PRIMARY KEY,
    breaths_per_min REAL,
    provider TEXT,
    data_source TEXT
);

CREATE TABLE IF NOT EXISTS skin_temperature (
    date TEXT PRIMARY KEY,
    nightly_relative REAL,
    log_type TEXT,
    nightly_absolute REAL,
    baseline REAL,
    provider TEXT,
    nightly_stddev_30d REAL,
    data_source TEXT
);

CREATE TABLE IF NOT EXISTS cardio_fitness (
    date TEXT PRIMARY KEY,
    vo2_max_low REAL,
    vo2_max_high REAL,
    vo2_max REAL,
    provider TEXT,
    cardio_fitness_level TEXT,
    estimated INTEGER,
    vo2_max_covariance REAL,
    data_source TEXT
);

CREATE TABLE IF NOT EXISTS ecg (
    reading_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    start_time TEXT,
    avg_bpm INTEGER,
    classification TEXT,
    lead_number INTEGER,
    sampling_hz INTEGER,
    scaling_factor INTEGER,
    duration_sec REAL,
    waveform TEXT,
    device_model TEXT,
    provider TEXT
);

CREATE INDEX IF NOT EXISTS idx_ecg_date ON ecg(date);

CREATE TABLE IF NOT EXISTS account (
    resource TEXT PRIMARY KEY,
    body TEXT,
    fetched_at TEXT,
    provider TEXT
);

CREATE TABLE IF NOT EXISTS height (
    datetime TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    height_mm INTEGER,
    provider TEXT
);

CREATE TABLE IF NOT EXISTS exercise_routes (
    log_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    tcx TEXT,
    provider TEXT
);

CREATE INDEX IF NOT EXISTS idx_exercise_routes_date ON exercise_routes(date);

CREATE TABLE IF NOT EXISTS sleep_sessions (
    session_id TEXT PRIMARY KEY,
    date TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    record TEXT,
    provider TEXT
);

CREATE INDEX IF NOT EXISTS idx_sleep_sessions_date ON sleep_sessions(date);

CREATE TABLE IF NOT EXISTS authorisation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    missing_scopes TEXT,
    checked_at TEXT
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    synced_at TEXT NOT NULL,
    data_type TEXT NOT NULL,
    status TEXT NOT NULL,
    records_added INTEGER,
    notes TEXT,
    last_date_attempted TEXT
);
"""


# Every column added to a table after google-health-mcp first shipped it, so
# a cache written by an older version gains it on open.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("heart_rate", "calculation_method", "TEXT"),
    ("heart_rate", "data_source", "TEXT"),
    ("spo2", "std_dev", "REAL"),
    ("spo2", "data_source", "TEXT"),
    ("hrv", "entropy", "REAL"),
    ("hrv", "non_rem_hr", "INTEGER"),
    ("hrv", "data_source", "TEXT"),
    ("breathing_rate", "data_source", "TEXT"),
    ("skin_temperature", "nightly_stddev_30d", "REAL"),
    ("skin_temperature", "data_source", "TEXT"),
    ("cardio_fitness", "cardio_fitness_level", "TEXT"),
    ("cardio_fitness", "estimated", "INTEGER"),
    ("cardio_fitness", "vo2_max_covariance", "REAL"),
    ("cardio_fitness", "data_source", "TEXT"),
    ("exercises", "end_time", "TEXT"),
    ("exercises", "start_utc_offset", "TEXT"),
    ("exercises", "end_utc_offset", "TEXT"),
    ("exercises", "active_seconds", "REAL"),
    ("exercises", "notes", "TEXT"),
    ("exercises", "create_time", "TEXT"),
    ("exercises", "update_time", "TEXT"),
    ("exercises", "metrics_summary", "TEXT"),
    ("exercises", "exercise_metadata", "TEXT"),
    ("exercises", "exercise_events", "TEXT"),
    ("exercises", "splits", "TEXT"),
    ("exercises", "split_summaries", "TEXT"),
    ("exercises", "data_source", "TEXT"),
)


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply additive schema migrations to older DBs. Idempotent."""
    for table, column, declaration in MIGRATIONS:
        present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in present:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
    conn.commit()


def default_path() -> Path:
    from ..config import get_paths

    return get_paths().mcp_db


def get_db(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a database connection and ensure the schema exists."""
    path = Path(db_path) if db_path is not None else default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# --- Save helpers ---

# The conflict target for each table an upsert writes. sync_log is append-only.
_UPSERT_KEYS: dict[str, tuple[str, ...]] = {
    "heart_rate": ("date",),
    "activity": ("date",),
    "exercises": ("log_id",),
    "sleep": ("date",),
    "weight": ("date",),
    "spo2": ("date",),
    "hrv": ("date",),
    "azm": ("date",),
    "breathing_rate": ("date",),
    "skin_temperature": ("date",),
    "cardio_fitness": ("date",),
    "ecg": ("reading_id",),
    "account": ("resource",),
    "height": ("datetime",),
    "exercise_routes": ("log_id",),
    "sleep_sessions": ("session_id",),
    "authorisation": ("id",),
}


def _upsert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    """Write `row`, leaving the columns it does not name as they were.

    Omitting a column and passing it as None are different instructions.
    The second writes NULL, which is how a value a provider has withdrawn
    gets cleared.
    """
    keys = _UPSERT_KEYS[table]
    known = {r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")}
    unknown = sorted(set(row) - known)
    if unknown:
        raise ValueError(f"{table} has no column(s) {', '.join(unknown)}")
    absent = [k for k in keys if row.get(k) is None]
    if absent:
        raise ValueError(f"{table} needs {', '.join(absent)} to identify the row")

    columns = list(row)
    updatable = [c for c in columns if c not in keys]
    if not updatable:
        # A row carrying nothing but its key has nothing to store, and inserting
        # it would date-stamp a day whose values never arrived.
        return

    # Nothing reaches this SQL text unchecked: every name is a column the table has.
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)}) "
        f"ON CONFLICT({', '.join(keys)}) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in updatable),
        row,
    )


def save_heart_rate_row(conn: sqlite3.Connection, row: dict):
    """Write the named columns only, leaving `zones` as it was."""
    _upsert(conn, "heart_rate", row)


def save_activity(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "activity", row)


def save_exercise(conn: sqlite3.Connection, log_id: str, row: dict):
    _upsert(conn, "exercises", {"log_id": log_id, **row})


def save_sleep(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "sleep", row)


def save_weight(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "weight", row)


def save_spo2(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "spo2", row)


def save_hrv(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "hrv", row)


def save_azm(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "azm", row)


def save_breathing_rate(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "breathing_rate", row)


def save_skin_temperature(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "skin_temperature", row)


def save_cardio_fitness(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "cardio_fitness", row)


def save_ecg(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "ecg", row)


def save_account(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "account", row)


def save_height(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "height", row)


def save_exercise_route(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "exercise_routes", row)


def save_sleep_session(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "sleep_sessions", row)


def save_authorisation(conn: sqlite3.Connection, row: dict):
    _upsert(conn, "authorisation", row)


def record_missing_scopes(conn: sqlite3.Connection, missing: list[str] | None):
    """Record which requested scopes the grant lacks, by name; None if unknown.

    Written by the host that syncs, which holds the token, and read by every
    host, including offline ones that hold none. Scope names only.
    """
    save_authorisation(
        conn,
        {
            "id": 1,
            "missing_scopes": None if missing is None else json.dumps(missing),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    conn.commit()


def open_readonly(path: Path) -> closing:
    """Open a database with no possibility of creating or altering it.

    The path is percent-encoded because this is a URI, not a filename: an
    unescaped `#` would start a fragment, so `?mode=ro` would land inside it
    and be silently ignored - handing back a writable connection to a
    truncated path. `quote` leaves `/` alone and escapes `#` and `?`.

    Encoding via `os.fsencode` rather than passing the str: a filename holding
    non-UTF-8 bytes arrives as surrogate escapes, which `quote` refuses. Going
    through bytes round-trips those unchanged. `Path.as_uri()` is not usable
    at all here - it rejects relative paths, and GOOGLE_HEALTH_MCP_DB_PATH may be one.
    """
    conn = sqlite3.connect(f"file:{quote(os.fsencode(path))}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return closing(conn)


def recorded_missing_scopes(path: Path | str | None = None) -> list[str] | None:
    """The scopes the last sync found the grant lacking, or None if unknown.

    Read-only and never raising: an absent database, a table from before this
    record existed, or an unreadable value all answer "unknown".
    """
    path = Path(path) if path is not None else default_path()
    try:
        if not path.is_file():
            return None
        with open_readonly(path) as conn:
            row = conn.execute("SELECT missing_scopes FROM authorisation WHERE id = 1").fetchone()
    except (sqlite3.DatabaseError, OSError):
        return None
    value = _decoded(row[0]) if row else None
    if isinstance(value, list) and all(isinstance(name, str) for name in value):
        return value
    return None


def log_sync(
    conn: sqlite3.Connection,
    data_type: str,
    status: str,
    records_added: int = 0,
    notes: str = "",
    last_date_attempted: str | None = None,
):
    conn.execute(
        """INSERT INTO sync_log
            (synced_at, data_type, status, records_added, notes, last_date_attempted)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            data_type,
            status,
            records_added,
            notes,
            last_date_attempted,
        ),
    )
    conn.commit()


# Allowlist mapping data_type names to their table names. Used in get_last_synced_date
# to safely interpolate table names into SQL (SQLite doesn't support parameterised table names).
_DATA_TABLE_MAP: dict[str, str] = {
    "heart_rate": "heart_rate",
    "activity": "activity",
    "exercises": "exercises",
    "sleep": "sleep",
    "weight": "weight",
    "spo2": "spo2",
    "hrv": "hrv",
    "azm": "azm",
    "breathing_rate": "breathing_rate",
    "skin_temperature": "skin_temperature",
    "cardio_fitness": "cardio_fitness",
    "ecg": "ecg",
    "height": "height",
    "exercise_routes": "exercise_routes",
    "sleep_sessions": "sleep_sessions",
}


def get_last_synced_date(conn: sqlite3.Connection, data_type: str) -> str | None:
    """Return the most recent date synced for a data type, from the actual data table."""
    table = _DATA_TABLE_MAP.get(data_type)
    if table is None:
        return None
    # table is from the hardcoded allowlist above - safe to interpolate
    row = conn.execute(f"SELECT MAX(date) AS d FROM {table}").fetchone()
    return row["d"] if row else None


def get_last_attempted_date(conn: sqlite3.Connection, data_type: str) -> str | None:
    """Return the most recent end-date a successful sync attempted to reach.

    Distinct from `get_last_synced_date`, which only sees days that produced
    a row. For sparse-data types (e.g. food_log, skin_temperature) where
    many days legitimately produce no data, the attempted-date is what we
    really want to advance forward on the next sync, otherwise we'd re-query
    every empty day forever.
    """
    row = conn.execute(
        "SELECT MAX(last_date_attempted) AS d FROM sync_log WHERE data_type = ? AND status = 'ok'",
        (data_type,),
    ).fetchone()
    return row["d"] if row and row["d"] else None


def _decoded(value):
    """Stored JSON text as a value, or None if it will not parse.

    A column that cannot be read is unknown, not empty - and one malformed
    row must not cost the caller every other row in the window.
    """
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


def route_ids(conn: sqlite3.Connection) -> set[str]:
    """The exercises whose route is already held."""
    return {row[0] for row in conn.execute("SELECT log_id FROM exercise_routes")}
