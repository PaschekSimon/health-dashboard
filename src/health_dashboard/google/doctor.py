"""Check the Google login and the cache, and say what to fix.

    health doctor

A short stand-in for google-health-mcp's `doctor`: the client file, the
token (and whether Google still accepts it), the granted scopes, and the
last sync of each cached type. Exits 1 if something needs fixing.
"""

import json
import sqlite3
import sys
import time

from . import auth, config
from . import cache as db
from .handlers import GOOGLE_SYNC_HANDLERS


def checks():
    """Yield (status, what, detail); status is ok, warn or fail."""
    try:
        auth._load_google_client()
        yield "ok", "client file", str(config.client_path())
    except auth.TokenRefused as e:
        yield "fail", "client file", str(e)
        return

    try:
        tokens = json.loads(config.tokens_path().read_text())
    except (OSError, ValueError):
        yield "fail", "login", f"no readable {config.tokens_path().name}; run: health auth"
        return
    if not config.offline():
        try:
            auth.refresh_google_token(force=True)
            yield "ok", "login", "Google accepts the token"
        except auth.TokenRefused as e:
            yield "fail", "login", str(e)
        except auth.RefreshNetworkError:
            yield "warn", "login", "could not reach Google to check the token"
    expires = tokens.get("refresh_token_expires_at")
    if isinstance(expires, (int, float)):
        days = (expires - time.time()) / 86400
        yield "warn", "login", (f"refresh token expires in {days:.0f} days: the OAuth app is still in "
                                "Testing; publish it, then run health auth")

    missing = config.missing_scopes(auth.granted_scopes())
    if missing is None:
        yield "warn", "permissions", "unknown until the next sync refreshes the token"
    elif missing:
        yield "fail", "permissions", f"not granted: {', '.join(missing)}; run health auth and tick every box"
    else:
        yield "ok", "permissions", "all granted"

    path = db.default_path()
    if not path.is_file():
        yield "warn", "cache", f"{path} doesn't exist yet; run: health daily-sync --days 90"
        return
    try:
        with db.open_readonly(path) as conn:
            for dtype in GOOGLE_SYNC_HANDLERS:
                row = conn.execute(
                    "SELECT synced_at, status, notes FROM sync_log WHERE data_type = ? "
                    "ORDER BY id DESC LIMIT 1", (dtype,)).fetchone()
                if row is None:
                    yield "warn", f"sync {dtype}", "never synced"
                elif row["status"] in ("ok", "skipped", "partial"):
                    note = f" ({row['notes']})" if row["notes"] else ""
                    yield "ok" if row["status"] == "ok" else "warn", f"sync {dtype}", \
                        f"{row['status']} at {row['synced_at'][:16]}{note}"
                else:
                    yield "fail", f"sync {dtype}", f"{row['status']} at {row['synced_at'][:16]}: {row['notes']}"
    except sqlite3.Error as e:
        yield "fail", "cache", f"unreadable: {e}"


def main(argv=None) -> int:
    worst = "ok"
    for status, what, detail in checks():
        print(f"{status.upper():5} {what:24} {detail}")
        if status == "fail" or (status == "warn" and worst == "ok"):
            worst = status
    if config.offline():
        print("note  offline mode is on (GOOGLE_HEALTH_MCP_OFFLINE): Google was not asked")
    return 1 if worst == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
