"""Run PR 11 browser review against a NEW temporary synthetic database only.

Usage: python scripts/review_auto_draft.py
Join as Steve with room PR11LOCAL. Week 1: empty draft; Week 2: skipped first
priority/double turn; Week 3: ninth pick (Steve has two priorities); archive
season: read-only draft. No existing database is opened or copied.
"""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix="footy-pr11-browser-") as temp:
        os.environ["DB_PATH"] = f"sqlite:///{Path(temp) / 'review.db'}"
        os.environ["INIT_ON_START"] = "0"
        import pickem_flask_htmx_tabs as d
        players = ["Steve", "Joe", "Marc", "Drew", "Scott", "Connor"]
        d.init_weeks_from_csv(str(ROOT / "epl_2025.csv"), [1, 2, 3], players, "PR11LOCAL")
        d.init_weeks_from_csv(str(ROOT / "epl_2025.csv"), [1], players, "PR11LOCAL",
                              season_code="pr11-archive", season_name="PR11 Archive")
        db = d.SessionLocal()
        season = db.query(d.Season).filter_by(code="year-2").one()
        season.is_active = 1
        archive = db.query(d.Season).filter_by(code="pr11-archive").one()
        archive.is_active = 0
        archive.is_archived = 1
        steve = db.query(d.Player).filter_by(name="Steve").one()
        for week in db.query(d.Week).filter_by(season_id=season.id):
            m = db.query(d.Matchup).filter(d.Matchup.week_id == week.id,
                (d.Matchup.player_a_id == steve.id) | (d.Matchup.player_b_id == steve.id)).one()
            other = m.player_b_id if m.player_a_id == steve.id else m.player_a_id
            m.first_picker_id = other if week.number == 2 else steve.id
            fixtures = db.query(d.Fixture).filter_by(week_id=week.id).order_by(d.Fixture.id).all()
            if week.number == 2:
                db.add(d.Pick(matchup_id=m.id, player_id=other, fixture_id=fixtures[0].id, team=fixtures[0].home))
                for i in range(3):
                    db.add(d.AutoDraftPreference(matchup_id=m.id, player_id=steve.id,
                        fixture_id=fixtures[i].id, team=fixtures[i].home, priority=i))
            if week.number == 3:
                for i in range(8):
                    db.add(d.Pick(matchup_id=m.id, player_id=d.draft_turn_at(steve.id, other, i),
                        fixture_id=fixtures[i].id, team=fixtures[i].home))
                for player in (steve.id, other):
                    for i in (8, 9):
                        db.add(d.AutoDraftPreference(matchup_id=m.id, player_id=player,
                            fixture_id=fixtures[i].id, team=fixtures[i].home, priority=i))
                db.add(d.AutoDraftSetting(matchup_id=m.id, player_id=other, enabled=1))
        db.commit()
        d.SessionLocal.remove()
        print(f"Synthetic review database: {Path(temp) / 'review.db'}", flush=True)
        try:
            d.app.run(host="127.0.0.1", port=5111, debug=False, use_reloader=False, threaded=True)
        finally:
            d.SessionLocal.remove()
            d.engine.dispose()


if __name__ == "__main__":
    main()
