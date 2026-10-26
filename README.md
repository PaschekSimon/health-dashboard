# Health dashboard

A local web dashboard for your Google Health data (Pixel Watch / Fitbit),
with WHOOP-style scores computed from your own heart-rate, HRV and sleep data.
Everything runs on your PC: the data stays in a SQLite file, the API listens
on `127.0.0.1` only, and nothing is uploaded anywhere.

**Tabs**
- **Dashboard**: today's Recovery, last night's Sleep Performance, Strain so far
  with a target for the day, steps, this week's running.
- **Recovery**: daily Recovery with the HRV, resting HR and breathing z-scores
  behind it, and early warnings when several signals are off at once.
- **Sleep**: every night with stages (hypnogram), sleep need and Sleep Performance.
- **Activity**: steps, distance, floors, calories, active and sitting time; one
  day in detail (HR, steps per hour, workouts).
- **Training**: daily Strain, fitness / fatigue / form, every workout. A run
  shows pace, HR and cadence over time, HR zones, km splits and the GPS route.
- **Long-term**: Healthspan age, VO2 max, weight, monthly averages, ECGs, events.
- **Data**: every table in the database, paged, with CSV export.

How the scores are built: [docs/scores.md](docs/scores.md).

## How it works

```
Google Health API ──> google-health-mcp ──> MCP cache (daily values, sleep, workouts)
                  └─> scripts/health_sync.py ──> data/health.db (2 s heart rate, HRV, per-minute steps ...)
                                                     │
          scripts/run_scores.py, daily_check.py, ... ┘ writes scores into data/health.db
                                                     │
          backend/ (FastAPI, 127.0.0.1:8765) ────────┘ reads both, serves frontend/ at /app/
```

