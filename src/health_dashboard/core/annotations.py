"""Annotations: context the watch doesn't have (illness, alcohol, travel, ...),
stored in the `annotations` table of health.db.

    kind        one of annotation_effects.KINDS
    start, end  ISO date or datetime (end optional, inclusive)
    target      what it's about: night:YYYY-MM-DD (wake date), day:YYYY-MM-DD,
                workout:<id>, or NULL
    attrs       JSON object, e.g. {"drinks": 5} or {"intent": "Z2", "cap_bpm": 150}
    status      suggested | confirmed | rejected  (only confirmed ones change numbers)
    source      user | ai | strava | import

Which kinds change the analyses is decided in annotation_effects.EFFECTS.
Pure standard library; the API and the MCP server both use these functions.
"""

import json
import sqlite3
from datetime import date, datetime

from ..analysis import annotation_effects as effects
from ..config import get_paths

KINDS = effects.KINDS
STATUSES = ("suggested", "confirmed", "rejected")
SOURCES = ("user", "ai", "strava", "import")
TARGET_PREFIXES = ("night", "day", "workout")

SCHEMA = """
CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,  -- ids are never reused after a delete
    kind TEXT NOT NULL,
    start TEXT NOT NULL,
    "end" TEXT,
    target TEXT,
    attrs TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (status IN ('suggested', 'confirmed', 'rejected')),
    source TEXT NOT NULL CHECK (source IN ('user', 'ai', 'strava', 'import')),
    confidence REAL,
    raw_text TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS annotations_start ON annotations (start);
CREATE INDEX IF NOT EXISTS annotations_kind ON annotations (kind, status);
"""

COLUMNS = ("id", "kind", "start", "end", "target", "attrs", "status", "source", "confidence",
           "raw_text", "note", "created_at", "updated_at")


class AnnotationError(ValueError):
    pass


def connect(db_path=None):
    """Writable connection to health.db (default: from the environment)."""
    conn = sqlite3.connect(db_path or get_paths().health_db, timeout=30)
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def ensure_schema(conn):
    old = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'annotations'").fetchone()
    # The first version reused deleted ids (no AUTOINCREMENT), and the source
    # for your own entries used to be 'simon'; one rebuild fixes either or both.
    if old and ("AUTOINCREMENT" not in old[0] or "'simon'" in old[0]):
        conn.executescript(
            "BEGIN; ALTER TABLE annotations RENAME TO annotations_old;" + SCHEMA +
            "INSERT INTO annotations SELECT id, kind, start, \"end\", target, attrs, status, "
            "CASE source WHEN 'simon' THEN 'user' ELSE source END, confidence, raw_text, note, "
            "created_at, updated_at FROM annotations_old; "
            "UPDATE sqlite_sequence SET seq = max(seq, coalesce((SELECT seq FROM sqlite_sequence "
            "WHERE name = 'annotations_old'), 0)) WHERE name = 'annotations'; "
            "DROP TABLE annotations_old; COMMIT;")
    conn.executescript(SCHEMA)
    conn.commit()


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _iso(value, field, required=True):
    if value in (None, ""):
        if required:
            raise AnnotationError(f"{field} is required")
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    try:
        if len(value) == 10:
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise AnnotationError(f"{field} must be an ISO date or datetime, got {value!r}") from None
    return value


def _target(value):
    if not value:
        return None
    prefix, _, rest = value.partition(":")
    if prefix not in TARGET_PREFIXES or not rest:
        raise AnnotationError("target must look like night:YYYY-MM-DD, day:YYYY-MM-DD or workout:<id>")
    if prefix in ("night", "day"):
        _iso(rest, "target date")
    return value


def _attrs(value):
    if value is None:
        return "{}"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise AnnotationError("attrs must be a JSON object") from None
    if not isinstance(value, dict):
        raise AnnotationError("attrs must be a JSON object")
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _row(r):
    if r is None:
        return None
    a = dict(zip(COLUMNS, r))
    a["attrs"] = json.loads(a["attrs"] or "{}")
    span = effects.nights_of(a["kind"], a["start"], a["end"], a["target"])
    a["excludes_nights"] = [span[0].isoformat(), span[1].isoformat()] if span else None
    return a


