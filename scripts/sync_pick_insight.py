"""Run centrally against the application's mounted database; never on page GET."""
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if not os.environ.get('DB_PATH') or not os.environ.get('SPORTMONKS_API_TOKEN'):
        raise SystemExit('DB_PATH and SPORTMONKS_API_TOKEN are required')
    os.environ['INIT_ON_START'] = '0'
    import pickem_flask_htmx_tabs as app
    from sportmonks_season import SeasonClient, sync_season
    with app.SessionLocal() as db:
        season = app.active_season(db)
        try:
            result = sync_season(db, app, season, SeasonClient())
        except Exception:
            # Never print provider responses, request URLs, credentials or DB paths.
            raise SystemExit('Pick Insight sync failed; prior cache retained. Check season mapping, provider entitlement, and connectivity.') from None
    print(f"Pick Insight synchronized: {result['clubs']} clubs; {result['clubs_with_scorers']} with scorers")


if __name__ == '__main__':
    main()
