"""Sync daily values, sleep, workouts and the profile into the cache.

Derived from google-health-mcp 1.8.0 (tools/sync_tools.py and the `sync`
command in cli.py), GPL-3.0-or-later.
Modified 2026-10-28: the MCP tool, the auto-sync and live-refresh helpers are
gone, and the types run in parallel, each on its own connection, so the
waiting on Google happens side by side (the cache is put in WAL mode so the
writers don't block each other or the API's readers).

    health daily-sync                      incremental, every type
    health daily-sync --days 90            first sync: how far back to start
    health daily-sync --since 2026-08-19 [--until 2026-09-01] [--types sleep,hrv]
"""

import argparse
import logging
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from . import api, auth, config, transport
from . import cache as db
from .handlers import GOOGLE_SYNC_HANDLERS, HANDLER_SCOPES

logger = logging.getLogger(__name__)

#: How far behind the incremental cursor a resumed sync starts.
#:
#: The property it buys, stated so the value can be re-derived rather than
#: taken on trust: a day D is asked for again on every run from D through
#: D+INCREMENTAL_OVERLAP_DAYS, so the overlap tolerates a publication lag of
#: up to that many days. Measured 19 Aug 2026, the lag is at least one -
#: `daily-*` summaries for that day were absent from a sync run late in the
#: local day and present the next morning - and three is margin on top.
#:
#: A host being off needs none of this: `last_date_attempted` is written only
#: on success, so the cursor does not advance while nothing runs and the first
#: run back covers the whole gap by itself.
#:
#: Bounded rather than open-ended because that cursor exists to stop a sparse
#: type being re-queried from its last reading on every run forever. The
#: upsert makes the repeated days free of consequence; they cost a few requests.
INCREMENTAL_OVERLAP_DAYS = 3

#: Types synced at once. Requests are spaced by the transport anyway, so more
#: only helps while some types wait on slow answers.
WORKERS = 6


def run_sync(
    data_types: list[str],
    days: int = 30,
    since: str | None = None,
    until: str | None = None,
    workers: int = 1,
) -> dict:
    """Sync `data_types` into the cache and return a result per type.

    Args:
        data_types: data type names to sync.
        days: history window for a cold sync (no prior data for that type).
        since: optional "YYYY-MM-DD". When set, every type is backfilled from
            this date, overriding the incremental resume-from-last-sync cursor
            (and `days`). Use to pull history older than what is already cached.
        until: optional "YYYY-MM-DD" inclusive end date; requires `since`.
            Together they re-fetch and upsert exactly the [since, until]
            window - use to repair a hole in the middle of the cache without
            re-pulling everything from the hole to today. Capped at today.
        workers: types synced at once, each on its own connection.
    """
    today = date.today()

    def _all_error(message: str) -> dict:
        return {dtype: {"status": "error", "message": message} for dtype in data_types}

    since_date = None
    if since:
        try:
            since_date = date.fromisoformat(since)
        except ValueError:
            return _all_error(f"Invalid since date '{since}'. Use YYYY-MM-DD.")

    until_date = None
    if until:
        try:
            until_date = date.fromisoformat(until)
        except ValueError:
            return _all_error(f"Invalid until date '{until}'. Use YYYY-MM-DD.")
        if since_date is None:
            return _all_error("'until' requires 'since' to define the backfill window.")
        if until_date < since_date:
            return _all_error(f"'until' ({until}) is before 'since' ({since}).")
        until_date = min(until_date, today)

    conn = db.get_db()  # schema and migrations in place before any thread opens it
    try:
        if workers > 1:
            conn.execute("PRAGMA journal_mode=WAL")
        missing = _missing_scopes()
        path = conn.execute("PRAGMA database_list").fetchone()["file"]

        def one(dtype):
            own = sqlite3.connect(path, timeout=120)
            own.row_factory = sqlite3.Row
            try:
                return _sync_type(own, dtype, missing, since_date, until_date, today, days)
            finally:
                own.close()

        if workers > 1:
            with ThreadPoolExecutor(workers) as pool:
                results = dict(zip(data_types, pool.map(one, data_types)))
        else:
            results = {t: _sync_type(conn, t, missing, since_date, until_date, today, days)
                       for t in data_types}

        # After the types rather than before: a refresh during them is what
        # brings the token's record of its scopes up to date. An unreadable
        # token leaves the record alone.
        try:
            found = config.missing_scopes(auth.granted_scopes())
            if found is not None:
                db.record_missing_scopes(conn, found)
        except Exception:
            logger.error("Could not record the grant's missing scopes")
        return results
    finally:
        conn.close()


def _missing_scopes() -> set[str]:
    """Scopes the grant lacks. A token from before scopes were recorded says
    nothing until its next refresh, so one is asked for now; otherwise the
    first sync fetches what the grant lacks and fails on the 403. If it cannot
    be had, unknown is treated as held and everything is fetched."""
    if not config.offline() and auth.granted_scopes() is None:
        try:
            auth.refresh_google_token(force=True)
        except (auth.TokenRefused, auth.RefreshNetworkError):
            pass
    return set(config.missing_scopes(auth.granted_scopes()) or ())


