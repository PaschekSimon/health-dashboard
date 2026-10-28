"""`health doctor`, and the health-app MCP starting a sync when the data is stale."""

import json
from datetime import datetime, timedelta

from health_dashboard import mcp_server
from health_dashboard.core import jobs
from health_dashboard.google import auth, doctor
from health_dashboard.google import cache as db


def test_doctor_without_a_client_file_says_where_it_goes(capsys):
    assert doctor.main([]) == 1
    out = capsys.readouterr().out
    assert out.startswith("FAIL  client file") and "google_client.json" in out


def test_doctor_reports_each_types_last_sync(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_HEALTH_MCP_CONFIG_DIR", str(tmp_path))
    (tmp_path / "google_client.json").write_text(json.dumps({"installed": {"client_id": "id"}}))
    scopes = " ".join(f"{auth.config.GOOGLE_SCOPE_PREFIX}{s}.readonly" for s in auth.config.GOOGLE_SCOPE_READERS)
    (tmp_path / "google_tokens.json").write_text(json.dumps({"access_token": "a", "refresh_token": "r",
                                                             "expires_at": 9e12, "scope": scopes}))
    monkeypatch.setattr(auth, "refresh_google_token", lambda force=False: "a")  # Google says yes
    conn = db.get_db()
    db.log_sync(conn, "sleep", "ok", 3, last_date_attempted="2026-03-15")
    db.log_sync(conn, "hrv", "error", notes="API error 500 for daily-heart-rate-variability list")
    conn.close()
    assert doctor.main([]) == 1
    out = capsys.readouterr().out
    assert "OK    login" in out and "OK    permissions" in out
    assert "OK    sync sleep" in out
    assert "FAIL  sync hrv" in out and "API error 500" in out
    assert "WARN  sync ecg                 never synced" in out


def test_a_question_starts_a_sync_only_when_the_data_is_stale(monkeypatch):
    started = []
    monkeypatch.setattr(jobs, "start_sync", lambda reason, origin: started.append((reason, origin)) or {"id": 1})
    monkeypatch.setattr(jobs, "lock_held", lambda paths: False)
    monkeypatch.setattr(jobs, "connect", lambda paths=None: _Conn())

    for last, expected in ((None, 1), (datetime.now() - timedelta(hours=4), 2),
                           (datetime.now() - timedelta(minutes=30), 2)):
        monkeypatch.setattr(jobs, "last_successful_sync", lambda conn, last=last: last)
        mcp_server._sync_if_stale()
        assert len(started) == expected
    assert started[0] == ("open", "mcp")


def test_a_failing_sync_start_never_fails_the_question(monkeypatch):
    def broken(paths=None):
        raise OSError("disk gone")
    monkeypatch.setattr(jobs, "connect", broken)
    mcp_server._sync_if_stale()


class _Conn:
    def close(self):
        pass
