"""Verify startup/rendering against a disposable copy of an existing SQLite DB.

The source is opened read-only and never passed to the application. No sync,
result submission, initialization, or production endpoint is invoked.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch


CORE_TABLES = ("players", "weeks", "fixtures", "matchups", "picks", "results")
ROOT = Path(__file__).resolve().parents[1]


def snapshot(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        schema = db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        rows = {table: db.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall()
                for table in CORE_TABLES}
        return {"schema": schema, "rows": rows}


def digest(value):
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


def render_copy(copy_path):
    os.environ["DB_PATH"] = "sqlite:///" + copy_path.as_posix()
    os.environ["INIT_ON_START"] = "0"
    sys.path.insert(0, str(ROOT))
    # Block any unexpected network access during startup and rendering.
    with patch("socket.socket.connect", side_effect=AssertionError("Network access forbidden")):
        import pickem_flask_htmx_tabs as module
        module.app.config.update(TESTING=True)
        db = module.SessionLocal()
        weeks = [(w.number, w.season.code) for w in db.query(module.Week).order_by(module.Week.id)]
        players = [p.name for p in db.query(module.Player).order_by(module.Player.id)]
        seasons = [s.code for s in db.query(module.Season).order_by(module.Season.id)]
        detailed_expected = {}
        for season in db.query(module.Season).order_by(module.Season.id):
            season_players = module.season_players(db, season)
            detailed_expected[season.code] = {}
            for week in db.query(module.Week).filter_by(season_id=season.id):
                points = module.weekly_for_against(db, week)
                values = []
                for player in season_players:
                    scored = points.get(player.id, {"for": 0, "against": 0})
                    values.extend([str(scored['for']), str(scored['against']),
                                   str(scored['for'] - scored['against'])])
                detailed_expected[season.code][week.number] = values
        module.SessionLocal.remove()
        count = 0
        try:
            with module.app.test_client() as client:
                def get(path, query=None, headers=None):
                    nonlocal count
                    response = client.get(path, query_string=query, headers=headers)
                    assert response.status_code == 200, (path, response.status_code)
                    count += 1
                    return response

                get("/")
                for name in players:
                    with client.session_transaction() as session:
                        session["player_name"] = name
                    for season in seasons:
                        for tab in ("current", "open", "season", "stats"):
                            response = get(f"/tab/{tab}", {"season": season})
                            if tab == "season":
                                detail = response.get_data(as_text=True).split('id="weekly-detailed"', 1)[1]
                                rows = re.findall(r'<tr data-week="(\d+)">(.*?)</tr>', detail, re.S)
                                assert len(rows) == len(detailed_expected[season])
                                for number, row in rows:
                                    cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
                                    assert cells[1:-1] == detailed_expected[season][int(number)]
                            get(f"/tab/{tab}", {"season": season}, {"HX-Request": "true"})
                for number, season in weeks:
                    get("/tab/current", {"season": season, "force_week": number})
                    for panel in ("fixtures", "matchups", "scores"):
                        get(f"/partials/{panel}/{number}", {"season": season})
        finally:
            module.SessionLocal.remove()
            module.engine.dispose()
        print(json.dumps({"rendered_requests": count, "weeks_rendered": len(weeks),
                          "players_rendered": len(players), "seasons_rendered": len(seasons),
                          "detailed_weekly_values_verified": True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--render-copy", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.render_copy:
        render_copy(args.render_copy.resolve())
        return
    if args.source is None or not args.source.is_file():
        parser.error("--source must name an existing local database export or backup")
    source = args.source.resolve()
    source_bytes_before = hashlib.sha256(source.read_bytes()).hexdigest()
    source_before = snapshot(source)
    with tempfile.TemporaryDirectory(prefix="pickem-preservation-") as directory:
        copy_path = Path(directory) / "verification.db"
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(copy_path)) as dst:
                src.backup(dst)
        before = snapshot(copy_path)
        assert before == source_before, "Backup differs from source"
        child = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--render-copy", str(copy_path)],
            cwd=directory, capture_output=True, text=True, check=True,
            env={**os.environ, "INIT_ON_START": "0"},
        )
        after = snapshot(copy_path)
        assert before["schema"] == after["schema"], "Schema changed on startup/render"
        for table in CORE_TABLES:
            assert before["rows"][table] == after["rows"][table], f"{table} changed"
        assert snapshot(source) == source_before, "Source data changed"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_bytes_before, "Source bytes changed"
        report = {
            "source_name": source.name,
            "source_sha256": source_bytes_before,
            "source_byte_for_byte_unchanged": True,
            "schema_unchanged": True,
            "schema_sha256_before": digest(before["schema"]),
            "schema_sha256_after": digest(after["schema"]),
            "rendering": json.loads(child.stdout.strip().splitlines()[-1]),
            "tables": {table: {
                "before_count": len(before["rows"][table]),
                "after_count": len(after["rows"][table]),
                "before_sha256": digest(before["rows"][table]),
                "after_sha256": digest(after["rows"][table]),
                "all_columns_unchanged": True,
            } for table in CORE_TABLES},
        }
    output = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
