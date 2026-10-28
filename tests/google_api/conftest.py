"""Fixtures for the tests of health_dashboard.google, copied with that code
from google-health-mcp 1.8.0 (GPL-3.0-or-later) and trimmed to what was kept.

No test here sees real credentials, the real cache or the network.
"""

import urllib.request

import pytest

from health_dashboard.google import auth
from health_dashboard.google import cache as db


@pytest.fixture(autouse=True)
def _no_real_login_or_cache(tmp_path_factory, monkeypatch):
    """Credentials in an empty folder, the cache in a temp file, and none of
    the developer's own environment. Pointing the login at an empty folder
    means a refresh is refused before a request is built, so every machine
    takes the branch CI takes; a test wanting credentials writes them there."""
    monkeypatch.delenv("GOOGLE_HEALTH_MCP_OFFLINE", raising=False)
    monkeypatch.setenv("GOOGLE_HEALTH_MCP_CONFIG_DIR", str(tmp_path_factory.mktemp("credentials")))
    monkeypatch.setenv("GOOGLE_HEALTH_MCP_DB_PATH",
                       str(tmp_path_factory.mktemp("cache") / "google_health.db"))
    auth.invalidate_google_token_cache()
    monkeypatch.setattr(auth, "_cached_google_client", None)
    yield
    auth.invalidate_google_token_cache()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Refuse a real request. `pytest.fail` rather than `assert`: the code under
    test catches Exception in places, and `Failed` is a BaseException. A test
    that means to exercise a request patches `urlopen` itself."""

    def refuse(*_args, **_kwargs):
        pytest.fail("this test reached the network; patch urllib.request.urlopen to serve it")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.fixture
def tmp_db(tmp_path):
    """A temporary cache carrying this version's schema."""
    conn = db.get_db(tmp_path / "test_google_health.db")
    yield conn
    conn.close()
