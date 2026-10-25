"""Where things live. The backend sits in Health/backend, the analysis
scripts in Health/scripts, and all data (health.db, logs, the sync lock) in
Health/data."""

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
HEALTH = BACKEND.parent
SCRIPTS = HEALTH / "scripts"
DATA = HEALTH / "data"
LOGS = DATA / "logs"
HEALTH_DB = DATA / "health.db"
MCP_DB = Path(os.path.expanduser("~/.local/share/google-health-mcp/google_health.db"))

# The google-health MCP's venv: it holds the Google login, so health_sync.py
# and the analysis scripts run with its Python (never install anything there).
MCP_SCRIPTS = Path(os.environ["APPDATA"]) / "uv" / "tools" / "google-health-mcp" / "Scripts"
MCP_PYTHON = MCP_SCRIPTS / "python.exe"
MCP_CLI = MCP_SCRIPTS / "google-health-mcp.exe"

# Shared with sync_on_access.py (SessionStart hook + MCP auto-sync).
SYNC_LOCK = DATA / ".sync.lock"
LAST_SYNC = DATA / ".last_sync"
SYNC_LOG = LOGS / "sync.log"

# annotation_effects.py lives next to baseline.py, which imports it too.
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))
