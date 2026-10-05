from contextlib import closing, redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from football_reference import (ReferenceClient, ReferenceError, counts, inspect_reference,
                                sync_reference, validate)
from scripts.sync_football_reference import main


def season_data(season_id=23614, future=False):
    metadata = dict(id=season_id, league_id=8, name="2026/2027" if future else "2024/2025",
                    finished=not future, is_current=future)
    fixtures = []
    # Complete 20-club double round robin, 380 unique provider fixture IDs.
    for home in range(1, 21):
        for away in range(1, 21):
            if home == away:
                continue
            fid = season_id * 1000 + len(fixtures)
            fixtures.append(dict(id=fid, season_id=season_id, league_id=8,
                starting_at="2026-08-01 15:00:00", state_id=1 if future else 5,
                participants=[dict(id=home, name=f"Club {home}", meta=dict(location="home")),
                              dict(id=away, name=f"Club {away}", meta=dict(location="away"))],
                scores=[] if future else [dict(participant_id=home, description="CURRENT", score=dict(participant="home", goals=1)),
                                         dict(participant_id=away, description="CURRENT", score=dict(participant="away", goals=0))],
                events=[] if future else [dict(id=fid*100, fixture_id=fid, type_id=14, participant_id=home,
                    player_id=home*10, player_name="Player", related_player_id=None,
                    minute=30, extra_minute=None, sort_order=1, result="1-0", rescinded=False)]))
    return metadata, fixtures


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "local.db"
        with closing(sqlite3.connect(self.path)) as db:
            # Sentinels cover game and archived state, including an unrelated table.
            for name in ("seasons", "weeks", "fixtures", "results", "picks", "matchups", "standings", "payouts", "archived_year1"):
                db.execute(f"CREATE TABLE {name}(id INTEGER PRIMARY KEY, state TEXT)")
                db.execute(f"INSERT INTO {name} VALUES(1,'protected')")
            db.commit()
        self.metadata, self.fixtures = season_data()
        self.client = Mock()
        self.client.season.side_effect = lambda sid: deepcopy(self.metadata)
        self.client.fixtures.side_effect = lambda sid: deepcopy(self.fixtures)

    def sync(self, **kwargs):
        return sync_reference(database=self.path, season_id=self.metadata["id"], client=self.client, **kwargs)

    def query(self, sql):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute(sql).fetchall()

    def test_completed_validation_requires_380_distinct_final_fixtures(self):
        self.assertEqual(len(validate(23614, self.metadata, self.fixtures)[1]), 380)
        for rows in (self.fixtures[:-1], self.fixtures + self.fixtures[:1], self.fixtures[:-1] + self.fixtures[:1]):
            with self.subTest(length=len(rows)), self.assertRaises(ReferenceError):
                validate(23614, self.metadata, rows)
        self.fixtures[0]["state_id"] = 2
        with self.assertRaises(ReferenceError):
            self.sync()

    def test_current_future_then_completed_resync(self):
        self.metadata, self.fixtures = season_data(28083, future=True)
        report = self.sync()
        self.assertEqual((report["scored_fixture_count"], report["total_event_count"]), (0, 0))
        _, scored = season_data(28083)
        self.fixtures[0] = scored[0]
        self.assertEqual(self.sync()["scored_fixture_count"], 1)
        self.assertEqual(self.query("SELECT count(*) FROM football_reference_fixtures")[0][0], 380)

    def test_payload_failures_leave_db_byte_identical(self):
        before = self.path.read_bytes()
        mutations = [lambda f: f.update(league_id=9), lambda f: f.update(season_id=25583),
                     lambda f: f.update(starting_at="bad"), lambda f: f.update(starting_at="2026-08-01"),
                     lambda f: f.update(participants=f["participants"][:1]),
                     lambda f: f["participants"][1]["meta"].update(location="home"),
                     lambda f: f.update(events=None), lambda f: f.pop("events"),
                     lambda f: f.update(scores=[]), lambda f: f["scores"][0]["score"].update(goals=-1),
                     lambda f: f["scores"][0]["score"].update(goals="1"),
                     lambda f: f["scores"][0].update(participant_id=999),
                     lambda f: f["events"][0].update(fixture_id=999)]
        original = deepcopy(self.fixtures[0])
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.fixtures[0] = deepcopy(original)
                mutate(self.fixtures[0])
                with self.assertRaises(ReferenceError):
                    self.sync()
                self.assertEqual(self.path.read_bytes(), before)

    def test_zero_event_completed_fixture_is_valid(self):
        self.fixtures[0]["events"] = []
        self.assertEqual(self.sync()["fixtures_with_events"], 379)

    def test_idempotent_and_event_corrections_with_unknown_types(self):
        self.fixtures[0]["events"][0]["type_id"] = 99999
        self.sync()
        before = self.query("SELECT id, external_fixture_id, created_at FROM football_reference_fixtures ORDER BY id")
        self.fixtures[0]["events"][0].update(type_id=17, player_name="Corrected", minute=32)
        self.sync()
        self.assertEqual(before, self.query("SELECT id, external_fixture_id, created_at FROM football_reference_fixtures ORDER BY id"))
        self.assertEqual(self.query("SELECT count(*) FROM football_reference_events")[0][0], 380)
        self.assertEqual(self.query("SELECT event_type,player_name,minute FROM football_reference_events ORDER BY id LIMIT 1")[0],
                         ("missed_penalty", "Corrected", 32))

    def test_unknown_event_retained_and_not_a_goal(self):
        self.fixtures[0]["events"][0]["type_id"] = 99999
        report = self.sync()
        self.assertEqual(report["goal_event_count"], 379)
        self.assertEqual(self.query("SELECT type_id,event_type FROM football_reference_events ORDER BY id LIMIT 1")[0], (99999, "unknown"))

    def test_rescinded_removed_and_reactivated_events(self):
        self.sync()
        original = deepcopy(self.fixtures[0]["events"])
        self.fixtures[0]["events"][0]["rescinded"] = True
        self.sync()
        self.assertEqual(self.query("SELECT rescinded,is_active FROM football_reference_events ORDER BY id LIMIT 1")[0], (1, 0))
        self.fixtures[0]["events"] = []
        self.sync()
        report = inspect_reference(self.path)[0]
        self.assertEqual((report["total_event_count"], report["inactive_event_count"]), (379, 1))
        self.fixtures[0]["events"] = original
        self.sync()
        self.assertEqual(inspect_reference(self.path)[0]["goal_event_count"], 380)

    def test_nullable_rescinded_and_raw_audit_counts(self):
        self.fixtures[0]["events"][0]["rescinded"] = None
        self.fixtures[1]["events"][0].update(type_id=19, rescinded=True, addition="First card")
        report = self.sync()
        self.assertEqual(report["total_event_count"], 380)
        self.assertEqual(report["active_event_count"], 379)
        self.assertEqual(report["rescinded_event_count"], 1)
        self.assertEqual(self.query("SELECT rescinded,is_active FROM football_reference_events ORDER BY id LIMIT 1")[0], (None, 1))
        self.assertEqual(self.query("SELECT addition FROM football_reference_events WHERE type_id=19")[0][0], "First card")

    def test_duplicate_event_ids_and_fixture_identity_change_rejected(self):
        self.sync()
        before = inspect_reference(self.path)
        eid = self.fixtures[1]["events"][0]["id"]
        self.fixtures[1]["events"][0]["id"] = self.fixtures[0]["events"][0]["id"]
        with self.assertRaises(ReferenceError):
            self.sync()
        self.fixtures[1]["events"][0]["id"] = eid
        self.fixtures[0]["id"] += 999999
        self.fixtures[0]["events"][0]["fixture_id"] = self.fixtures[0]["id"]
        with self.assertRaises(ReferenceError):
            self.sync()
        self.assertEqual(inspect_reference(self.path), before)

    def test_both_historical_seasons_can_coexist(self):
        self.sync()
        self.metadata, self.fixtures = season_data(25583)
        self.sync()
        self.assertEqual([r["season_id"] for r in inspect_reference(self.path)], [23614, 25583])
        self.assertEqual(self.query("SELECT count(*) FROM football_reference_fixtures")[0][0], 760)

    def test_late_page_failure_does_not_open_database(self):
        def opener(req, timeout):
            if "/seasons/" in req.full_url:
                return io.BytesIO(json.dumps(dict(data=self.metadata)).encode())
            page = int(parse_qs(urlparse(req.full_url).query)["page"][0])
            if page == 2:
                raise OSError("secret must not escape")
            return io.BytesIO(json.dumps(dict(data=self.fixtures[:50], pagination=dict(current_page=1, has_more=True))).encode())
        with patch("football_reference.sqlite3.connect", side_effect=AssertionError("DB opened")), self.assertRaises(ReferenceError):
            sync_reference(database=self.path, season_id=23614, client=ReferenceClient(token="secret", opener=opener))

    def test_network_or_validation_failure_preserves_good_snapshot(self):
        self.sync()
        before = self.path.read_bytes()
        self.client.fixtures.side_effect = ReferenceError("Provider unavailable")
        with self.assertRaises(ReferenceError):
            self.sync()
        self.assertEqual(self.path.read_bytes(), before)
        self.client.fixtures.side_effect = lambda sid: self.fixtures[:-1]
        with self.assertRaises(ReferenceError):
            self.sync()
        self.assertEqual(self.path.read_bytes(), before)

    def test_mid_transaction_failure_rolls_back_all_changes(self):
        self.sync()
        before = inspect_reference(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER reject_event BEFORE UPDATE ON football_reference_events BEGIN SELECT RAISE(ABORT, 'injected'); END")
            db.commit()
        with self.assertRaises(ReferenceError):
            self.sync()
        self.assertEqual(inspect_reference(self.path), before)
        self.assertEqual(self.query("SELECT count(*) FROM football_reference_syncs")[0][0], 1)

    def test_first_import_failure_rolls_back_schema_creation(self):
        before = self.path.read_bytes()
        with patch("football_reference.upsert", side_effect=RuntimeError("injected")), self.assertRaises(ReferenceError):
            self.sync()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(inspect_reference(self.path), [])

    def test_changed_score_and_event_identity_collision_roll_back(self):
        self.sync()
        self.fixtures[0]["scores"][0]["score"]["goals"] = 3
        self.sync()
        self.assertEqual(self.query("SELECT home_score FROM football_reference_fixtures ORDER BY id LIMIT 1")[0][0], 3)
        # Moving an event to another fixture must not silently steal its identity.
        event = self.fixtures[0]["events"].pop()
        event["fixture_id"] = self.fixtures[1]["id"]
        self.fixtures[1]["events"].append(event)
        before = inspect_reference(self.path)
        with self.assertRaises(ReferenceError):
            self.sync()
        self.assertEqual(inspect_reference(self.path), before)

    def test_all_game_tables_unchanged_and_trigger_writes_blocked(self):
        names = [row[0] for row in self.query("SELECT name FROM sqlite_master WHERE type='table'")]
        before = {name: self.query(f"SELECT * FROM {name}") for name in names}
        self.sync()
        self.assertEqual(before, {name: self.query(f"SELECT * FROM {name}") for name in names})
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER bad_trigger AFTER UPDATE ON football_reference_seasons BEGIN UPDATE results SET state='changed'; END")
            db.commit()
        with self.assertRaises(ReferenceError):
            self.sync()
        self.assertEqual(before, {name: self.query(f"SELECT * FROM {name}") for name in names})

    def test_dry_run_no_db_writes_or_creation(self):
        before = self.path.read_bytes()
        self.assertTrue(self.sync(dry_run=True)["dry_run"])
        self.assertEqual(self.path.read_bytes(), before)
        missing = self.path.parent / "missing.db"
        sync_reference(database=missing, season_id=23614, client=self.client, dry_run=True)
        self.assertFalse(missing.exists())

    def test_explicit_db_and_supported_season_required_before_network(self):
        for path in (None, self.path.parent / "missing.db"):
            with self.assertRaises(ReferenceError):
                sync_reference(database=path, season_id=23614, client=self.client)
        with self.assertRaises(ReferenceError):
            sync_reference(database=self.path, season_id=123, client=self.client)
        self.client.season.assert_not_called()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--season", "23614"], client=self.client)

    def test_inspection_deterministic_and_read_only(self):
        report = self.sync()
        before = self.path.read_bytes()
        stored = inspect_reference(self.path)[0]
        for key, value in report.items():
            if key != "dry_run":
                self.assertEqual(stored[key], value)
        self.assertEqual(stored["event_type_distribution"], {"14": 380})
        self.assertEqual(stored["goal_events_with_player_identity"], 380)
        self.assertEqual(inspect_reference(self.path), inspect_reference(self.path))
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_dry_run_reports_without_opening_sqlite(self):
        with patch("football_reference.sqlite3.connect", side_effect=AssertionError("DB opened")), redirect_stdout(io.StringIO()) as out:
            main(["--season", "23614", "--dry-run"], client=self.client)
        self.assertEqual(json.loads(out.getvalue())["fixture_count"], 380)


class ClientTests(unittest.TestCase):
    def response(self, payload):
        return io.BytesIO(json.dumps(payload).encode())

    def test_pagination_and_request_contract(self):
        _, fixtures = season_data()
        requests = []
        def opener(req, timeout):
            requests.append(req)
            query = parse_qs(urlparse(req.full_url).query)
            page = int(query["page"][0])
            return self.response(dict(data=fixtures[(page-1)*50:page*50],
                pagination=dict(current_page=page, has_more=page<8)))
        client = ReferenceClient(token="secret-test", opener=opener)
        self.assertEqual(len(client.fixtures(23614)), 380)
        self.assertEqual(len(requests), 8)
        query = parse_qs(urlparse(requests[0].full_url).query)
        self.assertEqual(query["filters"], ["fixtureSeasons:23614"])
        self.assertEqual(query["include"], ["participants;scores;events"])
        self.assertNotIn("secret-test", requests[0].full_url)
        self.assertEqual(requests[0].get_header("Authorization"), "secret-test")

    def test_bad_pagination_and_network_errors_are_safe(self):
        for pagination in (None, dict(current_page=2, has_more=False), dict(current_page=1, has_more="false"), dict(current_page=1, has_more=True)):
            with self.subTest(pagination=pagination), self.assertRaises(ReferenceError):
                ReferenceClient(token="secret", opener=lambda *a, **k: self.response(dict(data=[], pagination=pagination))).fixtures(23614)
        client = ReferenceClient(token="secret", opener=Mock(side_effect=HTTPError("https://secret", 429, "secret", {"Retry-After": "120"}, None)))
        with self.assertRaises(ReferenceError) as caught:
            client.fixtures(23614)
        self.assertEqual(caught.exception.retry_after, 120)
        self.assertNotIn("secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