def _select():
    return "SELECT " + ", ".join(f'"{c}"' for c in COLUMNS) + " FROM annotations"


def get(conn, ann_id):
    return _row(conn.execute(_select() + " WHERE id = ?", (ann_id,)).fetchone())


def add(conn, kind, start, end=None, target=None, attrs=None, status="suggested", source="user",
        confidence=None, raw_text=None, note=None):
    """Insert one annotation and return it."""
    if kind not in KINDS:
        raise AnnotationError(f"kind must be one of {', '.join(KINDS)}")
    if status not in STATUSES:
        raise AnnotationError(f"status must be one of {', '.join(STATUSES)}")
    if source not in SOURCES:
        raise AnnotationError(f"source must be one of {', '.join(SOURCES)}")
    start, end = _iso(start, "start"), _iso(end, "end", required=False)
    if end and end[:10] < start[:10]:
        raise AnnotationError("end is before start")
    if confidence is not None and not 0 <= float(confidence) <= 1:
        raise AnnotationError("confidence must be between 0 and 1")
    target = _target(target)
    if target is None and kind in effects.EFFECTS and effects.EFFECTS[kind].excludes == "night":
        # store the wake date it affects, so charts and day lookups land on that night
        target = "night:" + effects.nights_of(kind, start)[0].isoformat()
    now = _now()
    cur = conn.execute(
        'INSERT INTO annotations (kind, start, "end", target, attrs, status, source, confidence, '
        "raw_text, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, start, end, target, _attrs(attrs), status, source, confidence, raw_text, note,
         now, now))
    conn.commit()
    return get(conn, cur.lastrowid)


def list_(conn, date_from=None, date_to=None, kind=None, status=None):
    """Annotations touching [date_from, date_to] (dates, inclusive): their
    start..end span overlaps it, or their night:/day: target falls inside."""
    where, args = [], []
    if date_from or date_to:
        lo, hi = (date_from or "0000-01-01")[:10], (date_to or "9999-12-31")[:10]
        where.append('((substr(start, 1, 10) <= ? AND substr(coalesce("end", start), 1, 10) >= ?) '
                     "OR (target GLOB '[nd][ia]*:*' AND substr(target, instr(target, ':') + 1, 10) "
                     "BETWEEN ? AND ?))")
        args += [hi, lo, lo, hi]
    if kind:
        where.append("kind = ?")
        args.append(kind)
    if status:
        where.append("status = ?")
        args.append(status)
    sql = _select() + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY start, id"
    return [_row(r) for r in conn.execute(sql, args)]


def for_target(conn, target):
    return [_row(r) for r in conn.execute(_select() + " WHERE target = ? ORDER BY start, id", (target,))]


def set_status(conn, ann_id, status):
    """Confirm/reject/re-suggest. Returns (annotation, scores_changed)."""
    if status not in STATUSES:
        raise AnnotationError(f"status must be one of {', '.join(STATUSES)}")
    old = get(conn, ann_id)
    if old is None:
        raise KeyError(ann_id)
    conn.execute("UPDATE annotations SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), ann_id))
    conn.commit()
    changed = effects.affects_scores(old["kind"]) and (old["status"] == "confirmed") != (status == "confirmed")
    return get(conn, ann_id), changed


def delete(conn, ann_id):
    """Delete one annotation. Returns (deleted annotation, scores_changed)."""
    old = get(conn, ann_id)
    if old is None:
        raise KeyError(ann_id)
    conn.execute("DELETE FROM annotations WHERE id = ?", (ann_id,))
    conn.commit()
    return old, effects.affects_scores(old["kind"]) and old["status"] == "confirmed"


def changes_scores(annotation):
    """True if this (new) annotation changes analysis numbers."""
    return annotation["status"] == "confirmed" and effects.affects_scores(annotation["kind"])
