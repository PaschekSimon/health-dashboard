# API and MCP server

The dashboard in `web/` talks to the FastAPI app in
`src/health_dashboard/app.py` (`health serve`, http://127.0.0.1:8765, this machine only). Interactive docs for every endpoint:
http://127.0.0.1:8765/docs.

Every route has a Pydantic response model (`src/health_dashboard/core/schemas.py`),
so `/docs` shows each answer's shape and http://127.0.0.1:8765/openapi.json
describes all of them. The web app's TypeScript types are generated from it
(`npm run gen:types` in `web/`, see web/README.md).

Fields that can be empty are always sent, as `null`. A column a script adds
later is passed through before the model lists it.

## Endpoints

Dates are `YYYY-MM-DD`, local. A night is keyed by its wake date. Every read
answer includes the annotations touching it (`annotations`, and `annotation_ids` per row).

| Method | Path | What |
|---|---|---|
| GET | `/meta` | fixed lists: annotation kinds/statuses/sources, sync steps (name + label) by job reason, Recovery and Strain bands, Strain targets, trend metrics |
| POST | `/sync` `{"reason": "open"\|"button"}` | start a sync job, returns its row (`id`, `status`) |
| GET | `/sync/{id}` | job status and steps (`running`, `ok`, `partial`, `failed`, `skipped`, `busy`, `interrupted`) |
| GET | `/status` | last successful sync, newest data per table, health_sync watermarks, sync running?, last 5 runs |
| GET | `/dashboard?date=` | Recovery %, last night's sleep + Sleep Performance, Strain + target, steps, this week's running (with `easy_pct`), daily-check warning |
| GET | `/recovery?from=&to=` | daily Recovery with z-scores and the daily-check flag (default: last 30 days) |
| GET | `/sleep?from=&to=` | Google's sleep summary + Sleep Performance per night |
| GET | `/runs?from=&to=` | runs from `analysis_workouts` (+ Strain) and a `running` summary (runs, km, minutes, TRIMP, `easy_pct`); `&all_types=true` for every workout |
| GET | `/runs/{id}` | one workout: analysis, Google summary, km splits, minutes per HR zone Z1–Z5 (`hr_zones`) (id = short numeric id or full log_id) |
| GET | `/trend/{metric}?from=&to=` | one metric over time (recovery, hrv_ms, rhr, sleep_performance, strain, steps, ...) |
| GET | `/explain/{recovery\|sleep\|strain}?date=` | score broken into components |
| GET | `/activity?from=&to=` | per day: steps (watch), Google steps, distance, floors, calories, AZM, active minutes, sitting time |
| GET | `/day/{date}` | one day intraday: HR (5-min), steps per hour, sedentary periods, sleep, workouts |
| GET | `/training?from=&to=` | daily Strain, weekly load (fitness/fatigue/form), Google ACWR, personal records |
| GET | `/workout-series/{id}` | one workout: HR (10 s), running dynamics, GPS route shape |
| GET | `/longterm` | Healthspan (all variants), monthly averages, VO2 max, weight, ECG list, assessments, all annotations |
| GET | `/ecg/{id}` | one ECG with its waveform |
| GET | `/data` | every table in health.db and the daily cache: rows, columns, date range |
| GET | `/data/{health\|google}/{table}?from=&to=&limit=&offset=&desc=` | raw rows (long text cut, epoch times also as local time) |
| GET | `/annotations?from=&to=&kind=&status=` | annotations touching the range |
| POST | `/annotations` | create (`kind`, `start`, `end`, `target`, `attrs`, `status`, `source`, `confidence`, `raw_text`, `note`) |
| POST | `/annotations/{id}/confirm` | set confirmed |
| POST | `/annotations/{id}/reject` | set rejected |
| DELETE | `/annotations/{id}` | delete |

Creating, confirming, rejecting or deleting a **confirmed alcohol or illness**
annotation starts a recompute (the four analysis scripts); the response carries
it under `recompute` (poll `/sync/{id}`).

Requests with a `Host` the API doesn't know (anything but `127.0.0.1`,
`localhost`, an IP address, `*.ts.net`, a bare machine name or a name in
`HEALTH_ALLOWED_HOSTS`) get a 403, and so do writes (POST, DELETE) whose
`Origin` is another site. This keeps web pages on other sites from changing
your data through your browser.

