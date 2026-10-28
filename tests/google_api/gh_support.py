"""Test data shared by the google_api tests (from google-health-mcp 1.8.0, GPL-3.0-or-later)."""

_DAY = {"year": 2026, "month": 3, "day": 15}
_CIVIL = {"date": _DAY, "time": {}}


def dated_but_empty(field: str) -> dict:
    """A point of the given type carrying an identity and a date, and no value.

    One point serves every read path and filter family: a rollup reads
    `civilStartTime`, a daily list point its payload's `date`, a sample its
    `sampleTime`, a session its `interval`, and the episode tables their
    point-level `name`. Carrying all of them means each handler reaches its
    write rather than skipping the point for want of an identity, which is
    what makes a negative assertion about what gets written mean anything.

    Unlike Google in one way worth knowing when reading a green result: a real
    rollup of a true-zero type carries an explicit 0, so `sync_activity` fills
    a row here rather than writing nothing.
    """
    return {
        "name": f"point/{field}",
        "civilStartTime": _CIVIL,
        field: {
            "date": _DAY,
            "sampleTime": {"civilTime": _CIVIL},
            "interval": {
                "startTime": "2026-03-14T23:00:00Z",
                "endTime": "2026-03-15T07:00:00Z",
                "civilStartTime": _CIVIL,
                "civilEndTime": _CIVIL,
            },
        },
    }
