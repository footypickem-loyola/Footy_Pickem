"""Explicit local maintenance entry point. No Flask import or default DB path."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from football_reference import ReferenceClient, ReferenceError, SEASONS, sync_reference


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", required=True, action="append", type=int, choices=sorted(SEASONS))
    parser.add_argument("--db", help="Explicit path to an existing local SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Fetch, validate and report without opening any database")
    args = parser.parse_args(argv)
    if not args.dry_run and not args.db:
        parser.error("--db is required unless --dry-run is supplied")
    try:
        for season_id in dict.fromkeys(args.season):
            print(json.dumps(sync_reference(database=args.db, season_id=season_id,
                             client=client or ReferenceClient(), dry_run=args.dry_run), sort_keys=True))
    except ReferenceError as exc:
        raise SystemExit(str(exc)) from None
    except Exception:
        raise SystemExit("Reference sync failed; no successful refresh for this season") from None


if __name__ == "__main__":
    main()
