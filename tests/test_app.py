import os
import tempfile
import unittest
from pathlib import Path


TEST_DIR = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = f"sqlite:///{Path(TEST_DIR.name) / 'test.db'}"
os.environ["INIT_ON_START"] = "0"

import pickem_flask_htmx_tabs as app_module  # noqa: E402


class PickemAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.csv_path = Path(__file__).resolve().parents[1] / "epl_2025.csv"
        app_module.app.config.update(TESTING=True)

    @classmethod
    def tearDownClass(cls):
        # Windows will not delete the temporary SQLite file while SQLAlchemy
        # still has a pooled connection open.
        app_module.SessionLocal.remove()
        app_module.engine.dispose()
        TEST_DIR.cleanup()
        super().tearDownClass()

    def setUp(self):
        app_module.SessionLocal.remove()
        app_module.Base.metadata.drop_all(app_module.engine)
        app_module.Base.metadata.create_all(app_module.engine)
        app_module.init_weeks_from_csv(
            str(self.csv_path),
            [1],
            ["Steve", "Joe", "Marc", "Drew", "Scott", "Connor"],
            "LOCALTEST",
        )

    def tearDown(self):
        app_module.SessionLocal.remove()

    def test_snake_order_contains_back_to_back_turns(self):
        db = app_module.SessionLocal()
        matchup = db.query(app_module.Matchup).first()
        first, second = app_module.matchup_order(matchup)
        fixtures = db.query(app_module.Fixture).order_by(app_module.Fixture.match_number).all()

        expected = [first, second, second, first]
        for index, player_id in enumerate(expected):
            self.assertEqual(app_module.compute_next_turn(db, matchup), player_id)
            db.add(app_module.Pick(
                matchup_id=matchup.id,
                player_id=player_id,
                fixture_id=fixtures[index].id,
                team=fixtures[index].home,
            ))
            db.commit()

    def test_pick_response_keeps_matchups_refresh_target(self):
        db = app_module.SessionLocal()
        matchup = db.query(app_module.Matchup).first()
        first_id, _ = app_module.matchup_order(matchup)
        first = db.get(app_module.Player, first_id)
        fixture = db.query(app_module.Fixture).order_by(app_module.Fixture.match_number).first()

        with app_module.app.test_client() as client:
            with client.session_transaction() as user_session:
                user_session["player_name"] = first.name
            response = client.post("/pick", data={
                "week": 1,
                "matchup_id": matchup.id,
                "fixture_id": fixture.id,
                "team": fixture.home,
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'id="matchups"'), 1)
        self.assertIn(b'hx-trigger="confirmedPick"', response.data)

    def test_shell_renders_current_week(self):
        with app_module.app.test_client() as client:
            with client.session_transaction() as user_session:
                user_session["player_name"] = "Steve"
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Week 1", response.data)
        self.assertIn(b'id="matchups"', response.data)

    def test_pick_records_distinguish_correct_incorrect_and_draw(self):
        db = app_module.SessionLocal()
        week = db.query(app_module.Week).filter_by(number=1).one()
        matchup = db.query(app_module.Matchup).filter_by(week_id=week.id).first()
        player_id = matchup.player_a_id
        fixtures = db.query(app_module.Fixture).filter_by(week_id=week.id).order_by(
            app_module.Fixture.match_number
        ).all()

        for fixture, outcome, team in (
            (fixtures[0], "Home", fixtures[0].home),
            (fixtures[1], "Away", fixtures[1].home),
            (fixtures[2], "Draw", fixtures[2].home),
        ):
            db.add(app_module.Pick(
                matchup_id=matchup.id,
                player_id=player_id,
                fixture_id=fixture.id,
                team=team,
            ))
            db.add(app_module.Result(fixture_id=fixture.id, outcome=outcome))
        db.commit()

        record = app_module.weekly_pick_records(db, week)[player_id]
        self.assertEqual(record, {"correct": 1, "incorrect": 1, "draws": 1})


if __name__ == "__main__":
    unittest.main()