## Zones and easy share

Google reports four heart-rate zones; the app shows five. Z1, Z2 and Z5 are
Google's light, moderate and peak zones, and Z3 and Z4 split Google's
vigorous zone at its middle (`split_bpm`, about 72 % of heart-rate reserve).
The minutes per zone come from the run analysis; the vigorous minutes are
shared out between Z3 and Z4 by how the workout's 10-second HR means fall
either side of the split (without HR samples they all count as Z3). In
`hr_zones`, `from_bpm` is the zone's lowest bpm and `to_bpm` the first bpm
above it.

`easy_pct` is the share of run time at or below the top of Z2
(`pct_below_vigorous` of each run, weighted by its minutes). Both are worked
out in `src/health_dashboard/core/zones.py`, so the app and the MCP server show the
same numbers.

## Sync rules

- A sync job runs `health daily-sync` (daily cache: daily values, sleep,
  workouts; step `mcp_cache`), `health sync` (intraday samples), then `health daily-check`,
  `run-report`, `weekly-summary` and `scores`, each as its own process of the
  same environment. Output goes to `data/logs/sync.log`.
- Single flight: it holds `data/.sync.lock`, the same lock as `health sync-on-access`
  (e.g. a Claude Code SessionStart hook). If someone else holds it the request
  returns `busy` (or the id of our own running job).
- `reason=open` is skipped if the last successful full sync (a `sync_runs` row) finished < 15 min ago
  (sync-on-access's `.last_sync` doesn't count: it only runs `health sync`);
  `reason=button` always runs.
- Every run is a row in `sync_runs` (id, reason, started, finished, status, error, origin, steps).
- Jobs run in a detached process, so they finish even if the server stops.
- `health sync` already resumes per type from `sync_state` and re-reads the last
  2 days (upserts keyed by time + source), so nothing was added there.

## Annotations

Table `annotations` in health.db: kind (alcohol, illness, injury, travel,
late_meal, stress, run_intent, post_run_protocol, bad_sleep_env, note), start,
end, target (`night:YYYY-MM-DD` = wake date, `day:YYYY-MM-DD`, `workout:<id>`),
attrs (JSON), status (suggested / confirmed / rejected), source (you / ai /
strava / import), confidence, raw_text, note, created_at, updated_at.

Only confirmed ones change numbers, and only kinds listed in
`src/health_dashboard/analysis/annotation_effects.py` `EFFECTS` (now: illness = every night in the range,
alcohol = the night after the drinking evening). Adding a kind's effect is one
entry there.

## MCP server "health-app"

A stdio MCP server over the same data, so Claude (or any MCP client) can read
your scores and record context. Add it to Claude Desktop's
`%APPDATA%\Claude\claude_desktop_config.json` and restart Claude Desktop:

```json
"health-app": {
  "command": "C:\\path\\to\\repo\\.venv\\Scripts\\python.exe",
  "args": ["-m", "health_dashboard", "mcp"]
}
```

On Linux the command is `/path/to/repo/.venv/bin/health` with `"args": ["mcp"]`.

Tools: `get_day`, `get_trend`, `list_runs`, `get_run`, `list_annotations`,
`add_annotation` (confirmed only when the user stated the fact), `delete_annotation`,
`explain_score`, `sync_now`, `get_sync_status`.

`get_day`, `get_trend`, `list_runs`, `get_run` and `explain_score` start a sync
job (`reason=open`, origin `mcp`) in the background when the last successful
one finished over 3 hours ago, so Claude's next answer has fresh data.
