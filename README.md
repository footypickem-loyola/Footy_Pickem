# Footy Pick'Em

Flask + HTMX Premier League pick'em app for six players.

## Local setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Seasons and safe migration

When the updated app first opens an older single-season database, it automatically:

- creates an adjacent `*.pre_seasons.db` backup;
- assigns all existing weeks, picks, and results to archived `Year 1`;
- enables duplicate week numbers across separate seasons.

Initialize Year 2 once, using the actual Year 2 fixture CSV:

```powershell
$env:DB_PATH="sqlite:///pickem_local.db"
$env:INIT_ON_START="1"
.\.venv\Scripts\python.exe pickem_flask_htmx_tabs.py --csv epl_2026.csv --weeks all --players "Steve,Joe,Marc,Drew,Scott,Connor" --room LOCALTEST --season-code year-2 --season-name "Year 2" --host 127.0.0.1 --port 5000
```

Initialization refuses to replace any season that already contains weeks. `ALLOW_SEASON_RESET=1`
exists only for an intentional reset after making a verified backup.

For later starts, use `INIT_ON_START=0` so existing local data is preserved:

```powershell
$env:DB_PATH="sqlite:///pickem_local.db"
$env:INIT_ON_START="0"
.\.venv\Scripts\python.exe pickem_flask_htmx_tabs.py --csv epl_2025.csv --weeks all --players "Steve,Joe,Marc,Drew,Scott,Connor" --room LOCALTEST --host 127.0.0.1 --port 5000
```

Open <http://127.0.0.1:5000/join>.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Production safety

- Railway deploys from `main`; Year 2 work belongs on `year-2-development`.
- Production SQLite lives at `/data/pickem.db` on the Railway volume.
- Keep `INIT_ON_START=0` in production except during an intentional, backed-up initialization.
- Archived seasons reject pick and result writes at the server, not only in the UI.
- Never commit room codes, Flask secrets, API keys, local databases, or `.env` files.
