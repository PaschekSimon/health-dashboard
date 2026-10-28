"""The read helpers google-health-mcp 1.8.0 had in db.py (GPL-3.0-or-later),
kept for the tests: the dashboard reads the cache with its own SQL."""

import json
import sqlite3
from collections.abc import Sequence


def _rows_to_dicts(rows) -> list[dict]:
    result = []
    for r in rows:
        d = dict(r)
        # Decode JSON blobs
        if "zones" in d and d["zones"]:
            try:
                d["zones"] = json.loads(d["zones"])
            except (json.JSONDecodeError, TypeError):
                pass
        for column in _JSON_COLUMNS & d.keys():
            d[column] = _decoded(d[column])
        result.append(d)
    return result


#: Columns holding an object or array as Google sent it, decoded on read.
_JSON_COLUMNS = frozenset(
    {
        "data_source",
        "metrics_summary",
        "exercise_metadata",
        "exercise_events",
        "splits",
        "split_summaries",
    }
)


def query_heart_rate(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM heart_rate WHERE date >= ? AND date <= ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_activity(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM activity WHERE date >= ? AND date <= ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)


def exercise_names(conn: sqlite3.Connection) -> list[str]:
    """Every workout name held, in the spelling it was stored under."""
    rows = conn.execute(
        "SELECT DISTINCT name FROM exercises WHERE name IS NOT NULL ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def query_exercises(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: str,
    names: str | Sequence[str] | None = None,
) -> list[dict]:
    """Workouts in a window, narrowed to named ones when given some.

    Names match exactly, and which stored names a caller's filter means is
    decided above this layer: SQL's `LOWER` folds ASCII alone, so a name
    outside it would pass a Python fold here and match nothing.
    """
    sql = "SELECT * FROM exercises WHERE date >= ? AND date <= ?"
    params: list = [start_date, end_date]
    if names is not None:
        wanted = [names] if isinstance(names, str) else list(names)
        # No name asked for matches no row. Reading an empty sequence as "no
        # filter" would answer a narrowed question with the whole window.
        sql += f" AND name IN ({','.join('?' * len(wanted))})" if wanted else " AND 1 = 0"
        params.extend(wanted)
    rows = conn.execute(sql + " ORDER BY date, start_time", params).fetchall()
    return _rows_to_dicts(rows)


def query_sleep(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM sleep WHERE date >= ? AND date <= ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_weight(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM weight WHERE date >= ? AND date <= ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_spo2(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM spo2 WHERE date >= ? AND date <= ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_hrv(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM hrv WHERE date >= ? AND date <= ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_azm(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM azm WHERE date >= ? AND date <= ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_breathing_rate(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM breathing_rate WHERE date >= ? AND date <= ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_skin_temperature(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM skin_temperature WHERE date >= ? AND date <= ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_core_temperature(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM core_temperature WHERE date >= ? AND date <= ? "
        "ORDER BY datetime, temp_celsius",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_cardio_fitness(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM cardio_fitness WHERE date >= ? AND date <= ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)


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


def query_ecg(
    conn: sqlite3.Connection, start_date: str, end_date: str, include_waveform: bool = False
) -> list[dict]:
    """Readings in a date range, the trace counted rather than carried.

    A reading is thousands of voltages and most callers want the
    classification and the average rate, so the sample count is taken in SQL
    and the column itself is decoded only when asked for. The length is
    guarded twice: a stored trace that will not parse fails `json_valid`
    rather than the query, and one that parses to something other than an
    array fails `json_type` - a JSON object is well-formed and has no length,
    so it would otherwise count as a reading that recorded silence. Null
    means no trace or an unreadable one; only an empty array counts 0.
    """
    rows = conn.execute(
        "SELECT *, CASE WHEN json_valid(waveform) AND json_type(waveform) = 'array' "
        "THEN json_array_length(waveform) END "
        "AS waveform_samples FROM ecg WHERE date >= ? AND date <= ? ORDER BY date, start_time",
        (start_date, end_date),
    ).fetchall()

    readings = []
    for row in rows:
        reading = dict(row)
        stored = reading.pop("waveform")
        if include_waveform:
            reading["waveform"] = _decoded(stored)
        readings.append(reading)
    return readings


def query_irn(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM irn WHERE date >= ? AND date <= ? ORDER BY date, start_time",
        (start_date, end_date),
    ).fetchall()
    alerts = []
    for row in rows:
        alert = dict(row)
        alert["alert_windows"] = _decoded(alert["alert_windows"])
        alerts.append(alert)
    return alerts


def query_account(conn: sqlite3.Connection) -> dict[str, dict]:
    """Each stored account record by resource, its JSON decoded."""
    return {
        row["resource"]: {"body": _decoded(row["body"]), "fetched_at": row["fetched_at"]}
        for row in conn.execute("SELECT resource, body, fetched_at FROM account")
    }


def query_height(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM height WHERE date >= ? AND date <= ? ORDER BY datetime",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_exercise_route(conn: sqlite3.Connection, log_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM exercise_routes WHERE log_id = ?", (log_id,)).fetchone()
    return dict(row) if row else None


def route_ids(conn: sqlite3.Connection) -> set[str]:
    """The exercises whose route is already held."""
    return {row[0] for row in conn.execute("SELECT log_id FROM exercise_routes")}


def query_sleep_sessions(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    """Each session in a date range, its record decoded."""
    rows = conn.execute(
        "SELECT * FROM sleep_sessions WHERE date >= ? AND date <= ? ORDER BY date, start_time",
        (start_date, end_date),
    ).fetchall()
    sessions = []
    for row in rows:
        session = dict(row)
        session["record"] = _decoded(session["record"])
        sessions.append(session)
    return sessions


def _query_readings(conn: sqlite3.Connection, table: str, start_date: str, end_date: str):
    """Each reading in a date range from one of the reading tables, record decoded."""
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE date >= ? AND date <= ? ORDER BY datetime",
        (start_date, end_date),
    ).fetchall()
    readings = []
    for row in rows:
        reading = dict(row)
        reading["record"] = _decoded(reading["record"])
        readings.append(reading)
    return readings


def query_weight_readings(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    return _query_readings(conn, "weight_readings", start_date, end_date)


def query_body_fat_readings(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    return _query_readings(conn, "body_fat_readings", start_date, end_date)


def query_food_log(conn: sqlite3.Connection, start_date: str, end_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM food_log WHERE date >= ? AND date <= ? ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return _rows_to_dicts(rows)