- [google-health-mcp](https://github.com/partymola/google-health-mcp) does the
  Google login and keeps a cache of daily values, sleep sessions and workouts.
- `scripts/` adds what that cache drops (intraday samples), computes the scores
  and analyses, and can import a Google Takeout export for data the API does
  not offer (Google's own sleep score, readiness, stress, running dynamics).
- `backend/` is the API the dashboard talks to, plus an optional MCP server so
  Claude (or another MCP client) can read the same numbers and add notes.
- `frontend/` is plain HTML, CSS and JavaScript. There is no build step.

## Requirements

- Windows (the launch scripts and paths assume it; the Python code itself is portable).
- [uv](https://docs.astral.sh/uv/) and Python 3.12+.
- A Google account with Google Health data and your own Google Cloud OAuth
  client (the google-health-mcp README walks you through it).

## Setup

1. **Install and authorise google-health-mcp** (its README has the Google Cloud steps):

   ```
   uv tool install google-health-mcp
   google-health-mcp auth
   google-health-mcp doctor
   google-health-mcp sync --days 90
   ```

   The scripts run with that tool's Python
   (`%APPDATA%\uv\tools\google-health-mcp\Scripts\python.exe`), because it
   holds the Google login. They only need the standard library.

2. **Tell it which watch is yours** (optional). Create `data\settings.json`:

   ```json
   {"watch": "Pixel Watch 3", "watch_start": "2025-06-01"}
   ```

   `watch` is the device name Google reports for your watch (default
   `Pixel Watch 3`). Only that device's data is used, so phone steps and
   other devices don't mix in. `watch_start` is your first day with the watch.
   If you leave it out, it is the first day of that watch's heart rate in the
   database, or a year ago before the first sync.

3. **Pull the intraday data and compute the scores** (from `scripts\`):

   ```
   set PY=%APPDATA%\uv\tools\google-health-mcp\Scripts\python.exe
   %PY% -I health_sync.py
   %PY% -I run_scores.py
   ```

   The first run fits the Strain scale and the Recovery weights to your own
   history and stores them in `data\scores_config.json`. After that they stay
   frozen so scores don't drift; `--recalibrate` refits the Strain scale.
   Later runs only re-read the days whose raw data changed (`--full` rebuilds
   that cache from scratch, see docs/scores.md).

4. **Set up the backend** (from `backend\`):

   ```
   uv venv .venv --python 3.12
   uv pip install --python .venv\Scripts\python.exe fastapi "uvicorn[standard]" mcp httpx
   ```

5. **Open the dashboard**:

   ```
   backend\.venv\Scripts\pythonw.exe backend\open_app.pyw
   ```

   This starts the API hidden in the background (if it isn't running yet) and
   opens http://127.0.0.1:8765/ in your browser. A desktop shortcut to that
   command gives you a one-click app. For the API in a console window instead:
   `powershell -ExecutionPolicy Bypass -File backend\run_backend.ps1`.

   To start the API at every logon, put a shortcut to
   `backend\.venv\Scripts\pythonw.exe backend\open_app.pyw --no-browser` in
   `shell:startup`.

### Phone access (optional)

The API stays bound to `127.0.0.1`. To open the dashboard on your phone, install
[Tailscale](https://tailscale.com) on the PC and the phone (same account), then
on the PC run:

```
tailscale up --unattended
tailscale serve --bg http://127.0.0.1:8765
```

`tailscale serve` proxies the local port over HTTPS to devices in your tailnet
only; `tailscale serve status` shows the address
(`https://<pc-name>.<tailnet>.ts.net/`). Don't use `tailscale funnel`, which
would make the dashboard public.

On the phone, open that address in Chrome and choose **Install app** (or
**Add to home screen**): the dashboard gets its own icon and opens without the
browser bar.

The API has no login, so it only answers to host names that can point at it
(`127.0.0.1`, `localhost`, IP addresses, `*.ts.net`, bare machine names) and
refuses writes that another website's page sends. If you reach it under
another name, add that name to the `HEALTH_ALLOWED_HOSTS` environment variable
(comma-separated) before starting the backend.

## Staying up to date

Opening the dashboard starts a sync (skipped if one finished in the last
15 minutes); the **Sync** button always runs one. A sync updates the MCP cache,
pulls new intraday samples and recomputes every score. Only one sync runs at a
time, guarded by `data/.sync.lock`.

`scripts/sync_on_access.py` starts a background sync from anything else, for
example a Claude Code `SessionStart` hook. It skips if one ran in the last 3 hours.
As a hook in `.claude/settings.json` (paths are yours):

```json
{"hooks": {"SessionStart": [{"matcher": "startup|resume", "hooks": [{"type": "command", "timeout": 15,
  "command": "\"C:/Users/<you>/AppData/Roaming/uv/tools/google-health-mcp/Scripts/pythonw.exe\" -I \"C:/path/to/repo/scripts/sync_on_access.py\""}]}]}}
```

Optional: `scripts/takeout_import.py` loads a Google Takeout export
(`Takeout/Google Health`), and `scripts/takeout_auto.py` can run from a
scheduled task to pick up new Takeout ZIPs from Downloads and import them.

## Notes and exclusions

Illness or a night of drinking wrecks HRV and resting HR for days. Mark them as
annotations (dashboard, API or MCP) and the baselines skip those nights. Which
kind excludes what is set in `scripts/annotation_effects.py`.

## Project layout

```
frontend/   the dashboard (index.html, app.js, charts.js, style.css), vendor/ (Leaflet, fonts), app icons
backend/    FastAPI app, MCP server, health_core/ (queries, sync jobs, annotations), tests/
scripts/    sync, Takeout import, scores and analyses (standard library only)
docs/       scores.md (how the scores work), api.md (endpoints, MCP tools)
tests/      pytest suite on synthetic data, golden/scores.json (the expected scores)
data/       your data, created on first sync, never committed
```

`data/` holds `health.db`, `settings.json`, the score calibration
(`scores_config.json`), logs, the Takeout copy and the sync state. The MCP
cache and the Google tokens live in google-health-mcp's own folders
(`~/.local/share/google-health-mcp`, `~/.config/google-health-mcp`). The
`.gitignore` keeps data, logs, tokens and OAuth client files out of git.

## Tests and linting

`ruff check .` lints the Python code (settings in `ruff.toml`). To run it on
every commit: `uv tool install pre-commit`, then `pre-commit install` once.

`python -m pytest` (from the repo root, with pytest installed) runs the test
suite in `tests/` on a made-up watch history that `tests/fixture_db.py` builds
in a temporary folder, so it never touches `data/`. It includes a golden test:
every score on that history is stored in `tests/golden/scores.json`, and any
change to a number fails the test with a list of what moved. When a change to
the scores is intended, rewrite the file with `UPDATE_GOLDEN=1 python -m pytest`
and commit it with the change. GitHub Actions runs ruff and the suite on Linux
and Windows for every push and pull request.

With the API running:

```
backend\.venv\Scripts\python.exe backend\tests\test_api.py    # every endpoint
backend\.venv\Scripts\python.exe backend\tests\test_mcp.py    # the MCP server over stdio
```

## Limits

- Built for one person with one watch: workouts and samples are filtered to
  the device in `data\settings.json`. Tested with a Pixel Watch 3 only.
- Healthspan age compares VO2 max with norms for men aged 20-29. Adjust `REF`
  and `VO2_CAP` in `scripts/healthspan.py` for other ages or for women.
- The scores are modelled on WHOOP's published descriptions, not WHOOP's
  algorithms. Use them as a personal trend, not a medical measurement.

## License

GPL-3.0-or-later, see [LICENSE](LICENSE). The scripts import
[google-health-mcp](https://github.com/partymola/google-health-mcp), which is
GPL-3.0-or-later too.
