"""Telling a nap from the night it follows.

Google files every sleep session under the local date it ended, so an
afternoon nap shares its date with the night before it. The app keeps them
apart: a night is its main sleep, a nap is shown on the day it was taken and
lowers the next night's sleep need (scores/recovery.py). A nap the night
before discharges sleep pressure the next night does not have to (Vienne 2012,
doi:10.5665/sleep.1992; Centofanti 2015, doi:10.1016/j.aap.2015.11.009).
"""

from datetime import datetime, timedelta


def local_start(payload: dict) -> datetime | None:
    """The session's start on the user's own clock, or None if unreadable."""
    interval = payload.get("interval") or {}
    started, offset = interval.get("startTime"), interval.get("startUtcOffset") or "0s"
    try:
        moment = datetime.fromisoformat(started.replace("Z", "+00:00"))
        return moment + timedelta(seconds=int(str(offset).rstrip("s")))
    except (AttributeError, ValueError):
        return None


def is_nap(payload: dict) -> bool:
    """A session that is not part of the night: Google marks it a nap, or it
    is not the main sleep and starts between 10:00 and 20:00 local time.

    Both conditions, so a wake-and-resume piece at 03:00 stays in its night
    and a main sleep after a night shift stays a night.
    """
    metadata = payload.get("metadata") or {}
    if metadata.get("nap"):
        return True
    if metadata.get("mainSleep"):
        return False
    started = local_start(payload)
    return started is not None and 10 <= started.hour < 20
