"""Google Health API constants, and where the login is kept.

Derived from google-health-mcp 1.8.0 (config.py), GPL-3.0-or-later.
Modified 2026-10-28: paths are read when asked for rather than at import, the
cache path comes from health_dashboard.config, and only the scopes the
dashboard reads under are requested.

The login stays where google-health-mcp kept it, so an existing one keeps
working:

    GOOGLE_HEALTH_MCP_CONFIG_DIR   google_client.json and google_tokens.json;
                                   default ~/.config/google-health-mcp
    GOOGLE_HEALTH_MCP_OFFLINE      1/true/yes/on: never call Google
"""

import os
from pathlib import Path

DEFAULT_CONFIG_DIR = Path("~/.config/google-health-mcp")
OFFLINE_TRUTHY_VALUES = ("1", "true", "yes", "on")


def config_dir() -> Path:
    return Path(os.environ.get("GOOGLE_HEALTH_MCP_CONFIG_DIR") or DEFAULT_CONFIG_DIR).expanduser()


def client_path() -> Path:
    """Google's downloaded Desktop-client JSON, read as-is, so it can be dropped in unedited."""
    return config_dir() / "google_client.json"


def tokens_path() -> Path:
    return config_dir() / "google_tokens.json"


def offline() -> bool:
    """Cache-only mode: no live API calls (CI, a second machine reading a copied cache)."""
    return os.environ.get("GOOGLE_HEALTH_MCP_OFFLINE", "").strip().lower() in OFFLINE_TRUTHY_VALUES


GOOGLE_API_BASE = "https://health.googleapis.com/v4"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Each read-only scope requested, and what is read under it: data types by
# their API path, and the calls outside the data-point collection by function
# name. Never a writeonly scope. A grant does not gain scopes on refresh, so a
# scope left out costs a second consent; a scope with no reader is a
# permission granted for nothing.
#
# Google publishes a category per scope and no per-type mapping, so the
# placement here is inferred from those categories, and a wrong one answers
# 403. The intraday types health_sync reads (heart-rate, steps, ...) fall
# under the same two scopes as their daily twins.
GOOGLE_SCOPE_READERS: dict[str, tuple[str, ...]] = {
    "activity_and_fitness": (
        "steps",
        "distance",
        "floors",
        "total-calories",
        "active-zone-minutes",
        "exercise",
    ),
    "health_metrics_and_measurements": (
        "daily-resting-heart-rate",
        "daily-heart-rate-variability",
        "daily-oxygen-saturation",
        "daily-respiratory-rate",
        "daily-sleep-temperature-derivations",
        "daily-vo2-max",
        "weight",
        "body-fat",
        "height",
    ),
    "sleep": ("sleep",),
    "ecg": ("electrocardiogram",),
    "profile": ("get_profile",),
    # Exercise routes; Google requires it together with activity_and_fitness.
    "location": ("export_exercise_tcx",),
}
GOOGLE_SCOPE_PREFIX = "https://www.googleapis.com/auth/googlehealth."
GOOGLE_SCOPES = " ".join(f"{GOOGLE_SCOPE_PREFIX}{name}.readonly" for name in GOOGLE_SCOPE_READERS)


def missing_scopes(granted) -> list[str] | None:
    """The requested scopes a grant lacks, by short name, or None if unknown.

    `granted` is the space-separated string Google reports; anything else
    means nothing was recorded, which is not the same as nothing granted.
    """
    if not isinstance(granted, str):
        return None
    held = set(granted.split())
    return [n for n in GOOGLE_SCOPE_READERS if f"{GOOGLE_SCOPE_PREFIX}{n}.readonly" not in held]


GOOGLE_CALLBACK_PORT = 8081
GOOGLE_REDIRECT_URI = f"http://localhost:{GOOGLE_CALLBACK_PORT}"

# An access token lasts an hour, which is what `expires_in` reports when the
# response omits it.
GOOGLE_TOKEN_LIFETIME = 3600