def _start_date(conn, dtype, since_date, today, days) -> date:
    if since_date is not None:
        # Explicit backfill: ignore the incremental cursor entirely.
        return since_date
    # The later of (most-recent row in table) and (most-recent successful
    # sync's end-date). The second matters for sparse types: once nothing new
    # is recorded, MAX(date) freezes and every day since would be asked for
    # again on each run.
    candidates = [
        d
        for d in (db.get_last_synced_date(conn, dtype), db.get_last_attempted_date(conn, dtype))
        if d
    ]
    if not candidates:
        return today - timedelta(days=days)
    # Behind the cursor, not at it. Google publishes a day's `daily-*` summary
    # after the day has ended, so a sync running late in the local day gets
    # nothing for yesterday, records that it attempted through today, and
    # resuming from that cursor would never ask again. Never subtract past
    # date.min: a cursor within the overlap of it - which only a corrupt
    # sync_log produces - would raise OverflowError instead of syncing.
    cursor = date.fromisoformat(max(candidates))
    return cursor - timedelta(days=min(INCREMENTAL_OVERLAP_DAYS, (cursor - date.min).days))


def _sync_type(conn, dtype, missing, since_date, until_date, today, days) -> dict:
    try:
        handler = GOOGLE_SYNC_HANDLERS.get(dtype)
        if handler is None:
            return {"status": "error", "message": f"Unknown type: {dtype}"}
        start_date = _start_date(conn, dtype, since_date, today, days)
        end_date = until_date if until_date is not None else today

        lacking = [s for s in HANDLER_SCOPES.get(dtype, ()) if s in missing]
        if lacking:
            # Neither a failure nor a success: the sync exits 0, and the
            # missing permission is printed once at the end.
            message = f"permission not granted ({', '.join(lacking)}); run health auth"
            db.log_sync(conn, dtype, "skipped", notes=message)
            return {"status": "skipped", "message": message}

        count = handler(conn, start_date, end_date)
        db.log_sync(conn, dtype, "ok", count, last_date_attempted=end_date.isoformat())
        return {"status": "ok", "records": count, "range": f"{start_date} to {end_date}"}

    except api.HealthRateLimitError as e:
        db.log_sync(conn, dtype, "partial", notes="rate limited")
        return {"status": "rate_limited", "message": str(e)}
    except api.HealthAuthError as e:
        db.log_sync(conn, dtype, "auth_error", notes=str(e))
        return {"status": "auth_error", "message": str(e)}
    except api.HealthAPIError as e:
        db.log_sync(conn, dtype, "error", notes=str(e))
        return {"status": "error", "message": str(e)}
    except api.HealthOfflineError:
        raise
    except Exception as e:
        # Type only: these paths carry API responses.
        try:
            conn.rollback()
            db.log_sync(conn, dtype, "error", notes=f"unexpected {type(e).__name__}")
        except Exception:
            logger.error("Could not record a failed sync of %s", dtype)
        return {"status": "error", "message": "Unexpected error during sync."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sync Google's daily values, sleep and workouts into the cache.")
    ap.add_argument("--days", type=int, default=30, help="days of history for a first sync (default 30)")
    ap.add_argument("--types", default="all",
                    help="comma-separated: all, " + ", ".join(GOOGLE_SYNC_HANDLERS))
    ap.add_argument("--since", help="backfill from this date (YYYY-MM-DD), ignoring the cursor and --days")
    ap.add_argument("--until", help="inclusive end date for a --since backfill")
    ap.add_argument("--workers", type=int, default=WORKERS, help=f"types synced at once (default {WORKERS})")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s", stream=sys.stderr)

    if config.offline():
        print("Offline mode is on (GOOGLE_HEALTH_MCP_OFFLINE); not syncing.", file=sys.stderr)
        return 1
    types = [t.strip() for t in args.types.split(",")]
    if "all" in types:
        types = list(GOOGLE_SYNC_HANDLERS)

    transport.install()
    print(f"Syncing: {', '.join(types)}", flush=True)
    try:
        results = run_sync(types, args.days, since=args.since, until=args.until, workers=args.workers)
    except auth.TokenRefused as e:  # no client file or token yet
        print(e, file=sys.stderr)
        return 1
    for dtype, result in results.items():
        if result.get("status") == "ok":
            print(f"  {dtype}: {result.get('records', 0)} records ({result.get('range', '')})")
        else:
            print(f"  {dtype}: {result.get('status', '?')} - {result.get('message', '')}")

    if missing := db.recorded_missing_scopes():
        print(f"Google has not granted: {', '.join(missing)}. Types needing them are skipped; "
              "run health auth and tick every permission.", file=sys.stderr)
    # rate_limited is transient and heals on the next run, so it isn't a failure.
    failed = [d for d, r in results.items() if r.get("status") in ("auth_error", "error")]
    if failed:
        print(f"Sync failed for: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
