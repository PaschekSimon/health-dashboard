"""The exception base for failures this package raises on purpose.

Derived from google-health-mcp 1.8.0 (errors.py), GPL-3.0-or-later.
Modified 2026-10-28: only the base class is kept; the MCP tool errors went
with the tools.

Messages never quote an API response or a resolved path: they end up in
data/logs/sync.log and in the sync status the web app shows.
"""


class GoogleHealthError(Exception):
    """An error raised deliberately, with a message written for the user."""
