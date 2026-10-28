"""Google Health API: login, requests and the daily cache.

Derived from google-health-mcp 1.8.0 by jjdelrio
(https://github.com/partymola/google-health-mcp), GPL-3.0-or-later, and
modified for health-dashboard in October 2026: the MCP server, its query
tools, `doctor`, the importer and the types the dashboard never reads were
removed, paths come from health_dashboard.config, and the sync runs its types
in parallel. Each copied module says what changed in it.

    config    scopes, URLs, where the client file and tokens live
    auth      OAuth login (`health auth`) and token refresh
    api       requests, paging, retries, rate limits, the type catalogue
    cache     the cache's schema (google_health.db) and its writers
    handlers  one function per cached type: Google's points -> cache rows
    sync      the incremental sync loop (`health daily-sync`)
    doctor    a short setup check (`health doctor`)
"""
