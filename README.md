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

For a Railway cron service or another scheduler, run one synchronization and exit:

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
- Archived seasons reject pick and result writes at the server, not only in the UI.
- Never commit room codes, Flask secrets, API keys, local databases, or `.env` files.
