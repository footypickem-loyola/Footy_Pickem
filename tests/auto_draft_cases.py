"""Focused cases mixed into the existing isolated-database app fixture."""
import threading
import json
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import text
from auto_draft import DraftService, DraftError


class AutoDraftCases:
    def bulk_edit(self, player, count=0, choices=None):
        if choices is None:
            choices = [{"fixture_id": f[0], "team": f[1]} for f in self.fx]
        return self.auto_edit(player, "confirm", expected_count=str(count), preferences=json.dumps(choices))

    def test_auto_bulk_confirmation_locks_persisted_list_but_allows_pause(self):
        self.auto_context()
        # An unconfirmed old-style priority is replaced by the reviewed bulk list.
        self.auto_add(self.a, 9)
        choices = [{"fixture_id": f[0], "team": f[2]} for f in reversed(self.fx)]
        result = self.bulk_edit(self.a, choices=choices)
        self.assertEqual(result["count"], 1)
        self.assertEqual(self.auto_picks()[0].fixture_id, self.fx[9][0])
        self.db.expire_all()
        setting = self.db.query(self.d.AutoDraftSetting).filter_by(matchup_id=self.mid, player_id=self.a).one()
        self.assertIsNotNone(setting.confirmed_at)
        self.assertEqual(setting.enabled, 1)
        rows = self.service.preferences(self.db, self.mid, self.a)
        self.assertEqual([p.fixture_id for p in rows], [f[0] for f in reversed(self.fx)])
        row_id = str(rows[0].id)
        for action in ("add", "remove", "up", "down", "confirm"):
            with self.subTest(action=action), self.assertRaises(DraftError) as error:
                self.auto_edit(self.a, action, preference_id=row_id)
            self.assertEqual(error.exception.status, 409)
        self.auto_edit(self.a, "toggle", enabled="0")
        self.db.expire_all()
        self.assertEqual(setting.enabled, 0)
        self.assertIsNotNone(setting.confirmed_at)
        self.assertEqual(len(self.service.preferences(self.db, self.mid, self.a)), 10)

    def test_auto_bulk_rejects_incomplete_duplicate_invalid_and_wrong_fixture(self):
        self.auto_context()
        valid = [{"fixture_id": f[0], "team": f[1]} for f in self.fx]
        invalid = [valid[:-1], valid + [valid[0]], [valid[0]] * 10,
                   [dict(valid[0], team="Draw")] + valid[1:],
                   [dict(valid[0], team="Not a team")] + valid[1:],
                   [dict(valid[0], fixture_id=999999)] + valid[1:],
                   [dict(valid[0], fixture_id=True)] + valid[1:], {}, None]
        for choices in invalid:
            with self.subTest(choices=choices), self.assertRaises(DraftError):
                self.bulk_edit(self.b, choices=choices if choices is not None else {})
        with self.assertRaises(DraftError):
            self.auto_edit(self.b, "confirm", expected_count="0", preferences="{broken")
        self.assertEqual(self.db.query(self.d.AutoDraftSetting).count(), 0)
        self.assertEqual(self.db.query(self.d.AutoDraftPreference).count(), 0)
        self.assertEqual(len(self.auto_picks()), 0)

    def test_auto_bulk_stale_review_rejected_then_remaining_list_can_confirm(self):
        self.auto_context()
        self.service.command(self.mid, self.a, manual=self.fx[0][:2])
        with self.assertRaises(DraftError) as error:
            self.bulk_edit(self.b)
        self.assertEqual(error.exception.status, 409)
        remaining = [{"fixture_id": f[0], "team": f[1]} for f in self.fx[1:]]
        self.assertEqual(self.bulk_edit(self.b, count=1, choices=remaining)["count"], 2)
        self.assertEqual(len(self.auto_picks()), 3)
        self.assertEqual(len(self.service.preferences(self.db, self.mid, self.b)), 9)

    def test_auto_bulk_competing_confirmations_cannot_replace_locked_list(self):
        self.auto_context()
        barrier = threading.Barrier(2)
        def confirm(reverse):
            choices = [{"fixture_id": f[0], "team": f[1]} for f in self.fx]
            if reverse:
                choices.reverse()
            barrier.wait()
            try:
                self.bulk_edit(self.b, choices=choices)
                return choices
            except DraftError as error:
                self.assertEqual(error.status, 409)
                return None
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(confirm, [False, True]))
        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1)
        rows = self.service.preferences(self.db, self.mid, self.b)
        self.assertEqual([p.fixture_id for p in rows], [p["fixture_id"] for p in winners[0]])
        self.assertEqual(len(self.auto_picks()), 0)

    def test_auto_bulk_confirm_http_and_read_only_modal(self):
        self.auto_context()
        name = self.db.get(self.d.Player, self.b).name
        self.db.rollback()
        with self.d.app.test_client() as client:
            with client.session_transaction() as session:
                session["player_name"] = name
            page = client.get("/tab/current")
            self.assertIn(b"data-bulk-open", page.data)
            self.assertIn(b"Submit Bulk Picks", page.data)
            self.assertEqual(len(self.auto_picks()), 0)
            data = dict(action="confirm", expected_count="0", preferences=json.dumps(
                [{"fixture_id": f[0], "team": f[1]} for f in self.fx]))
            response = client.post(f"/auto-draft/{self.mid}", data=data, headers={"HX-Request": "true"})
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"View Bulk Picks", response.data)
            self.assertIn(b"List locked", response.data)
            self.assertNotIn(b"Submit Bulk Picks", response.data)
            response = client.post(f"/auto-draft/{self.mid}", data=data, headers={"HX-Request": "true"})
            self.assertEqual(response.status_code, 409)
            self.assertIn("cannot be edited", response.json["error"])
            self.assertEqual(len(self.auto_picks()), 0)

    def test_auto_bulk_confirm_archived_and_finalized_rejected(self):
        self.auto_context()
        week = self.db.get(self.d.Week, self.week_id)
        week.season.is_archived = 1
        self.db.commit()
        with self.assertRaises(DraftError):
            self.bulk_edit(self.a)
        week.season.is_archived = 0
        week.status = "finalized"
        self.db.commit()
        with self.assertRaises(DraftError):
            self.bulk_edit(self.a)
        self.assertEqual(self.db.query(self.d.AutoDraftSetting).count(), 0)

    def test_auto_bulk_migration_preserves_existing_unconfirmed_settings(self):
        self.auto_context()
        self.auto_add(self.b, 0)
        self.db.rollback()
        self.d.AutoDraftSetting.__table__.drop(self.d.engine)
        with self.d.engine.begin() as connection:
            connection.execute(text("CREATE TABLE auto_draft_settings (id INTEGER PRIMARY KEY, "
                "matchup_id INTEGER NOT NULL, player_id INTEGER NOT NULL, enabled INTEGER NOT NULL, "
                "UNIQUE(matchup_id, player_id))"))
            connection.execute(text("INSERT INTO auto_draft_settings VALUES (1, :m, :p, 1)"),
                               {"m": self.mid, "p": self.b})
        self.assertFalse(self.d.ensure_database_schema(self.d.engine))
        self.assertFalse(self.d.ensure_database_schema(self.d.engine))
        setting = self.db.query(self.d.AutoDraftSetting).one()
        self.assertEqual(setting.enabled, 1)
        self.assertIsNone(setting.confirmed_at)
        self.assertEqual(len(self.service.preferences(self.db, self.mid, self.b)), 1)

    def test_auto_allowed_season_reset_removes_state_before_id_reuse(self):
        import pickem_flask_htmx_tabs as d
        from unittest.mock import patch

        db = d.SessionLocal()
        players = [p.name for p in db.query(d.Player).order_by(d.Player.name)]
        unrelated = db.query(d.Matchup).first()
        unrelated_id = unrelated.id
        unrelated_player = unrelated.first_picker_id
        unrelated_fixture = db.query(d.Fixture).filter_by(week_id=unrelated.week_id).first()
        db.add(d.AutoDraftSetting(matchup_id=unrelated_id, player_id=unrelated_player, enabled=1))
        db.add(d.AutoDraftPreference(matchup_id=unrelated_id, player_id=unrelated_player,
            fixture_id=unrelated_fixture.id, team=unrelated_fixture.home, priority=0))
        db.commit()

        # Create/reset the newest season so SQLite reuses its deleted IDs.
        # Stable pairing also reproduces the same matchup/player scopes.
        with patch.object(d.random, "shuffle", side_effect=lambda names: None), \
                patch.object(d.random, "choice", side_effect=lambda items: items[0]):
            season = d.init_weeks_from_csv(str(self.csv_path), [1], players, "RESETTEST",
                season_code="auto-reset", season_name="Auto reset")
            season_id = season.id
            matchups = db.query(d.Matchup).join(d.Week).filter(d.Week.season_id == season_id).all()
            old_matchup_ids = {m.id for m in matchups}
            for matchup in matchups:
                fixture = db.query(d.Fixture).filter_by(week_id=matchup.week_id).first()
                for player_id in (matchup.player_a_id, matchup.player_b_id):
                    db.add(d.AutoDraftSetting(matchup_id=matchup.id, player_id=player_id, enabled=1))
                    db.add(d.AutoDraftPreference(matchup_id=matchup.id, player_id=player_id,
                        fixture_id=fixture.id, team=fixture.home, priority=0))
            db.commit()
            self.assertEqual(db.query(d.AutoDraftSetting).filter(
                d.AutoDraftSetting.matchup_id.in_(old_matchup_ids)).count(), 6)
            self.assertEqual(db.query(d.AutoDraftPreference).filter(
                d.AutoDraftPreference.matchup_id.in_(old_matchup_ids)).count(), 6)
            db.expunge_all()

            # Exercise the real explicitly allowed reset-and-recreate entry point.
            d.init_weeks_from_csv(str(self.csv_path), [1], players, "RESETTEST",
                season_code="auto-reset", season_name="Auto reset", allow_reset=True)

        recreated = db.query(d.Matchup).join(d.Week).filter(d.Week.season_id == season_id).all()
        self.assertEqual({m.id for m in recreated}, old_matchup_ids)
        self.assertEqual(db.query(d.AutoDraftPreference).filter(
            d.AutoDraftPreference.matchup_id.in_(old_matchup_ids)).count(), 0)
        self.assertEqual(db.query(d.AutoDraftSetting).filter(
            d.AutoDraftSetting.matchup_id.in_(old_matchup_ids)).count(), 0)
        # Other seasons retain their persisted queue and enabled setting.
        self.assertEqual(db.query(d.AutoDraftPreference).count(), 1)
        self.assertEqual(db.query(d.AutoDraftSetting).count(), 1)
        self.assertEqual(db.query(d.AutoDraftSetting).filter_by(matchup_id=unrelated_id).one().enabled, 1)
        matchup_id, player_id = recreated[0].id, recreated[0].first_picker_id
        db.rollback()
        self.assertEqual(DraftService(d).command(matchup_id, player_id)["count"], 0)
        self.assertEqual(db.query(d.Pick).filter(d.Pick.matchup_id.in_(old_matchup_ids)).count(), 0)

    def auto_context(self):
        import pickem_flask_htmx_tabs as d
        self.d = d
        self.db = d.SessionLocal()
        self.addCleanup(self.db.close)
        m = self.db.query(d.Matchup).first()
        self.mid = m.id
        self.a, self.b = d.matchup_order(m)
        self.fx = [(f.id, f.home, f.away) for f in self.db.query(d.Fixture).filter_by(
            week_id=m.week_id).order_by(d.Fixture.id)]
        self.week_id = m.week_id
        self.db.rollback()
        self.service = DraftService(d)

    def auto_edit(self, player, action, **values):
        return self.service.command(self.mid, player, edit=dict(action=action, **values))

    def auto_add(self, player, index):
        f = self.fx[index]
        return self.auto_edit(player, "add", choice=f"{f[0]}:{f[1]}")

    def auto_picks(self):
        self.db.expire_all()
        return self.db.query(self.d.Pick).filter_by(matchup_id=self.mid).order_by(self.d.Pick.id).all()

    def test_auto_crud_order_scope_and_persistence(self):
        self.auto_context()
        for i in range(3):
            self.auto_add(self.a, i)
        self.assertEqual(len(self.auto_picks()), 0)
        for team in ("Draw", "Bogus"):
            with self.assertRaises(DraftError):
                self.auto_edit(self.a, "add", choice=f"{self.fx[3][0]}:{team}")
        with self.assertRaises(DraftError):
            self.auto_add(self.a, 0)
        rows = self.service.preferences(self.db, self.mid, self.a)
        ids = [r.id for r in rows]
        self.auto_edit(self.a, "up", preference_id=str(ids[2]))
        self.db.expire_all()
        self.assertEqual([r.id for r in self.service.preferences(self.db, self.mid, self.a)],
                         [ids[0], ids[2], ids[1]])
        self.auto_edit(self.a, "down", preference_id=str(ids[0]))
        self.auto_edit(self.a, "remove", preference_id=str(ids[1]))
        with self.assertRaises(DraftError):
            self.auto_edit(self.b, "remove", preference_id=str(ids[0]))
        other = self.db.query(self.d.Player).filter(self.d.Player.id.notin_([self.a, self.b])).first().id
        self.db.rollback()
        with self.assertRaises(DraftError):
            self.auto_edit(other, "toggle", enabled="1")
        self.auto_edit(self.b, "toggle", enabled="1")
        self.d.SessionLocal.remove()
        self.db = self.d.SessionLocal()
        self.addCleanup(self.db.close)
        self.assertEqual(self.db.query(self.d.AutoDraftSetting).filter_by(
            matchup_id=self.mid, player_id=self.b).one().enabled, 1)
        self.auto_edit(self.b, "toggle", enabled="0")
        self.db.expire_all()
        self.assertEqual(self.db.query(self.d.AutoDraftSetting).filter_by(
            matchup_id=self.mid, player_id=self.b).one().enabled, 0)

    def test_auto_highest_priority_skips_taken_and_double_turn(self):
        self.auto_context()
        for i in range(4):
            self.auto_add(self.b, i)
        self.auto_edit(self.b, "toggle", enabled="1")
        result = self.service.command(self.mid, self.a, manual=self.fx[0][:2], expected_count=0)
        self.assertEqual(result["count"], 2)
        self.assertEqual([p.fixture_id for p in self.auto_picks()], [f[0] for f in self.fx[:3]])
        self.assertEqual([p.player_id for p in self.auto_picks()], [self.a, self.b, self.b])
        self.assertEqual(self.service.command(self.mid, self.b)["count"], 0)

    def test_auto_no_fallback_for_invalid_or_exhausted_queue(self):
        self.auto_context()
        for i in range(2):
            self.auto_add(self.a, i)
        row = self.db.query(self.d.AutoDraftPreference).filter_by(fixture_id=self.fx[0][0]).one()
        row.team = "Invalid"
        row2 = self.db.query(self.d.AutoDraftPreference).filter_by(fixture_id=self.fx[1][0]).one()
        row2.fixture_id = 999999
        self.db.commit()
        self.assertEqual(self.auto_edit(self.a, "toggle", enabled="1")["count"], 0)
        self.assertEqual(len(self.auto_picks()), 0)

    def test_auto_both_queues_complete_ten_picks_idempotently(self):
        self.auto_context()
        for player in (self.a, self.b):
            for i in range(10):
                self.auto_add(player, i)
        self.auto_edit(self.b, "toggle", enabled="1")
        self.assertEqual(self.auto_edit(self.a, "toggle", enabled="1")["count"], 10)
        picks = self.auto_picks()
        self.assertEqual([p.player_id for p in picks],
                         [self.a, self.b, self.b, self.a, self.a, self.b, self.b, self.a, self.a, self.b])
        with self.assertRaises(DraftError):
            self.auto_edit(self.a, "toggle", enabled="1")
        self.assertEqual(len(self.auto_picks()), 10)

    def test_auto_manual_same_player_second_pick_and_stale_version(self):
        self.auto_context()
        self.service.command(self.mid, self.a, manual=self.fx[0][:2])
        self.auto_edit(self.b, "toggle", enabled="1")
        # Simulate a persisted enabled queue prepared before this request.
        self.db.add(self.d.AutoDraftPreference(matchup_id=self.mid, player_id=self.b,
            fixture_id=self.fx[2][0], team=self.fx[2][1], priority=0))
        self.db.commit()
        result = self.service.command(self.mid, self.b, manual=self.fx[1][:2], expected_count=1)
        self.assertEqual(result["count"], 1)
        with self.assertRaises(DraftError):
            self.service.command(self.mid, self.a, manual=self.fx[3][:2], expected_count=0)
        with self.assertRaises(DraftError):
            self.service.command(self.mid, self.a, manual=self.fx[0][:2], expected_count=3)
        self.assertEqual(len(self.auto_picks()), 3)

    def test_auto_competing_manual_writes_reserve_single_snake_slot(self):
        self.auto_context()
        self.service.command(self.mid, self.a, manual=self.fx[0][:2])
        barrier = threading.Barrier(2)
        def write(index):
            barrier.wait()
            try:
                self.service.command(self.mid, self.b, manual=self.fx[index][:2], expected_count=1)
                return "saved"
            except DraftError:
                return "conflict"
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(write, [1, 2]))
        self.assertCountEqual(results, ["saved", "conflict"])
        self.assertEqual(len(self.auto_picks()), 2)

    def test_auto_competing_auto_manual_and_refresh(self):
        self.auto_context()
        self.auto_add(self.a, 0)
        self.db.add(self.d.AutoDraftSetting(matchup_id=self.mid, player_id=self.a, enabled=1))
        self.db.commit()
        barrier = threading.Barrier(3)
        def write(kind):
            barrier.wait()
            try:
                return self.service.command(self.mid, self.a,
                    manual=self.fx[1][:2] if kind == "manual" else None, expected_count=0)
            except DraftError:
                return None
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(write, ["auto", "auto", "manual"]))
        self.assertEqual(len(self.auto_picks()), 1)

    def test_auto_archived_finalized_and_completed_read_only(self):
        self.auto_context()
        week = self.db.get(self.d.Week, self.week_id)
        week.season.is_archived = 1
        self.db.commit()
        with self.assertRaises(DraftError):
            self.auto_add(self.a, 0)
        week.season.is_archived = 0
        week.status = "finalized"
        self.db.commit()
        with self.assertRaises(DraftError):
            self.service.command(self.mid, self.a, manual=self.fx[0][:2])
        with self.assertRaises(DraftError):
            self.auto_edit(self.a, "toggle", enabled="1")

    def test_auto_http_get_read_only_htmx_prg_and_authorization(self):
        self.auto_context()
        name = self.db.get(self.d.Player, self.a).name
        self.db.rollback()
        with self.d.app.test_client() as client:
            self.assertEqual(client.post(f"/auto-draft/{self.mid}", data={"action": "toggle", "enabled": "1"}).status_code, 403)
            with client.session_transaction() as session:
                session["player_name"] = name
            data = dict(action="add", choice=f"{self.fx[0][0]}:{self.fx[0][1]}")
            response = client.post(f"/auto-draft/{self.mid}", data=data)
            self.assertEqual(response.status_code, 303)
            self.assertIn(b"Auto-Draft", client.get(response.location).data)
            self.assertEqual(client.get(f"/auto-draft/{self.mid}").status_code, 405)
            response = client.post(f"/auto-draft/{self.mid}", data=dict(action="toggle", enabled="1"), headers={"HX-Request": "true"})
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Auto-Draft made 1 pick", response.data)
            self.assertIn(b"Liverpool Picked", response.data)
            self.assertNotIn(b"<!DOCTYPE", response.data)
            for _ in range(2):
                client.get("/tab/current")
            self.assertEqual(len(self.auto_picks()), 1)

    def test_auto_additive_migration_idempotent_preserves_picks(self):
        self.auto_context()
        self.service.command(self.mid, self.a, manual=self.fx[0][:2])
        self.auto_add(self.b, 1)
        self.db.rollback()
        self.assertFalse(self.d.ensure_database_schema(self.d.engine))
        self.assertFalse(self.d.ensure_database_schema(self.d.engine))
        self.assertEqual(len(self.auto_picks()), 1)
        self.assertEqual(len(self.service.preferences(self.db, self.mid, self.b)), 1)

    def test_auto_migration_from_prior_schema_and_disk_persistence(self):
        self.auto_context()
        self.service.command(self.mid, self.a, manual=self.fx[0][:2])
        self.db.rollback()
        self.d.AutoDraftPreference.__table__.drop(self.d.engine)
        self.d.AutoDraftSetting.__table__.drop(self.d.engine)
        self.assertFalse(self.d.ensure_database_schema(self.d.engine))
        self.assertEqual(len(self.auto_picks()), 1)
        self.auto_add(self.b, 1)
        # A separate interpreter reads the persisted queue without importing the
        # application or sharing any ORM state/connections.
        import subprocess
        import sys
        result = subprocess.run([sys.executable, "-c",
            "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
            "print(c.execute('SELECT COUNT(*) FROM auto_draft_preferences').fetchone()[0]); c.close()",
            self.d.engine.url.database], check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "1")

    def test_auto_database_lock_fails_without_partial_write(self):
        self.auto_context()
        from sqlalchemy import create_engine
        from unittest.mock import patch
        locking = self.d.engine.connect()
        locking.execute(text("BEGIN IMMEDIATE"))
        short_wait = create_engine(str(self.d.engine.url), connect_args={"timeout": .01})
        try:
            with patch.object(self.d, "engine", short_wait):
                with self.assertRaises(DraftError) as error:
                    self.auto_add(self.a, 0)
                self.assertEqual(error.exception.status, 409)
        finally:
            locking.rollback()
            locking.close()
            short_wait.dispose()
        self.assertEqual(len(self.service.preferences(self.db, self.mid, self.a)), 0)

    def test_auto_http_manual_version_and_participant_checks(self):
        self.auto_context()
        name = self.db.get(self.d.Player, self.a).name
        outsider = self.db.query(self.d.Player).filter(self.d.Player.id.notin_([self.a, self.b])).first().name
        self.db.rollback()
        data = dict(week=1, season="year-2", matchup_id=self.mid,
                    fixture_id=self.fx[0][0], team=self.fx[0][1], presentation="v3")
        with self.d.app.test_client() as client:
            with client.session_transaction() as session:
                session["player_name"] = name
            self.assertEqual(client.post("/pick", data=data).status_code, 400)
            data["expected_count"] = 0
            response = client.post("/pick", data=data)
            self.assertIn(response.status_code, (302, 303))
            self.assertEqual(client.post("/pick", data=data).status_code, 409)
            with client.session_transaction() as session:
                session["player_name"] = outsider
            self.assertEqual(client.post(f"/auto-draft/{self.mid}", data=dict(action="toggle", enabled="1")).status_code, 403)
            self.assertEqual(client.post("/pick", data=data).status_code, 403)
        self.assertEqual(len(self.auto_picks()), 1)

    def test_auto_competing_same_fixture_never_double_selected(self):
        self.auto_context()
        barrier = threading.Barrier(2)
        def write(player):
            barrier.wait()
            try:
                self.service.command(self.mid, player, manual=self.fx[0][:2])
                return True
            except DraftError:
                return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(write, [self.a, self.b])), 1)
        self.assertEqual(len(self.auto_picks()), 1)

    def test_auto_gets_do_not_run_eligible_persisted_queue(self):
        self.auto_context()
        self.auto_add(self.a, 0)
        self.db.add(self.d.AutoDraftSetting(matchup_id=self.mid, player_id=self.a, enabled=1))
        name = self.db.get(self.d.Player, self.a).name
        self.db.commit()
        with self.d.app.test_client() as client:
            self.assertNotIn(b"Turn Auto-Draft", client.get("/tab/current").data)
            with client.session_transaction() as session:
                session["player_name"] = name
            for path in ("/", "/tab/current", "/partials/matchweek/1", "/partials/matchups/1"):
                self.assertEqual(client.get(path).status_code, 200)
        self.assertEqual(len(self.auto_picks()), 0)
