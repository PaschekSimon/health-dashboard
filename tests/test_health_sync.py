"""The intraday sync fetches every window in parallel but must store exactly
what the old one-window-at-a-time loop stored: every window, in order, with
sync_state only ever covering windows that made it in. No network here: the
fetch is replaced and the transport is fed fake responses."""

import gzip
import io
import sqlite3
import threading
import time
import urllib.request
from datetime import date, timedelta
from email.message import Message

import pytest

from health_dashboard.sync import health_sync as hs

TODAY = date(2026, 10, 28)
FIRST = date(2026, 10, 10)


def hr_point(day: date, minute: int, bpm: int) -> dict:
    t = f"{day.isoformat()}T00:{minute:02d}:00Z"
    return {"heartRate": {"sampleTime": {"physicalTime": t, "utcOffset": "3600s"}, "beatsPerMinute": bpm},
            "dataSource": {"device": {"displayName": "Pixel Watch 3"}}}


def steps_point(day: date, count: int) -> dict:
    iv = {"startTime": f"{day.isoformat()}T08:00:00Z", "endTime": f"{day.isoformat()}T08:01:00Z",
          "startUtcOffset": "3600s"}
    return {"steps": {"interval": iv, "count": str(count)}, "dataSource": {"device": {"displayName": "Pixel Watch 3"}}}


def fake_google(fail=None, delay=0.0):
    """A fetch answering two heart-rate points and one steps point per day,
    raising for (type, first day) in `fail`. Records every call."""
    calls = []

    def fetch(data_type, first, last):
        calls.append((data_type, first, last))
        time.sleep(delay)
        if fail and (data_type, first) in fail:
            raise RuntimeError(f"boom {data_type} {first}")
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        if data_type == "heart-rate":
            return [hr_point(d, m, 60 + m) for d in days for m in (0, 1)]
        return [steps_point(d, 100 + d.day) for d in days]
    return fetch, calls


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(hs.SCHEMA)
    yield c
    c.close()


def synced(conn):
    return dict(conn.execute("SELECT data_type, synced_through FROM sync_state"))


def test_windows_cover_every_day_once():
    for days in (1, 7, 30):
        spans = list(hs.windows(FIRST, TODAY, days))
        covered = [s + timedelta(days=i) for s, e in spans for i in range((e - s).days + 1)]
        assert covered == [FIRST + timedelta(days=i) for i in range((TODAY - FIRST).days + 1)]
        assert all((e - s).days < days for s, e in spans)
    assert list(hs.windows(TODAY + timedelta(days=1), TODAY, 1)) == []


def test_parallel_sync_stores_every_window(conn, monkeypatch):
    fetch, calls = fake_google(delay=0.01)
    monkeypatch.setattr(hs, "fetch", fetch)
    out = list(hs.sync_types(conn, ["heart-rate", "steps"], None, TODAY, FIRST, workers=4))

    ndays = (TODAY - FIRST).days + 1
    assert out == [("heart-rate", 2 * ndays, None), ("steps", ndays, None)]
    assert conn.execute("SELECT count(*) FROM hr_samples").fetchone()[0] == 2 * ndays
    assert conn.execute("SELECT count(*), sum(steps) FROM steps_minute").fetchone() == (
        ndays, sum(100 + (FIRST + timedelta(days=i)).day for i in range(ndays)))
    assert synced(conn) == {"heart-rate": TODAY.isoformat(), "steps": TODAY.isoformat()}
    # Heart rate a day per request, steps a week.
    assert sum(c[0] == "heart-rate" for c in calls) == ndays
    assert sum(c[0] == "steps" for c in calls) == len(list(hs.windows(FIRST, TODAY, 7)))


def test_resume_rereads_the_overlap(conn, monkeypatch):
    conn.execute("INSERT INTO sync_state VALUES ('heart-rate', ?, 'x')", ((TODAY - timedelta(days=1)).isoformat(),))
    fetch, calls = fake_google()
    monkeypatch.setattr(hs, "fetch", fetch)
    list(hs.sync_types(conn, ["heart-rate"], None, TODAY, FIRST))
    assert sorted(c[1] for c in calls) == [TODAY - timedelta(days=d) for d in (3, 2, 1, 0)]


def test_never_reads_before_the_first_watch_day(conn, monkeypatch):
    fetch, calls = fake_google()
    monkeypatch.setattr(hs, "fetch", fetch)
    list(hs.sync_types(conn, ["heart-rate"], FIRST - timedelta(days=30), TODAY, FIRST))
    assert min(c[1] for c in calls) == FIRST


def test_a_failed_window_keeps_the_ones_before_it(conn, monkeypatch):
    bad = FIRST + timedelta(days=5)
    fetch, _ = fake_google(fail={("heart-rate", bad)}, delay=0.01)
    monkeypatch.setattr(hs, "fetch", fetch)
    out = list(hs.sync_types(conn, ["heart-rate", "steps"], None, TODAY, FIRST, workers=4))

    assert out[0][0] == "heart-rate" and out[0][1] is None and "boom" in str(out[0][2])
    assert out[1] == ("steps", (TODAY - FIRST).days + 1, None)  # the other type still syncs
    # Only the days before the failed one are stored, and sync_state stops there,
    # so the next run picks up from the failed day.
    days = {r[0] for r in conn.execute("SELECT date(t, 'unixepoch') FROM hr_samples")}
    assert days == {(FIRST + timedelta(days=i)).isoformat() for i in range(5)}
    assert synced(conn)["heart-rate"] == (bad - timedelta(days=1)).isoformat()


def test_rate_limit_is_waited_out(monkeypatch):
    tries = []

    def read(data_type, start, end):
        tries.append(start)
        if len(tries) < 3:
            raise hs.HealthRateLimitError(900)
        return ["ok"]
    monkeypatch.setattr(hs, "list_google_data_points", read)
    monkeypatch.setattr(hs, "RATE_LIMIT_WAITS", (0, 0, 0))
    assert hs.fetch("heart-rate", TODAY, TODAY) == ["ok"]
    assert len(tries) == 3

    tries.clear()
    monkeypatch.setattr(hs, "RATE_LIMIT_WAITS", (0,))
    with pytest.raises(hs.HealthRateLimitError):
        hs.fetch("heart-rate", TODAY, TODAY)


class FakeResponse(io.BytesIO):
    def __init__(self, body, encoding=None, code=200):
        super().__init__(body)
        self.headers = Message()
        if encoding:
            self.headers["Content-Encoding"] = encoding
        self.url, self.code, self.msg = "https://example.test/x", code, "OK"


def test_transport_unzips_and_asks_for_gzip():
    transport = hs.GoogleTransport(gap=0)
    req = transport.https_request(urllib.request.Request("https://example.test/x"))
    assert req.get_header("Accept-encoding") == "gzip"
    assert "gzip" in req.get_header("User-agent")

    body = b'{"dataPoints": []}'
    resp = transport.https_response(req, FakeResponse(gzip.compress(body), "gzip", code=404))
    assert (resp.read(), resp.code, resp.msg) == (body, 404, "OK")
    plain = FakeResponse(body)
    assert transport.https_response(req, plain) is plain


def test_transport_spaces_requests_across_threads():
    transport = hs.GoogleTransport(gap=0.05)
    starts = []

    def go():
        transport.https_request(urllib.request.Request("https://example.test/x"))
        starts.append(time.monotonic())
    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    starts.sort()
    assert starts[-1] - starts[0] >= 5 * 0.05 * 0.9
