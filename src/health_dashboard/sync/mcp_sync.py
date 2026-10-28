"""`google-health-mcp sync`, with its requests fetched in parallel first.

The MCP's own sync (daily values, sleep, workouts into its cache) sends about
30 small requests one after another, each type reading and writing the cache
between them. Run here it goes in two passes:

1. Prefetch: every type syncs on its own thread into an in-memory copy of the
   cache, and each answer Google gives is kept, keyed by the exact request.
2. The real run: the MCP's unchanged `sync` command on the real cache, one
   type after another as always. A request asked before is answered from
   pass 1; anything else (a request that failed, or one that depends on what
   an earlier type just wrote) goes to Google as usual.

So the cache gets exactly what the plain command would write, only the
waiting on Google happens side by side. Requests use health_sync's transport:
gzip, and spaced to stay inside the per-user quota.

    python -m health_dashboard mcp-sync
"""

import copy
import json
import sqlite3
import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import google_health_mcp.api as api
from google_health_mcp import config, db
from google_health_mcp.tools import sync_tools

from .health_sync import WORKERS, GoogleTransport


class Answers:
    """Google's answers by request, each handed out once, as a copy."""

    def __init__(self):
        self._lock = threading.Lock()
        self._kept: dict[tuple, list[dict]] = {}

    @staticmethod
    def key(path, params, body):
        return path, json.dumps(params, sort_keys=True), json.dumps(body, sort_keys=True)

    def keep(self, key, answer):
        with self._lock:
            self._kept.setdefault(key, []).append(copy.deepcopy(answer))

    def take(self, key):
        with self._lock:
            kept = self._kept.get(key)
            return kept.pop(0) if kept else None


def recording(get, answers):
    def google_get(path, params, body=None):
        answer = get(path, params, body)
        answers.keep(Answers.key(path, params, body), answer)
        return answer
    return google_get


def replaying(get, answers):
    def google_get(path, params, body=None):
        kept = answers.take(Answers.key(path, params, body))
        return kept if kept is not None else get(path, params, body)
    return google_get


def _prefetch_one(data_type):
    """Sync one type into a private in-memory copy of the cache (thrown away)."""
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    try:
        source = sqlite3.connect(db.DB_PATH, timeout=30)
        try:
            source.backup(scratch)
        finally:
            source.close()
        # Same call and defaults as `google-health-mcp sync` makes (30 days on a first sync).
        sync_tools._run_sync_types(scratch, [data_type], {}, None, None, date.today(), 30,
                                   sync_tools.GOOGLE_SYNC_HANDLERS)
    finally:
        scratch.close()


def prefetch(types, answers, workers=WORKERS):
    """Pass 1: run every type's sync on its own thread against a copy of the
    cache, keeping every answer. Failures here only mean pass 2 asks again."""
    db.get_db().close()  # schema and migrations in place before copying
    get = api.google_get
    api.google_get = recording(get, answers)
    try:
        with ThreadPoolExecutor(workers) as pool:
            for future in [pool.submit(_prefetch_one, t) for t in types]:
                try:
                    future.result()
                except Exception:
                    pass
    finally:
        api.google_get = get


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        sys.exit("mcp-sync takes no arguments; use google-health-mcp sync for backfills")
    urllib.request.install_opener(urllib.request.build_opener(GoogleTransport()))
    answers = Answers()
    if not config.OFFLINE_MODE:
        prefetch(list(sync_tools.GOOGLE_SYNC_HANDLERS), answers)

    from google_health_mcp import cli
    api.google_get = replaying(api.google_get, answers)
    sys.argv = ["google-health-mcp", "sync"]
    try:
        cli.main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
