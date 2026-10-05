"""Developer-only read-only JSON inspection; deliberately does not import Flask."""
import argparse
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fixture_intelligence import build_fixture_intelligence
from fixture_history import load_fixture_history


def inspect(database, fixture_id, as_of, target_season_start=None):
    path = Path(database).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only = ON")
        db.row_factory = sqlite3.Row

        def records(sql, params=()):
            items = []
            for row in db.execute(sql, params):
                data = dict(row)
                for key in ("kickoff_utc", "updated_at"):
                    if key in data and data[key] is not None:
                        data[key] = datetime.fromisoformat(data[key].replace("Z", "+00:00"))
                items.append(SimpleNamespace(**data))
            return items

        # One consistent read transaction, no schema initialization or app startup.
        db.execute("BEGIN")
        target = records("SELECT f.*, w.season_id FROM fixtures f JOIN weeks w ON w.id=f.week_id WHERE f.id=?", (fixture_id,))
        if not target:
            raise ValueError("Fixture not found")
        fixture = target[0]
        weeks = records("SELECT id, season_id FROM weeks WHERE season_id=?", (fixture.season_id,))
        fixtures = records("SELECT f.* FROM fixtures f JOIN weeks w ON w.id=f.week_id WHERE w.season_id=?", (fixture.season_id,))
        results = records("SELECT r.* FROM results r JOIN fixtures f ON f.id=r.fixture_id JOIN weeks w ON w.id=f.week_id WHERE w.season_id=?", (fixture.season_id,))
        history = load_fixture_history(db, fixture=fixture, as_of=as_of, target_season_start=target_season_start)
        return build_fixture_intelligence(fixture=fixture, fixtures=fixtures, results=results,
                                          weeks=weeks, season_id=fixture.season_id, as_of=as_of, reference_history=history)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to a local SQLite snapshot")
    parser.add_argument("--fixture-id", required=True, type=int)
    parser.add_argument("--as-of", required=True, type=datetime.fromisoformat, help="Explicit ISO timestamp; naive means UTC")
    parser.add_argument("--target-season-start", type=int, help="Optional PL season starting year; default derives July-to-June from target kickoff")
    args = parser.parse_args()
    print(json.dumps(inspect(args.db, args.fixture_id, args.as_of, args.target_season_start), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
