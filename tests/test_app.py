import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEST_DIR = tempfile.TemporaryDirectory()
os.environ["DB_PATH"] = f"sqlite:///{Path(TEST_DIR.name) / 'test.db'}"
os.environ["INIT_ON_START"] = "0"

import pickem_flask_htmx_tabs as app_module  # noqa: E402


class FakeResponse:
    def __init__(self, payload, headers=None):
        import json

        self._body = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeFootballDataClient:
    def __init__(self, matches, remaining=9, reset=4):
        self.matches = matches
        self.requests_remaining = remaining
        self.reset_seconds = reset

    def competition_matches(self, competition, season_year):
        self.competition = competition
        self.season_year = season_year
        return self.matches


def complete_api_schedule():
    matches = []
    external_id = 100000
    for matchday in range(1, 39):
        for game in range(1, 11):
            matches.append({
                "id": external_id,
                "matchday": matchday,
                "utcDate": f"2026-08-{((matchday - 1) % 28) + 1:02d}T{game + 9:02d}:00:00Z",
                "status": "TIMED",
                "homeTeam": {"shortName": f"Home {matchday}-{game}"},
                "awayTeam": {"shortName": f"Away {matchday}-{game}"},
                "score": {"fullTime": {"home": None, "away": None}},
            })
            external_id += 1
    return matches


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

    def finalize_one_sided_matchup(self):
        db = app_module.SessionLocal()
        week = db.query(app_module.Week).filter_by(number=1).one()
        matchup = db.query(app_module.Matchup).filter_by(week_id=week.id).first()
        player_a = db.get(app_module.Player, matchup.player_a_id)
        player_b = db.get(app_module.Player, matchup.player_b_id)
        fixtures = db.query(app_module.Fixture).filter_by(week_id=week.id).order_by(
            app_module.Fixture.match_number
        ).all()
        for index, fixture in enumerate(fixtures):
            picker = player_a if index % 2 == 0 else player_b
            picked_team = fixture.home if picker.id == player_a.id else fixture.away
            db.add(app_module.Pick(
                matchup_id=matchup.id,
                player_id=picker.id,
                fixture_id=fixture.id,
                team=picked_team,
            ))
            db.add(app_module.Result(
                fixture_id=fixture.id,
                outcome="Home",
                source="manual",
            ))
        week.status = "finalized"
        db.commit()
        return db, week, matchup, player_a, player_b

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
        self.assertIn(b"Football-Data.org API", response.data)

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

    def test_scores_panel_refreshes_and_shows_record_breakdown(self):
        db = app_module.SessionLocal()
        week = db.query(app_module.Week).filter_by(number=1).one()
        matchup = db.query(app_module.Matchup).filter_by(week_id=week.id).first()
        player = db.get(app_module.Player, matchup.player_a_id)
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
                player_id=player.id,
                fixture_id=fixture.id,
                team=team,
            ))
            db.add(app_module.Result(fixture_id=fixture.id, outcome=outcome))
        db.commit()

        with app_module.app.test_client() as client:
            response = client.get("/partials/scores/1")

        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(html.count('id="scores"'), 1)
        self.assertIn("Games Finalized", html)
        self.assertIn(f"<td>{player.name}</td><td>0</td><td>3</td>", html)

    def test_result_submission_returns_replaceable_scores_panel(self):
        db = app_module.SessionLocal()
        fixture = db.query(app_module.Fixture).order_by(app_module.Fixture.match_number).first()

        with app_module.app.test_client() as client:
            response = client.post("/set_result", data={
                "week": 1,
                "fixture_id": fixture.id,
                "outcome": fixture.home,
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'id="scores"'), 1)
        self.assertIn(fixture.home.encode(), response.data)

    def test_season_page_has_centered_net_breakdown(self):
        db = app_module.SessionLocal()
        week = db.query(app_module.Week).filter_by(number=1).one()
        week.status = "finalized"
        db.commit()

        with app_module.app.test_client() as client:
            with client.session_transaction() as user_session:
                user_session["player_name"] = "Steve"
            response = client.get("/tab/season")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="centered-table"', response.data)
        self.assertIn(b'class="centered-table season-summary-table"', response.data)
        self.assertIn(b"For Net", response.data)
        self.assertIn(b"Against Net", response.data)
        self.assertIn(b"Total Net", response.data)
        html = response.get_data(as_text=True)
        ordered_headers = [
            "Correct", "Incorrect", "Draws", "For Net",
            "Against Correct", "Against Incorrect", "Against Draws", "Against Net",
            "Total Net",
        ]
        header_positions = [html.index(f">{header}</th>") for header in ordered_headers]
        self.assertEqual(header_positions, sorted(header_positions))
        self.assertEqual(html.count('class="for-header"'), 4)
        self.assertIn('class="against-header against-start"', html)
        self.assertEqual(html.count('class="against-header"'), 3)
        self.assertIn('class="total-net-header"', html)
        self.assertIn('class="total-net-cell"', html)

    def test_season_leaders_identify_perfect_week_and_biggest_win(self):
        db, week, matchup, player_a, player_b = self.finalize_one_sided_matchup()
        season = db.query(app_module.Season).filter_by(code="year-2").one()

        leaders = {
            row["label"]: row for row in app_module.season_leader_stats(db, season)
        }

        self.assertEqual(leaders["Biggest weekly win"]["value"], player_a.name)
        self.assertIn("+10", leaders["Biggest weekly win"]["detail"])
        self.assertEqual(leaders["Most correct picks"]["value"], player_a.name)
        self.assertEqual(leaders["Most correct picks"]["detail"], "5 correct")
        self.assertEqual(leaders["Most incorrect picks"]["value"], player_b.name)
        self.assertEqual(leaders["Most incorrect picks"]["detail"], "5 incorrect")
        self.assertEqual(leaders["Most perfect weeks"]["value"], player_a.name)
        self.assertEqual(leaders["Longest win streak"]["detail"], "1 week")
        self.assertEqual(leaders["Longest losing streak"]["value"], player_b.name)
        self.assertEqual(leaders["Longest losing streak"]["detail"], "1 week")
        self.assertIn("100.0% · 1 correct of 1 decided", leaders["Highest club correct %"]["detail"])
        self.assertIn("0.0% · 1 incorrect of 1 decided", leaders["Lowest club correct %"]["detail"])

    def test_head_to_head_and_club_records_use_finalized_weeks(self):
        db, week, matchup, player_a, player_b = self.finalize_one_sided_matchup()
        season = db.query(app_module.Season).filter_by(code="year-2").one()

        head_to_head = app_module.head_to_head_for_player(db, season, player_a)
        club_records = app_module.club_records_for_player(db, season, player_a)

        self.assertEqual(len(head_to_head), 1)
        self.assertEqual(head_to_head[0]["opponent"], player_b.name)
        self.assertEqual(
            (head_to_head[0]["wins"], head_to_head[0]["ties"], head_to_head[0]["losses"]),
            (1, 0, 0),
        )
        self.assertEqual(
            (
                head_to_head[0]["against_correct"],
                head_to_head[0]["against_incorrect"],
                head_to_head[0]["against_draws"],
                head_to_head[0]["net_against"],
            ),
            (0, 5, 0, -5),
        )
        self.assertEqual(head_to_head[0]["money_display"], "+$50")
        self.assertEqual(len(club_records), 5)
        self.assertTrue(all(row["net"] == 1 for row in club_records))
        self.assertTrue(all(row["accuracy_display"] == "100.0%" for row in club_records))

        season_club_records = app_module.season_club_records(db, season)
        self.assertEqual(sum(row["correct"] for row in season_club_records), 5)
        self.assertEqual(sum(row["incorrect"] for row in season_club_records), 5)

    def test_stats_tab_and_current_week_leader_card_render(self):
        db, week, matchup, player_a, player_b = self.finalize_one_sided_matchup()
        player_a_id = player_a.id
        player_a_name = player_a.name
        with app_module.app.test_client() as client:
            with client.session_transaction() as user_session:
                user_session["player_name"] = player_a_name
            stats_response = client.get(f"/tab/stats?player={player_a_id}")
            current_response = client.get("/tab/current")

        self.assertEqual(stats_response.status_code, 200)
        self.assertIn(b"Head-to-Head", stats_response.data)
        self.assertIn(b"Club-Picking Record", stats_response.data)
        self.assertIn(b"Against Correct", stats_response.data)
        self.assertIn(b"Against Incorrect", stats_response.data)
        self.assertIn(b"Against Draws", stats_response.data)
        self.assertIn(b"Highest club correct %", stats_response.data)
        self.assertIn(b"Lowest club correct %", stats_response.data)
        self.assertIn(b"+$50", stats_response.data)
        self.assertEqual(current_response.status_code, 200)
        self.assertIn(b"Season Leaders", current_response.data)
        self.assertIn(b"Most incorrect picks", current_response.data)
        self.assertIn(b"Longest losing streak", current_response.data)
        self.assertIn(b"View all stats", current_response.data)
        self.assertIn(player_a_name.encode(), current_response.data)

    def test_duplicate_week_numbers_are_isolated_by_season(self):
        db = app_module.SessionLocal()
        year_two = db.query(app_module.Season).filter_by(code="year-2").one()
        year_one = app_module.Season(
            code="year-1", name="Year 1", is_active=0, is_archived=1
        )
        db.add(year_one)
        db.flush()
        db.add(app_module.Week(
            season_id=year_one.id,
            number=1,
            room_code="ARCHIVE",
            status="finalized",
        ))
        db.commit()

        weeks = db.query(app_module.Week).filter_by(number=1).all()
        self.assertEqual(len(weeks), 2)
        self.assertEqual({week.season_id for week in weeks}, {year_one.id, year_two.id})

        with app_module.app.test_client() as client:
            with client.session_transaction() as user_session:
                user_session["player_name"] = "Steve"
            season_response = client.get("/tab/season?season=year-1")
            week_response = client.get("/tab/current?season=year-1&force_week=1")

        self.assertIn(b"Year 1 Summary", season_response.data)
        self.assertIn(b"Year 1 (Archived)", season_response.data)
        self.assertIn(b"Year 2", season_response.data)
        self.assertIn("Archived — read only".encode(), week_response.data)

    def test_existing_season_cannot_be_reinitialized_without_explicit_reset(self):
        with self.assertRaises(RuntimeError):
            app_module.init_weeks_from_csv(
                str(self.csv_path),
                [1],
                ["Steve", "Joe", "Marc", "Drew", "Scott", "Connor"],
                "LOCALTEST",
                season_code="year-2",
                season_name="Year 2",
            )

    def test_archived_season_rejects_result_changes(self):
        db = app_module.SessionLocal()
        archived = app_module.Season(
            code="year-1", name="Year 1", is_active=0, is_archived=1
        )
        db.add(archived)
        db.flush()
        week = app_module.Week(
            season_id=archived.id,
            number=1,
            room_code="ARCHIVE",
            status="finalized",
        )
        db.add(week)
        db.flush()
        fixture = app_module.Fixture(
            week_id=week.id,
            match_number=1,
            home="Arsenal",
            away="Chelsea",
        )
        db.add(fixture)
        db.commit()

        with app_module.app.test_client() as client:
            response = client.post("/set_result", data={
                "season": "year-1",
                "week": 1,
                "fixture_id": fixture.id,
                "outcome": fixture.home,
            })

        self.assertEqual(response.status_code, 403)
        self.assertEqual(db.query(app_module.Result).filter_by(fixture_id=fixture.id).count(), 0)

    def test_legacy_database_migrates_without_losing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE players (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE);
                CREATE TABLE weeks (
                    id INTEGER PRIMARY KEY,
                    number INTEGER NOT NULL UNIQUE,
                    room_code VARCHAR NOT NULL,
                    status VARCHAR
                );
                CREATE TABLE fixtures (
                    id INTEGER PRIMARY KEY,
                    week_id INTEGER NOT NULL REFERENCES weeks(id),
                    match_number INTEGER NOT NULL,
                    home VARCHAR NOT NULL,
                    away VARCHAR NOT NULL,
                    UNIQUE (week_id, match_number)
                );
                CREATE TABLE matchups (
                    id INTEGER PRIMARY KEY,
                    week_id INTEGER NOT NULL REFERENCES weeks(id),
                    player_a_id INTEGER NOT NULL REFERENCES players(id),
                    player_b_id INTEGER NOT NULL REFERENCES players(id),
                    first_picker_id INTEGER NOT NULL REFERENCES players(id)
                );
                CREATE TABLE picks (
                    id INTEGER PRIMARY KEY,
                    matchup_id INTEGER NOT NULL REFERENCES matchups(id),
                    player_id INTEGER NOT NULL REFERENCES players(id),
                    fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
                    team VARCHAR NOT NULL,
                    created_at DATETIME,
                    UNIQUE (matchup_id, fixture_id)
                );
                CREATE TABLE results (
                    id INTEGER PRIMARY KEY,
                    fixture_id INTEGER NOT NULL UNIQUE REFERENCES fixtures(id),
                    outcome VARCHAR NOT NULL
                );
                INSERT INTO players VALUES (1, 'Steve'), (2, 'Joe');
                INSERT INTO weeks VALUES (10, 1, 'YEAR1', 'finalized');
                INSERT INTO fixtures VALUES (20, 10, 1, 'Arsenal', 'Chelsea');
                INSERT INTO matchups VALUES (30, 10, 1, 2, 1);
                INSERT INTO picks VALUES (40, 30, 1, 20, 'Arsenal', '2025-08-17 12:00:00');
                INSERT INTO results VALUES (50, 20, 'Home');
                """
            )
            connection.commit()
            connection.close()

            legacy_engine = app_module.create_engine(
                f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
            )
            self.assertTrue(app_module.ensure_database_schema(legacy_engine))
            legacy_engine.dispose()

            connection = sqlite3.connect(db_path)
            season = connection.execute(
                "SELECT id, name, is_archived FROM seasons WHERE code='year-1'"
            ).fetchone()
            migrated_week = connection.execute(
                "SELECT id, season_id, number FROM weeks WHERE id=10"
            ).fetchone()
            connection.execute(
                "INSERT INTO seasons (code, name, is_active, is_archived) VALUES ('year-2', 'Year 2', 1, 0)"
            )
            year_two_id = connection.execute(
                "SELECT id FROM seasons WHERE code='year-2'"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO weeks (season_id, number, room_code, status) VALUES (?, 1, 'YEAR2', 'drafting')",
                (year_two_id,),
            )
            connection.commit()

            self.assertEqual(season[1:], ("Year 1", 1))
            self.assertEqual(migrated_week, (10, season[0], 1))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM picks").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM results").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT source FROM results WHERE id=50").fetchone()[0],
                "manual",
            )
            fixture_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(fixtures)").fetchall()
            }
            self.assertIn("external_match_id", fixture_columns)
            self.assertIn("kickoff_utc", fixture_columns)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM weeks WHERE number=1").fetchone()[0], 2)
            connection.close()

            self.assertTrue((Path(directory) / "legacy.pre_seasons.db").exists())
            self.assertTrue((Path(directory) / "legacy.pre_football_api.db").exists())

    def test_api_client_obeys_rate_limit_response_headers(self):
        responses = [
            FakeResponse(
                {"matches": []},
                {"X-Requests-Available-Minute": "0", "X-RequestCounter-Reset": "2"},
            ),
            FakeResponse(
                {"matches": []},
                {"X-Requests-Available-Minute": "9", "X-RequestCounter-Reset": "58"},
            ),
        ]
        clock = {"now": 100.0}
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock["now"] += seconds

        client = app_module.FootballDataClient(
            "test-token",
            opener=lambda request, timeout: responses.pop(0),
            sleeper=fake_sleep,
            monotonic=lambda: clock["now"],
        )
        client.competition_matches("PL", 2026)
        client.competition_matches("PL", 2026)

        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], 2)
        self.assertEqual(client.requests_remaining, 9)
        self.assertEqual(client.reset_seconds, 58)

    def test_admin_renders_api_controls_without_exposing_a_key(self):
        with app_module.app.test_client() as client:
            with client.session_transaction() as admin_session:
                admin_session[app_module.ADMIN_SESSION_KEY] = True
            response = client.get("/admin")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Premier League Data", response.data)
        self.assertIn(b"Sync Final Scores", response.data)
        self.assertNotIn(b"test-token", response.data)

    def test_scheduled_sync_requires_secret_and_returns_summary(self):
        os.environ["SYNC_SECRET"] = "scheduler-secret"
        summary = {
            "results_imported": 2,
            "pending_matches": 378,
            "unmatched_matches": 0,
            "manual_overrides": 0,
        }
        try:
            with app_module.app.test_client() as client, patch.object(
                app_module, "sync_results_from_api", return_value=summary
            ) as sync_mock:
                missing = client.post("/tasks/sync-results")
                wrong = client.post(
                    "/tasks/sync-results", headers={"X-Sync-Secret": "wrong"}
                )
                accepted = client.post(
                    "/tasks/sync-results",
                    headers={"X-Sync-Secret": "scheduler-secret"},
                )
        finally:
            os.environ.pop("SYNC_SECRET", None)

        self.assertEqual(missing.status_code, 403)
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()["results_imported"], 2)
        sync_mock.assert_called_once()

    def test_scheduled_sync_is_disabled_without_server_secret(self):
        os.environ.pop("SYNC_SECRET", None)
        with app_module.app.test_client() as client:
            response = client.post("/tasks/sync-results")
        self.assertEqual(response.status_code, 503)

    def test_api_fixture_import_creates_complete_year_two(self):
        db = app_module.SessionLocal()
        existing = db.query(app_module.Season).filter_by(code="year-2").one()
        app_module._delete_season_weeks(db, existing)
        db.delete(existing)
        db.commit()

        fake_client = FakeFootballDataClient(complete_api_schedule(), remaining=8, reset=12)
        season = app_module.init_season_from_api(
            ["Steve", "Joe", "Marc", "Drew", "Scott", "Connor"],
            "LOCALTEST",
            client=fake_client,
        )

        self.assertEqual(season.name, "2026–27")
        self.assertEqual(season.api_competition_code, "PL")
        self.assertEqual(season.api_season_year, 2026)
        self.assertEqual(db.query(app_module.Week).filter_by(season_id=season.id).count(), 38)
        self.assertEqual(
            db.query(app_module.Fixture).join(app_module.Week).filter(
                app_module.Week.season_id == season.id
            ).count(),
            380,
        )
        self.assertEqual(
            db.query(app_module.Matchup).join(app_module.Week).filter(
                app_module.Week.season_id == season.id
            ).count(),
            114,
        )
        state = db.query(app_module.ApiSyncState).filter_by(season_id=season.id).one()
        self.assertEqual(state.fixtures_imported, 380)
        self.assertEqual(state.requests_remaining, 8)

    def test_api_score_sync_preserves_manual_override(self):
        db = app_module.SessionLocal()
        season = db.query(app_module.Season).filter_by(code="year-2").one()
        season.api_competition_code = "PL"
        season.api_season_year = 2026
        fixtures = db.query(app_module.Fixture).join(app_module.Week).filter(
            app_module.Week.season_id == season.id
        ).order_by(app_module.Fixture.match_number).all()
        for index, fixture in enumerate(fixtures):
            fixture.external_match_id = 200000 + index
        manual_fixture = fixtures[1]
        db.add(app_module.Result(
            fixture_id=manual_fixture.id,
            outcome="Draw",
            source="manual",
        ))
        db.commit()

        matches = []
        for index, fixture in enumerate(fixtures):
            finished = index < 2
            matches.append({
                "id": fixture.external_match_id,
                "matchday": 1,
                "utcDate": "2026-08-15T14:00:00Z",
                "status": "FINISHED" if finished else "TIMED",
                "homeTeam": {"shortName": fixture.home},
                "awayTeam": {"shortName": fixture.away},
                "score": {"fullTime": {
                    "home": 2 if index == 0 else (0 if finished else None),
                    "away": 1 if index == 0 else (1 if finished else None),
                }},
            })

        summary = app_module.sync_results_from_api(
            season,
            client=FakeFootballDataClient(matches, remaining=7, reset=22),
        )
        api_result = db.query(app_module.Result).filter_by(fixture_id=fixtures[0].id).one()
        manual_result = db.query(app_module.Result).filter_by(
            fixture_id=manual_fixture.id
        ).one()

        self.assertEqual(summary["results_imported"], 1)
        self.assertEqual(summary["manual_overrides"], 1)
        self.assertEqual(api_result.outcome, "Home")
        self.assertEqual((api_result.home_score, api_result.away_score), (2, 1))
        self.assertEqual(api_result.source, app_module.FOOTBALL_DATA_PROVIDER)
        self.assertEqual(manual_result.outcome, "Draw")
        self.assertEqual(manual_result.source, "manual")


if __name__ == "__main__":
    unittest.main()
