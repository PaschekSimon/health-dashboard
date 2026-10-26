"""Where things live. Nothing here is read at import time: call `get_paths()`
(it reads the environment each time) or build a `Paths` yourself, e.g. in
tests.

Defaults fit a checkout of the repo: data in <repo>/data, the web app in
<repo>/frontend. Environment variables override them:

    HEALTH_DATA_DIR             health.db, logs, settings.json, scores_config.json,
                                Takeout files and the sync lock
    GOOGLE_HEALTH_MCP_DB_PATH   the google-health MCP's cache (same variable the
                                MCP itself reads); default
                                ~/.local/share/google-health-mcp/google_health.db
    HEALTH_FRONTEND_DIR         the static web app served at /app/
"""

import os
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # src/health_dashboard -> repo root
DEFAULT_MCP_DB = Path("~/.local/share/google-health-mcp/google_health.db")


@dataclass(frozen=True)
class Paths:
    data: Path
    mcp_db: Path
    frontend: Path = REPO / "frontend"

    @property
    def health_db(self) -> Path:
        return self.data / "health.db"

    @property
    def logs(self) -> Path:
        return self.data / "logs"

    @property
    def settings(self) -> Path:
        """Per-user settings (watch name, first day), see settings.py."""
        return self.data / "settings.json"

    @property
    def scores_config(self) -> Path:
        """Frozen score calibration (Strain k, Recovery weights)."""
        return self.data / "scores_config.json"

    # Shared by every sync: the backend jobs, sync-on-access and the Takeout import.
    @property
    def sync_lock(self) -> Path:
        return self.data / ".sync.lock"

    @property
    def last_sync(self) -> Path:
        return self.data / ".last_sync"

    @property
    def sync_log(self) -> Path:
        return self.logs / "sync.log"


def get_paths() -> Paths:
    env = os.environ.get
    return Paths(
        data=Path(env("HEALTH_DATA_DIR") or REPO / "data").expanduser(),
        mcp_db=Path(env("GOOGLE_HEALTH_MCP_DB_PATH") or DEFAULT_MCP_DB).expanduser(),
        frontend=Path(env("HEALTH_FRONTEND_DIR") or REPO / "frontend").expanduser(),
    )


def child_env(paths: Paths) -> dict:
    """Environment for a child process that must see the same data as us."""
    env = dict(os.environ)
    env.update(HEALTH_DATA_DIR=str(paths.data), GOOGLE_HEALTH_MCP_DB_PATH=str(paths.mcp_db),
               HEALTH_FRONTEND_DIR=str(paths.frontend))
    return env
