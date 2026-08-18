# Footy Pick'Em

Flask + HTMX Premier League pick'em app for six players.

## Local setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Initialize a new local database once:

```powershell
$env:DB_PATH="sqlite:///pickem_local.db"
$env:INIT_ON_START="1"
python pickem_flask_htmx_tabs.py --csv epl_2025.csv --weeks all --players "Steve,Joe,Marc,Drew,Scott,Connor" --room LOCALTEST --host 127.0.0.1 --port 5000
```

For later starts, use `INIT_ON_START=0` so existing local data is preserved:

```powershell
$env:DB_PATH="sqlite:///pickem_local.db"
$env:INIT_ON_START="0"
python pickem_flask_htmx_tabs.py --csv epl_2025.csv --weeks all --players "Steve,Joe,Marc,Drew,Scott,Connor" --room LOCALTEST --host 127.0.0.1 --port 5000
```

Open <http://127.0.0.1:5000/join>.

## Tests

```powershell
python -m unittest discover -s tests -v
```

## Production safety

- Railway deploys from `main`; Year 2 work belongs on `year-2-development`.
- Production SQLite lives at `/data/pickem.db` on the Railway volume.
- Keep `INIT_ON_START=0` in production except during an intentional, backed-up initialization.
- Never commit room codes, Flask secrets, API keys, local databases, or `.env` files.
