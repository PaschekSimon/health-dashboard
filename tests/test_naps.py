"""Which sleep sessions are naps rather than part of the night."""

from health_dashboard.naps import is_nap, local_start


def _payload(start, offset="3600s", **metadata):
    return {"interval": {"startTime": start, "startUtcOffset": offset}, "metadata": metadata}


def test_the_main_sleep_is_never_a_nap():
    assert not is_nap(_payload("2026-10-30T12:57:00Z", mainSleep=True))


def test_googles_nap_flag_wins():
    assert is_nap(_payload("2026-10-30T01:00:00Z", nap=True))


def test_an_unflagged_session_is_a_nap_by_its_local_start():
    assert is_nap(_payload("2026-10-30T12:57:00Z"))        # 13:57 local
    assert not is_nap(_payload("2026-10-30T02:00:00Z"))    # 03:00 local, a piece of the night
    assert not is_nap(_payload("2026-10-30T19:30:00Z"))    # 20:30 local, an early night


def test_the_offset_decides_the_hour():
    assert is_nap(_payload("2026-10-30T08:30:00Z", offset="7200s"))   # 10:30 local
    assert not is_nap(_payload("2026-10-30T08:30:00Z", offset="0s"))


def test_an_unreadable_start_is_not_a_nap():
    assert local_start({"interval": {}}) is None
    assert not is_nap({"interval": {"startTime": "garbage"}})
