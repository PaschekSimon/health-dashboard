"""The `health` command (also `python -m health_dashboard`).

    health serve [--host H] [--port P]   the local API and web app
    health open [--no-browser]           start the API hidden if needed, open the app
    health mcp                           the "health-app" MCP server (stdio)
    health sync [--since D] [--types T]  intraday samples from Google into health.db
    health sync-on-access                background sync unless one ran in the last 3 h
    health takeout-import [FOLDER]       load a Google Health Takeout export
    health takeout-auto                  monthly Takeout helper (scheduled task)
    health daily-check | run-report | weekly-summary | baseline
    health scores [--recalibrate] [--retune]   Strain, Recovery, Sleep, Healthspan
    health strain | recovery | healthspan       one score

Every command reads its paths from the environment (HEALTH_DATA_DIR, ...,
see config.py). Each runs its module's main(argv), so
`python -m health_dashboard.scores.strain` works the same way.
"""

import importlib
import sys

COMMANDS = {  # command -> module with main(argv)
    "serve": None,
    "open": "launcher",
    "mcp": "mcp_server",
    "sync": "sync.health_sync",
    "sync-on-access": "sync.on_access",
    "takeout-import": "sync.takeout_import",
    "takeout-auto": "sync.takeout_auto",
    "baseline": "analysis.baseline",
    "daily-check": "analysis.daily_check",
    "run-report": "analysis.run_report",
    "weekly-summary": "analysis.weekly_summary",
    "scores": "scores.run",
    "strain": "scores.strain",
    "recovery": "scores.recovery",
    "healthspan": "scores.healthspan",
}


def serve(argv):
    import argparse

    import uvicorn
    ap = argparse.ArgumentParser(prog="health serve", description="Run the local API and web app.")
    ap.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1 (this machine only)")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    uvicorn.run("health_dashboard.app:app", host=args.host, port=args.port)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in COMMANDS:
        print(__doc__.strip())
        sys.exit(0 if not argv or argv[0] in ("-h", "--help") else 2)
    cmd, rest = argv[0], argv[1:]
    sys.argv = [f"health {cmd}", *rest]  # argparse help shows the command
    if cmd == "serve":
        return serve(rest)
    result = importlib.import_module(f"health_dashboard.{COMMANDS[cmd]}").main(rest)
    if isinstance(result, int):
        sys.exit(result)
