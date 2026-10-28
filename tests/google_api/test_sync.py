"""The daily sync loop (health_dashboard.google.sync): windows, the cursor,
failures, and the parallel run writing what the serial one does."""

from datetime import date, timedelta

import pytest

from health_dashboard.google import api, sync
from health_dashboard.google import cache as db


def recording(records=0, calls=None):
    """A handler that notes the window it was given and reports a count."""
    def handler(conn, start, end):
        if calls is not None:
            calls.append((start, end))
        return records
    return handler


def failing(error):
    def handler(conn, start, end):
        raise error
    return handler


@pytest.fixture
def handlers(monkeypatch):
    """Replace the handler map (and the scope lookup) for one test."""
    def use(mapping, missing=()):
        monkeypatch.setattr(sync, "GOOGLE_SYNC_HANDLERS", mapping)
        monkeypatch.setattr(sync, "_missing_scopes", lambda: set(missing))
    return use


def log_rows(dtype):
    conn = db.get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT status, records_added, notes, last_date_attempted FROM sync_log WHERE data_type = ?",
            (dtype,))]
    finally:
        conn.close()


@pytest.mark.parametrize("kwargs,word", [
    ({"since": "nope"}, "Invalid since"),
    ({"since": "2026-03-01", "until": "nope"}, "Invalid until"),
    ({"until": "2026-03-01"}, "requires 'since'"),
    ({"since": "2026-03-10", "until": "2026-03-01"}, "before"),
])
def test_a_bad_window_is_an_error_for_every_type(kwargs, word):
    result = sync.run_sync(["sleep", "hrv"], **kwargs)
    assert set(result) == {"sleep", "hrv"}
    assert all(r["status"] == "error" and word in r["message"] for r in result.values())


def test_a_cold_sync_goes_back_days_and_records_its_end(handlers):
    calls = []
    handlers({"hrv": recording(4, calls)})
    result = sync.run_sync(["hrv"], days=10)
    today = date.today()
    assert calls == [(today - timedelta(days=10), today)]
    assert result["hrv"] == {"status": "ok", "records": 4,
                             "range": f"{today - timedelta(days=10)} to {today}"}
    assert log_rows("hrv")[-1]["last_date_attempted"] == today.isoformat()


def test_a_resumed_sync_looks_back_behind_the_cursor(handlers):
    calls = []
    handlers({"hrv": recording(0, calls)})
    conn = db.get_db()
    db.log_sync(conn, "hrv", "ok", 0, last_date_attempted="2026-03-20")
    conn.close()
    sync.run_sync(["hrv"])
    assert calls[0][0] == date(2026, 3, 20) - timedelta(days=sync.INCREMENTAL_OVERLAP_DAYS)


def test_since_and_until_fetch_exactly_that_window(handlers):
    calls = []
    handlers({"hrv": recording(0, calls)})
    sync.run_sync(["hrv"], since="2026-03-01", until="2026-03-05")
    assert calls == [(date(2026, 3, 1), date(2026, 3, 5))]


def test_a_type_the_grant_cannot_read_is_skipped_not_failed(handlers):
    calls = []
    handlers({"ecg": recording(0, calls)}, missing=["ecg"])
    result = sync.run_sync(["ecg"])
    assert calls == []
    assert result["ecg"]["status"] == "skipped" and "health auth" in result["ecg"]["message"]
    assert log_rows("ecg")[-1]["status"] == "skipped"


@pytest.mark.parametrize("error,status", [
    (api.HealthRateLimitError(60), "rate_limited"),
    (api.HealthAuthError("Run: health auth"), "auth_error"),
    (api.HealthAPIError("API error 500 for sleep list"), "error"),
    (KeyError("secret response content"), "error"),
])
def test_a_failure_is_logged_and_the_other_types_still_run(handlers, error, status):
    handlers({"sleep": failing(error), "hrv": recording(2)})
    result = sync.run_sync(["sleep", "hrv"])
    assert result["sleep"]["status"] == status
    assert result["hrv"]["status"] == "ok"
    assert "secret" not in result["sleep"]["message"]
    assert log_rows("sleep")[-1]["status"] in ("partial", "auth_error", "error")


def test_offline_mode_stops_the_sync(handlers):
    handlers({"sleep": failing(api.HealthOfflineError("offline"))})
    with pytest.raises(api.HealthOfflineError):
        sync.run_sync(["sleep"])


def test_an_unknown_type_is_reported(handlers):
    handlers({})
    assert sync.run_sync(["nope"])["nope"]["status"] == "error"


def _points(data_type, start, end):
    """A day of each type the real handlers read, as Google sends it."""
    day = {"year": 2026, "month": 3, "day": 15}
    return {
        "daily-heart-rate-variability": [{"dailyHeartRateVariability": {
            "date": day, "averageHeartRateVariabilityMilliseconds": 41.5}}],
        "daily-resting-heart-rate": [{"dailyRestingHeartRate": {"date": day, "beatsPerMinute": "58"}}],
        "daily-respiratory-rate": [{"dailyRespiratoryRate": {"date": day, "breathsPerMinute": 14.5}}],
        "sleep": [{"name": "users/me/dataTypes/sleep/dataPoints/1", "sleep": {
            "interval": {"startTime": "2026-03-14T22:30:00Z", "endTime": "2026-03-15T06:30:00Z",
                         "endUtcOffset": "3600s"},
            "summary": {"minutesAsleep": "450", "stagesSummary": [{"type": "DEEP", "minutes": "80"}]}}}],
    }.get(data_type, [])


def _cache_rows():
    conn = db.get_db()
    try:
        return {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")]
                for t in ("hrv", "heart_rate", "breathing_rate", "sleep", "sleep_sessions")}
    finally:
        conn.close()


def test_the_parallel_run_writes_what_the_serial_one_does(monkeypatch, tmp_path):
    """Same answers from Google, one run with a worker per type and one in
    order: the cache ends up with the same rows, and in WAL mode."""
    monkeypatch.setattr(api, "list_google_data_points", _points)
    monkeypatch.setattr(sync, "_missing_scopes", lambda: set())
    types = ["hrv", "heart_rate", "breathing_rate", "sleep", "sleep_sessions"]

    monkeypatch.setenv("GOOGLE_HEALTH_MCP_DB_PATH", str(tmp_path / "serial.db"))
    serial = sync.run_sync(types, since="2026-03-14", until="2026-03-16", workers=1)
    serial_rows = _cache_rows()

    monkeypatch.setenv("GOOGLE_HEALTH_MCP_DB_PATH", str(tmp_path / "parallel.db"))
    parallel = sync.run_sync(types, since="2026-03-14", until="2026-03-16", workers=6)
    assert parallel == serial
    assert all(r["status"] == "ok" and r["records"] == 1 for r in parallel.values())
    assert _cache_rows() == serial_rows
    conn = db.get_db()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_the_command_refuses_in_offline_mode(monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_HEALTH_MCP_OFFLINE", "1")
    assert sync.main([]) == 1
    assert "Offline mode" in capsys.readouterr().err


def test_the_command_without_a_login_says_what_to_do(monkeypatch, capsys):
    monkeypatch.setattr(sync.transport, "install", lambda: None)
    assert sync.main(["--types", "hrv"]) == 1
    out = capsys.readouterr()
    assert "auth_error" in out.out and "health auth" in out.out
