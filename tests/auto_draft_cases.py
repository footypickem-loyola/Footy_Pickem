"""Focused cases mixed into the existing isolated-database app fixture."""
import threading
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import text
from auto_draft import DraftService, DraftError


class AutoDraftCases:
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
            self.assertIn(b"Fixture already drafted", response.data)
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
