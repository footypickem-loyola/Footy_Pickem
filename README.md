# Footy Pick'Em

Flask + HTMX Premier League pick'em app for six players.

## Local setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Safe database migrations

When the updated app first opens an older single-season database, it automatically:

- creates an adjacent `*.pre_seasons.db` backup;
- assigns all existing weeks, picks, and results to archived `Year 1`;
- enables duplicate week numbers across separate seasons.

Before adding football-data.org fields to an existing database, it also creates
`*.pre_football_api.db`. The migration preserves all fixtures, picks, results, and
season history.

## Start the app and import 2026–27 fixtures

Set the API key only in the local environment. Never paste it into source code or
commit it to GitHub.

```powershell
$env:DB_PATH="sqlite:///pickem_local.db"
$env:INIT_ON_START="0"
$env:FOOTBALL_DATA_API_KEY="your-token-here"

.\.venv\Scripts\python.exe pickem_flask_htmx_tabs.py --host 127.0.0.1 --port 5000
```

Then:

1. Open <http://127.0.0.1:5000/admin>.
2. Unlock Admin using the existing Year 1 room code.
3. Review the six player names and choose the Year 2 room code.
4. Select **Import Premier League Fixtures**.

The API import validates all 380 fixtures and all 38 matchdays before writing
anything. It refuses to overwrite a season that already contains weeks.

Open <http://127.0.0.1:5000/join> after the import.

## Season statistics

The Current Week page includes a compact Season Leaders card for biggest weekly
win, correct and incorrect pick leaders, perfect weeks, winning and losing
streaks. The Stats tab adds player-filtered head-to-head records with detailed
for/against pick breakdowns, plus club-picking records with club, minimum-picks,
and best/worst/most-picked filters. Statistics use finalized weeks only and
remain isolated by season.

## AI Correspondent V1

The Python application can generate and retain an editorial recap for any
finalized active-season week. Python builds the complete factual context from
the game database; OpenAI is used only to select the interesting stories and
write the recap. Score synchronization and week finalization never depend on
the OpenAI request.

Set the API configuration in the environment. Never put the key in source code
or commit it to GitHub.

```powershell
$env:OPENAI_API_KEY="your-openai-api-key"
$env:OPENAI_MODEL="gpt-5.6-luna"
```

`OPENAI_MODEL` is optional and defaults to `gpt-5.6-luna`. After all ten
results for a week are final, open Admin, select the week, and click **Generate
Weekly Recap**. Each attempt is stored as a new revision, including the exact
JSON fact snapshot and prompt version used. A failed OpenAI request is recorded
without changing picks, results, standings, payouts, or week status.

The OpenAI transport is isolated in `correspondent/writer.py`. The versioned
voice and editorial instructions live in
`correspondent/prompts/weekly_recap_v1.md`.

## Arsenal pick banter

After a successful Arsenal pick, the app fires a one-time HTMX response event
and displays a random full-screen image from `static/arsenal_banter` with the
champions message. The overlay auto-dismisses after four seconds or on tap and
does not replay on refresh. Expected filenames are `banter_01.jpg` through
`banter_10.jpg`.

## Automatic final scores

The Admin page includes **Sync Final Scores**. Each synchronization uses one
season-level API request, updates fixture statuses and kickoff times, and imports
only matches marked `FINISHED` with a complete full-time score.

The API client reads `X-Requests-Available-Minute` and
`X-RequestCounter-Reset` after every response. It automatically waits when the
allowance is exhausted and handles one throttled retry. The Admin health panel
shows the latest allowance, reset time, pending matches, unmatched fixtures, and
last error.

Manual results are authoritative: a later API sync will not overwrite a result
whose source is `manual`. API-sourced results may be updated if the provider
corrects a score.

For production automation with the volume-backed SQLite database, configure a
separate scheduler to send a `POST` request to the existing web service:

```text
https://YOUR-APP.up.railway.app/tasks/sync-results
X-Sync-Secret: a-long-random-secret
```

Set the same random value as the web service's `SYNC_SECRET` environment
variable. The endpoint returns `403` for an incorrect secret and `503` when the
server secret is not configured. Never put the secret in the URL or repository.

The production cadence is every 15 minutes on Saturday and Sunday and hourly
Monday through Friday, using Eastern time. Create a separate Railway cron
service from this repository with:

```text
Start Command: python trigger_score_sync.py
Cron Schedule: */15 * * * *
SYNC_URL: https://YOUR-APP.up.railway.app/tasks/sync-results
SYNC_SECRET: the same secret configured on the web service
SYNC_SCHEDULE_TIMEZONE: America/New_York
```

Railway starts the caller every 15 minutes. The caller syncs on every weekend
run and exits without calling the API on weekday quarter-hour runs except at
the top of each hour. This application-level Eastern-time check keeps the
Saturday/Sunday boundary correct through daylight-saving changes even though
Railway evaluates cron expressions in UTC. The caller prints its result and
exits after every run. The web service performs the database write on the
service that owns the persistent volume.
Do not enable the cron schedule until the endpoint is deployed and Year 2 has
been initialized as the writable active season.

The command below remains available for maintenance when it runs inside a
container that has the same database volume mounted:

```powershell
.\.venv\Scripts\python.exe pickem_flask_htmx_tabs.py --sync-api-once
```

Do not schedule more frequently than needed; the command already honors the
provider's response-header throttling instructions.

## Legacy CSV initialization

CSV import remains available only as a fallback. `epl_2025.csv` should remain in
the repository for Year 1 history and recovery.

```powershell
$env:INIT_ON_START="1"
.\.venv\Scripts\python.exe pickem_flask_htmx_tabs.py --csv epl_2025.csv --weeks all --players "Steve,Joe,Marc,Drew,Scott,Connor" --room LOCALTEST
```

Initialization refuses to replace a season that already contains weeks.
`ALLOW_SEASON_RESET=1` exists only for an intentional reset after making and
verifying a separate backup.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Production safety

- Railway deploys from `main`; Year 2 work belongs on `year-2-development`.
- Production SQLite lives at `/data/pickem.db` on the Railway volume.
- Keep `INIT_ON_START=0` in production except during an intentional, backed-up CSV initialization.
- Store `FOOTBALL_DATA_API_KEY` only as a local or Railway environment variable.
- Store `SYNC_SECRET` only as a Railway environment variable and send it in the `X-Sync-Secret` header.
- Store `OPENAI_API_KEY` only as a local or Railway environment variable.
- Archived seasons reject pick and result writes at the server, not only in the UI.
- Never commit room codes, Flask secrets, API keys, local databases, or `.env` files.

## Production rollout order

1. Run the final local smoke test.
2. Back up and verify `/data/pickem.db` from the Railway volume.
3. Merge `year-2-development` into `main` and deploy the web service.
4. Confirm the archived Year 1 data is still readable.
5. Initialize Year 2 through the protected Admin API import.
6. Configure and enable the Railway cron service.
7. Verify a successful automatic execution in the cron and web-service logs.
