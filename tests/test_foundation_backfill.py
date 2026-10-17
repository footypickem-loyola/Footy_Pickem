"""Synthetic-only Stage 2 tests. Never uses production paths or provider clients."""
import ast
from contextlib import closing, redirect_stdout
from copy import deepcopy
import gc
import io
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import foundation_migrations as stage1
from foundation_backfill import main
from foundation_backfill_manifest import (
    ManifestError, canonical_json, load_manifest, manifest_checksum, validate_manifest,
)
import foundation_backfill_validation as inspector
from test_foundation_migrations import make_legacy

ROOT = Path(__file__).resolve().parents[1]


def synthetic_manifest():
    evidence = "e" * 64
    permission = "f" * 64
    season, home, away = "a" * 64, "b" * 64, "c" * 64
    rows = [
        dict(kind="player", player_id=1, email=None, claimed_identity=None,
             favorite_club=None, platform_admin=None, role=None, recap_opt_in=None),
        dict(kind="season", season_id=1, football_season_key=season),
        dict(kind="week", week_id=1, season_id=1, football_season_key=season, number=1),
        dict(kind="fixture", fixture_id=1, week_id=1, football_season_key=season,
             round=1, home_club_key=home, away_club_key=away),
        dict(kind="alias", football_season_key=season, normalized_name_sha256="d"*64, club_key=home),
        dict(kind="pick", pick_id=1, fixture_id=1, selected_club_key=home),
        dict(kind="preference", preference_id=1, fixture_id=1, selected_club_key=away),
        dict(kind="task", week_id=1, status="completed", has_claim=False),
        dict(kind="recipient", recipient_kind="recap", permission_evidence=permission,
             identity_evidence=None),
    ]
    for row in rows:
        row["evidence"] = [permission if row["kind"] == "recipient" else evidence]
    doc = dict(schema_version=1, source=dict(schema_sha256="1"*64, data_sha256="2"*64),
               evidence=[dict(sha256=evidence, kind="fixture_provider"),
                         dict(sha256=permission, kind="permission")], mappings=rows)
    doc["checksum"] = manifest_checksum(doc)
    return doc


def resign(doc):
    doc["checksum"] = manifest_checksum(doc)
    return doc


def codes(report):
    return {item["code"] for item in report["blockers"]}


class InspectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Stage 1's fixture helper uses connection context managers that commit
        # without closing. Collect its unreachable connections before Windows cleanup.
        self.addCleanup(gc.collect)
        self.path = Path(self.temp.name) / "synthetic.db"
        make_legacy(self.path)
        before = stage1.inspect_database(self.path)
        stage1.apply_database(self.path, expected_schema=before["schema_sha256"],
                              backup=Path(self.temp.name)/"synthetic-before.db", authorized=True)

    def inspect(self):
        return inspector.inspect_database(self.path)

    def test_valid_stage1_baseline_inventory_and_blockers(self):
        report = self.inspect()
        self.assertTrue(report["stage1_valid"])
        self.assertFalse(report["execution_authorized"])
        self.assertEqual(report["status"], "blocked")
        self.assertIn("SEASON_EVIDENCE_MISSING", codes(report))
        self.assertIn("RECIPIENT_PERMISSION_MANIFEST_REQUIRED", codes(report))
        self.assertNotIn("FOUNDATION_ALREADY_POPULATED", codes(report))
        self.assertTrue(all(n == 0 for n in report["population"]["foundation_rows"].values()))
        players = report["inventory"]["tables"]["players"]
        self.assertEqual(players["primary_key"], ["id"])
        self.assertTrue(any(row["target_table"] == "users" for row in players["foreign_keys"]))
        self.assertTrue(any(row["column"] == "league_id" and not row["not_null"]
                            for row in players["columns"]))
        self.assertEqual(report["table_fingerprints"]["picks"]["rows"], 1)
        self.assertTrue(any(row["kind"] == "trigger" for row in report["inventory"]["objects"]))

    def test_repeat_inspection_deterministic_and_no_private_output(self):
        first = self.inspect()
        self.assertEqual(first, self.inspect())
        output = json.dumps(first)
        for private in ("Steve", "Joe", "Original recap", "sent-keep", "private_audit", "spacing"):
            self.assertNotIn(private, output)
        self.assertNotIn(str(self.path), output)

    def test_inspection_changes_no_database_or_files(self):
        before = {p.name: p.read_bytes() for p in Path(self.temp.name).iterdir()}
        self.inspect()
        after = {p.name: p.read_bytes() for p in Path(self.temp.name).iterdir()}
        self.assertEqual(before, after)

    def test_missing_path_refuses_without_file_creation(self):
        missing = self.path.with_name("absent.db")
        with self.assertRaisesRegex(inspector.InspectionError, "DATABASE_MISSING"):
            inspector.inspect_database(missing)
        self.assertFalse(missing.exists())
        with self.assertRaisesRegex(inspector.InspectionError, "EXPLICIT_DATABASE_REQUIRED"):
            inspector.inspect_database(None)

    def test_protected_and_uri_targets_refused_before_sqlite(self):
        with patch.object(sqlite3, "connect", side_effect=AssertionError("No connection")):
            for path in ("/data/pickem.db", "file:synthetic.db?mode=rw", "//server/database.db"):
                with self.subTest(path=path), self.assertRaises(inspector.InspectionError):
                    inspector.inspect_database(path)

    def test_wal_committed_rows_visible_and_uncommitted_rows_hidden(self):
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("UPDATE players SET name='WAL synthetic value' WHERE id=1")
            writer.commit()
            wal = Path(str(self.path) + "-wal")
            self.assertTrue(wal.exists())
            db_before, wal_before = self.path.read_bytes(), wal.read_bytes()
            current = self.inspect()
            writer.execute("UPDATE players SET name='Uncommitted value' WHERE id=1")
            self.assertEqual(current, self.inspect())
            writer.rollback()
            self.assertEqual(db_before, self.path.read_bytes())
            self.assertEqual(wal_before, wal.read_bytes())
            expected = stage1.inspect_database(self.path)["tables"]["players"]
            self.assertEqual(current["table_fingerprints"]["players"], expected)

    def test_read_connection_denies_writes_and_guard_disable(self):
        with closing(inspector.connect_readonly(self.path)) as db:
            for sql in ("UPDATE players SET name='no'", "CREATE TABLE forbidden(id)",
                        "CREATE TEMP TABLE forbidden(id)", "PRAGMA query_only=OFF",
                        "PRAGMA journal_mode=DELETE", "SELECT load_extension('no')"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                    db.execute(sql)

    def test_invalid_ledger_checksum_refused_without_repair(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE schema_migrations SET checksum=?", ("0"*64,))
        before = self.path.read_bytes()
        with self.assertRaisesRegex(inspector.InspectionError, "STAGE1_LEDGER_MISMATCH"):
            self.inspect()
        self.assertEqual(before, self.path.read_bytes())

    def test_second_receipt_is_not_accepted_yet(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO schema_migrations SELECT 'private_beta_002_backfill',"
                       "checksum,applied_at,before_schema_hash,after_schema_hash FROM schema_migrations")
        with self.assertRaisesRegex(inspector.InspectionError, "STAGE1_LEDGER_MISMATCH"):
            self.inspect()

    def test_schema_drift_refused(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE unexpected(id INTEGER)")
        with self.assertRaisesRegex(inspector.InspectionError, "SCHEMA_MISMATCH"):
            self.inspect()

    def test_unmigrated_database_refused(self):
        other = Path(self.temp.name)/"unmigrated.db"
        make_legacy(other)
        with self.assertRaisesRegex(inspector.InspectionError, "STAGE1_REQUIRED"):
            inspector.inspect_database(other)

    def test_conflicting_league_and_prepopulated_columns_reported(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO leagues(id,slug,name,created_at,updated_at) VALUES(1,'other','Other','now','now')")
            db.execute("UPDATE players SET league_id=1 WHERE id=1")
        report = self.inspect()
        self.assertTrue({"FOUNDATION_ALREADY_POPULATED", "FOUNDATION_COLUMN_POPULATED",
                         "CONFLICTING_LEAGUE1_IDENTITY"} <= codes(report))

    def test_matching_league_identity_still_requires_reviewed_plan(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO leagues(id,slug,name,created_at,updated_at) VALUES(1,'loyola',?,'now','now')",
                       ("Loyola Pick \u2019Em",))
        report = self.inspect()
        self.assertIn("FOUNDATION_ALREADY_POPULATED", codes(report))
        self.assertNotIn("CONFLICTING_LEAGUE1_IDENTITY", codes(report))

    def test_unresolved_pick_and_inflight_task(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE picks SET team='not a participant' WHERE id=1")
            db.execute("UPDATE pick_insight_refreshes SET status='running',claim_token='private-claim',lease_until='expired'")
        report = self.inspect()
        self.assertIn("UNRESOLVED_PICK_PARTICIPANT", codes(report))
        self.assertIn("UNSUPPORTED_OR_IN_FLIGHT_TASK", codes(report))
        self.assertNotIn("private-claim", json.dumps(report))

    def test_foreign_key_violation_refused(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE picks SET player_id=999")
        with self.assertRaisesRegex(inspector.InspectionError, "INTEGRITY_OR_FOREIGN_KEY_FAILURE"):
            self.inspect()

    def test_views_and_literal_defaults_not_exposed(self):
        # Synthetic extra schema established before Stage 1, so its ledger covers it.
        other = Path(self.temp.name)/"view.db"
        make_legacy(other)
        with closing(sqlite3.connect(other)) as db, db:
            db.execute("CREATE VIEW sensitive_view AS SELECT 'private-view-canary' AS secret")
        baseline = stage1.inspect_database(other)
        stage1.apply_database(other, expected_schema=baseline["schema_sha256"],
                              backup=Path(self.temp.name)/"view-before.db", authorized=True)
        output = json.dumps(inspector.inspect_database(other))
        self.assertNotIn("private-view-canary", output)
        self.assertNotIn("sensitive_view", output)
        self.assertIn('"kind": "view"', output)

    def test_inspection_does_not_call_stage1_apply_or_network(self):
        with patch.object(stage1, "apply_database", side_effect=AssertionError("No apply")), \
             patch.object(socket, "create_connection", side_effect=AssertionError("No network")), \
             patch.object(socket.socket, "connect", side_effect=AssertionError("No network")):
            self.assertTrue(self.inspect()["stage1_valid"])

    def test_cli_safe_failures_and_no_implicit_or_apply_target(self):
        for argv in ([], ["inspect"], ["apply", "--database", str(self.path)],
                     ["inspect", "--database", str(self.path), "--token", "private-canary"]):
            with self.subTest(argv=argv), redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit):
                    main(argv)
                self.assertEqual(json.loads(output.getvalue())["code"], "INVALID_ARGUMENTS")
                self.assertNotIn("private-canary", output.getvalue())
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["inspect", "--database", str(self.path)]), 1)
        self.assertTrue(json.loads(output.getvalue())["stage1_valid"])


class ManifestTests(unittest.TestCase):
    def test_valid_manifest_has_no_execution_authority(self):
        report = validate_manifest(synthetic_manifest())
        self.assertEqual(report["status"], "valid")
        self.assertFalse(report["execution_authorized"])
        self.assertEqual(report["scope"], "structure_and_internal_consistency_only")

    def test_checksum_and_serialization_ignore_order_not_values(self):
        doc = synthetic_manifest()
        other = deepcopy(doc)
        other["mappings"].reverse()
        other["evidence"].reverse()
        self.assertEqual(canonical_json(doc), canonical_json(other))
        self.assertEqual(manifest_checksum(doc), manifest_checksum(other))
        other["source"]["data_sha256"] = "3"*64
        self.assertNotEqual(manifest_checksum(doc), manifest_checksum(other))
        with self.assertRaisesRegex(ManifestError, "MANIFEST_CHECKSUM_MISMATCH"):
            validate_manifest(other)

    def test_unknown_identity_fields_remain_null_without_mutation(self):
        doc = synthetic_manifest()
        before = deepcopy(doc)
        validate_manifest(doc)
        self.assertEqual(doc, before)
        for field in ("email", "claimed_identity", "favorite_club", "platform_admin", "role", "recap_opt_in"):
            broken = deepcopy(doc)
            broken["mappings"][0][field] = "do-not-infer"
            with self.subTest(field=field), self.assertRaisesRegex(
                    ManifestError, "IDENTITY_PERMISSION_VALUES_UNSUPPORTED_IN_V1"):
                validate_manifest(resign(broken))

    def test_duplicate_mapping_and_conflicting_mapping_refused(self):
        for changed in (False, True):
            doc = synthetic_manifest()
            row = deepcopy(doc["mappings"][1])
            if changed:
                row["football_season_key"] = "9"*64
            doc["mappings"].append(row)
            self.assertIn("DUPLICATE_OR_CONFLICTING_MAPPING", codes(validate_manifest(resign(doc))))

    def test_ambiguous_club_alias_refused(self):
        doc = synthetic_manifest()
        row = deepcopy(doc["mappings"][4])
        row["club_key"] = "c"*64
        doc["mappings"].append(row)
        self.assertIn("AMBIGUOUS_CLUB_ALIAS", codes(validate_manifest(resign(doc))))

    def test_inconsistent_season_and_round_refused(self):
        for field, value in (("football_season_key", "9"*64), ("round", 2)):
            doc = synthetic_manifest()
            doc["mappings"][3][field] = value
            self.assertIn("INCONSISTENT_SEASON_ROUND_EVIDENCE", codes(validate_manifest(resign(doc))))

    def test_missing_and_conflicting_evidence_are_distinct(self):
        doc = synthetic_manifest()
        doc["mappings"][1]["evidence"] = []
        self.assertIn("MISSING_EVIDENCE", codes(validate_manifest(resign(doc))))
        doc = synthetic_manifest()
        doc["evidence"].append(dict(sha256="e"*64, kind="permission"))
        self.assertIn("DUPLICATE_OR_CONFLICTING_EVIDENCE", codes(validate_manifest(resign(doc))))

    def test_unresolved_pick_mapping_refused(self):
        for value in (None, "9"*64):
            doc = synthetic_manifest()
            doc["mappings"][5]["selected_club_key"] = value
            self.assertIn("UNRESOLVED_PICK_PARTICIPANT", codes(validate_manifest(resign(doc))))

    def test_task_claim_or_uncertain_state_blocks(self):
        for field, value in (("has_claim", True), ("status", "uncertain")):
            doc = synthetic_manifest()
            doc["mappings"][7][field] = value
            self.assertIn("UNSUPPORTED_OR_IN_FLIGHT_TASK", codes(validate_manifest(resign(doc))))

    def test_recipient_requires_explicit_permission_not_name(self):
        doc = synthetic_manifest()
        doc["mappings"][8]["permission_evidence"] = "e"*64
        self.assertIn("RECIPIENT_PERMISSION_UNRESOLVED", codes(validate_manifest(resign(doc))))
        doc["mappings"][8]["email"] = "private@example.invalid"
        with self.assertRaisesRegex(ManifestError, "UNRECOGNIZED_OR_MISSING_FIELDS"):
            validate_manifest(resign(doc))

    def test_unknown_malformed_and_unsupported_fields_refuse_cleanly(self):
        cases = []
        for field, value in (("schema_version", 2), ("schema_version", True),
                             ("mappings", {}), ("checksum", 9)):
            doc = synthetic_manifest()
            doc[field] = value
            cases.append(doc)
        doc = synthetic_manifest()
        doc["credential"] = "private-canary"
        cases.append(doc)
        doc = synthetic_manifest()
        doc["mappings"][0]["player_id"] = True
        cases.append(resign(doc))
        for doc in cases:
            with self.subTest(doc_type=type(doc)), self.assertRaises(ManifestError) as error:
                validate_manifest(doc)
            self.assertNotIn("private-canary", str(error.exception))

    def test_conflicting_input_order_produces_identical_reports(self):
        doc = synthetic_manifest()
        conflict = deepcopy(doc["mappings"][1])
        conflict["football_season_key"] = "9"*64
        doc["mappings"].append(conflict)
        doc["evidence"].append(dict(sha256="f"*64, kind="fixture_provider"))
        original = validate_manifest(resign(doc))
        doc["mappings"].reverse()
        doc["evidence"].reverse()
        self.assertEqual(original, validate_manifest(resign(doc)))

    def test_alias_requires_known_season_and_participant(self):
        doc = synthetic_manifest()
        doc["mappings"][4]["football_season_key"] = "9"*64
        report = validate_manifest(resign(doc))
        self.assertTrue({"MISSING_SEASON_MAPPING", "UNRESOLVED_CLUB_ALIAS"} <= codes(report))

    def test_duplicate_canonical_fixture_refused_without_merging(self):
        doc = synthetic_manifest()
        duplicate = deepcopy(doc["mappings"][3])
        duplicate["fixture_id"] = 2
        doc["mappings"].append(duplicate)
        self.assertIn("DUPLICATE_CANONICAL_MAPPING", codes(validate_manifest(resign(doc))))

    def test_file_loader_rejects_duplicate_keys_nan_and_invalid_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"synthetic.json"
            for raw in ('{"schema_version":1,"schema_version":1}', '{"x":NaN}', "not-json"):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(ManifestError):
                    load_manifest(path)
            path.write_text(json.dumps(synthetic_manifest()), encoding="utf-8")
            self.assertEqual(load_manifest(path)["status"], "valid")
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main(["validate-manifest", "--manifest", str(path)]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "valid")


class IsolationTests(unittest.TestCase):
    def test_imports_have_no_app_worker_network_or_database_side_effects(self):
        script = """
import builtins, sqlite3, socket
def forbidden(*args, **kwargs):
    raise AssertionError('Unexpected side effect')
sqlite3.connect = forbidden
socket.create_connection = forbidden
socket.socket.connect = forbidden
original_import = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('flask', 'sqlalchemy', 'pickem_flask', 'shared_brief_worker', 'live_sync_worker')):
        raise AssertionError('Runtime import')
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded
import foundation_backfill, foundation_backfill_manifest, foundation_backfill_validation
"""
        result = subprocess.run([sys.executable, "-B", "-c", script], cwd=ROOT,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_modules_have_no_apply_or_allocation_function(self):
        for name in ("foundation_backfill.py", "foundation_backfill_manifest.py",
                     "foundation_backfill_validation.py"):
            tree = ast.parse((ROOT/name).read_text(encoding="utf-8"))
            functions = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            self.assertFalse(any("apply" in n or "allocat" in n for n in functions))
            source = (ROOT/name).read_text(encoding="utf-8")
            self.assertNotIn("immutable=1&", source)
            self.assertNotIn("executescript(", source)



class PlannerTests(unittest.TestCase):
    """Reviewed synthetic v2 evidence, never provider requests or real snapshots."""
    def setUp(self):
        from test_foundation_migrations import LEGACY_DDL
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'planner-synthetic.db'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executescript(LEGACY_DDL)
            db.executescript("""
                INSERT INTO players(id,name) VALUES(9,'Alice'),(2,'Bob'),(15,'Unused');
                INSERT INTO seasons(id,code,name,is_active,is_archived,api_competition_code,api_season_year)
                    VALUES(1,'synthetic-one','Synthetic One',0,1,'PL',2025),(2,'synthetic-two','Synthetic Two',1,0,'PL',2026);
                INSERT INTO weeks(id,season_id,number,room_code,status) VALUES(10,1,1,'synthetic-1','finalized'),(20,2,1,'synthetic-2','open');
                INSERT INTO fixtures(id,week_id,match_number,home,away,external_match_id) VALUES
                    (100,10,7,'Home FC','Away FC',1000),(101,10,9,'Away FC','Home FC',1001),(200,20,1,'Home FC','Away FC',2000);
                INSERT INTO matchups(id,week_id,player_a_id,player_b_id,first_picker_id) VALUES(1,10,9,2,9),(2,20,2,9,2);
                INSERT INTO picks(id,matchup_id,player_id,fixture_id,team) VALUES(1,1,9,100,'Home FC'),(2,2,2,200,'Away FC');
                INSERT INTO auto_draft_preferences(id,matchup_id,player_id,fixture_id,team,priority) VALUES(1,1,2,101,'Home FC',1);
                INSERT INTO results(id,fixture_id,outcome,source) VALUES(1,100,'Home','manual');
                INSERT INTO fixture_provider_links(id,fixture_id,provider,external_fixture_id) VALUES
                    (1,100,'sportmonks',3000),(2,101,'sportmonks',3001),(3,200,'sportmonks',4000);
                INSERT INTO api_sync_states(id,season_id,provider,fixtures_imported,results_imported,pending_matches,unmatched_matches) VALUES(1,1,'football-data',3,1,0,0);
                INSERT INTO pick_insight_refreshes(week_id,season_id,status,created_at,completed_at)
                    VALUES(10,1,'completed','2026-01-01','2026-01-02');
                INSERT INTO club_season_enrichment(season_id,sportmonks_team_id,sportmonks_season_id,league_id,footy_club,top_scorers,synced_at)
                    VALUES(1,30,301,8,'Home FC','[]','2026-01-01');
                INSERT INTO match_events(id,fixture_id,provider,external_event_id,event_type,side,team_provider_id,team_name,sort_order,is_active,created_at,updated_at,revisions)
                    VALUES(1,100,'sportmonks','event-1','goal','home',30,'Home FC',1,1,'2026-01-01','2026-01-01','[]');
            """)
        before = stage1.inspect_database(self.path)
        stage1.apply_database(self.path, expected_schema=before['schema_sha256'], backup=Path(self.temp.name)/'before.db', authorized=True)
        self.doc = self.manifest()

    def manifest(self):
        from foundation_migrations import digest
        evidence = {kind: digest(kind) for kind in ('product_decision','season_provider','fixture_provider','reviewed_alias','permission')}
        from foundation_backfill_manifest import PLAN_EVIDENCE
        rows = []
        def add(kind, **values):
            rows.append(dict(kind=kind, evidence=[evidence[PLAN_EVIDENCE[kind]]], **values))
        def providers(a,b):
            return [dict(provider='football-data',external_id=str(a)),dict(provider='sportmonks',external_id=str(b))]
        comp, home, away = digest('competition'), digest('home'), digest('away')
        add('policy', timestamp='2026-10-01T00:00:00+00:00', slug='synthetic-loyola', status='setup',
            timezone='America/New_York', correspondent_enabled=0, user_is_platform_admin=0, club_is_active=1, sync_enabled=0)
        add('competition',key=comp,code='PL',name='Synthetic League',providers=providers('PL',8))
        add('club',key=home,name='Home FC',providers=providers(10,30))
        add('club',key=away,name='Away FC',providers=providers(20,40))
        for sid, year, wid in [(1,2025,10),(2,2026,20)]:
            sk = digest(str(sid))
            add('football_season',key=sk,competition_key=comp,name=f'Synthetic {sid}',start_year=year,providers=providers(100+sid,300+sid))
            add('season',id=sid,football_season_key=sk,api_competition_code='PL',api_season_year=year)
            add('week',id=wid,season_id=sid,football_season_key=sk,number=1)
            for name, ck in [('Home FC',home),('Away FC',away)]:
                add('alias',football_season_key=sk,name=name,club_key=ck)
        for fid,wid,sid,external,sport,h,a in [(100,10,1,1000,3000,home,away),(101,10,1,1001,3001,away,home),(200,20,2,2000,4000,home,away)]:
            ps = []
            for provider, ext, hc, ac, season, competition in [
                ('football-data',external,10 if h==home else 20,20 if a==away else 10,100+sid,'PL'),
                ('sportmonks',sport,30 if h==home else 40,40 if a==away else 30,300+sid,8)]:
                ps.append(dict(provider=provider,external_id=str(ext),home_external_id=str(hc),away_external_id=str(ac),
                               external_season_id=str(season),external_competition_id=str(competition)))
            add('fixture',id=fid,week_id=wid,football_season_key=digest(str(sid)),round=1,
                home='Home FC' if h==home else 'Away FC',away='Away FC' if a==away else 'Home FC',
                home_club_key=h,away_club_key=a,external_match_id=external,providers=ps)
        add('recipient',recipient_kind='recap',email_normalized='synthetic@example.invalid',enabled=1,user_id=None)
        doc = dict(schema_version=2, source=inspector.inspect_database(self.path)['source'],
                   evidence=[dict(kind=k,sha256=v) for k,v in evidence.items()],mappings=rows)
        return resign(doc)

    def plan(self):
        from foundation_backfill_planner import plan_database
        return plan_database(self.path,resign(self.doc),inspector.inspect_database(self.path))

    def change(self, sql, args=()):
        with closing(sqlite3.connect(self.path)) as db, db: db.execute(sql,args)
        self.doc['source'] = inspector.inspect_database(self.path)['source']

    def row(self, kind):
        return next(r for r in self.doc['mappings'] if r['kind']==kind)

    def test_happy_path_multi_season(self):
        plan=self.plan()
        self.assertEqual(plan['blockers'],[])
        self.assertEqual(plan['status'],'planned')
        self.assertEqual(len(plan['new_rows']['football_seasons']),2)
        self.assertEqual(len(plan['nullable_updates']['fixtures']),3)
        self.assertFalse(plan['execution_authorized'])

    def test_repeat_and_permuted_input_byte_stability(self):
        first=canonical_json(self.plan())
        self.doc['mappings'].reverse()
        self.doc['evidence'].reverse()
        for row in self.doc['mappings']:
            if 'providers' in row: row['providers'].reverse()
        self.assertEqual(first,canonical_json(self.plan()))
        self.assertEqual(first,canonical_json(self.plan()))

    def test_proposed_ids_follow_numeric_player_order(self):
        users=self.plan()['proposed_ids']['users']
        self.assertEqual(users,{'2':1,'9':2,'15':3})

    def test_player_name_collision_never_merges_users(self):
        self.change("UPDATE players SET name='alice' WHERE id=2")
        p=self.plan()
        self.assertIn('MEMBERSHIP_NAME_COLLISION',codes(p))
        self.assertEqual(len(p['new_rows']['users']),3)
        self.assertEqual(len({r['values']['id'] for r in p['new_rows']['users']}),3)

    def test_league_one_conflict(self):
        self.change("INSERT INTO leagues(id,slug,name,created_at,updated_at) VALUES(1,'wrong','Wrong','now','now')")
        self.assertIn('CONFLICTING_LEAGUE1_IDENTITY',codes(self.plan()))

    def test_verified_season_not_year_identifier(self):
        p=self.plan()
        self.assertEqual({r['values']['external_season_id'] for r in p['new_rows']['football_season_provider_links']},{'101','102','301','302'})

    def test_year_cannot_be_provider_season_id(self):
        r=self.row('football_season')
        r['providers'][0]['external_id']=str(r['start_year'])
        self.assertIn('YEAR_IS_NOT_PROVIDER_SEASON_ID',codes(self.plan()))

    def test_conflicting_season_evidence(self):
        self.row('season')['api_season_year']=2030
        self.assertIn('INCONSISTENT_SEASON_EVIDENCE',codes(self.plan()))

    def test_week_matchweek_mismatch(self):
        self.row('week')['number']=2
        self.assertIn('INCONSISTENT_SEASON_ROUND_EVIDENCE',codes(self.plan()))

    def test_alias_ambiguity(self):
        self.doc['mappings'].append(deepcopy(self.row('alias')))
        self.assertIn('AMBIGUOUS_CLUB_ALIAS',codes(self.plan()))

    def test_provider_id_conflict(self):
        self.row('fixture')['providers'][0]['external_id']='bad'
        self.assertIn('CONFLICTING_PROVIDER_IDS',codes(self.plan()))

    def test_provider_participants_reversed(self):
        p=self.row('fixture')['providers'][0]
        p['home_external_id'],p['away_external_id']=p['away_external_id'],p['home_external_id']
        self.assertIn('CONFLICTING_PROVIDER_PARTICIPANT_OR_SEASON',codes(self.plan()))

    def test_home_away_reversal(self):
        r=self.row('fixture')
        r['home'],r['away']=r['away'],r['home']
        self.assertIn('FIXTURE_ORIENTATION_CONFLICT',codes(self.plan()))

    def test_exact_pick_and_preference_mapping(self):
        p=self.plan()
        self.assertEqual(len(p['nullable_updates']['picks']),2)
        self.assertEqual(len(p['nullable_updates']['auto_draft_preferences']),1)

    def test_draw_and_unknown_are_blockers(self):
        for team in ['Draw','HOME FC','Unknown']:
            with self.subTest(team=team):
                self.change('UPDATE picks SET team=? WHERE id=1',(team,))
                self.assertIn('UNRESOLVED_PICK_PARTICIPANT',codes(self.plan()))

    def test_unknown_preference_blocked(self):
        self.change("UPDATE auto_draft_preferences SET team='Draw'")
        self.assertIn('UNRESOLVED_PICK_PARTICIPANT',codes(self.plan()))

    def test_cross_week_pick_relationship(self):
        self.change('UPDATE picks SET fixture_id=200 WHERE id=1')
        self.assertIn('CONFLICTING_PICK_RELATIONSHIP',codes(self.plan()))

    def test_nonparticipant_player_blocked(self):
        self.change('UPDATE picks SET player_id=15 WHERE id=1')
        self.assertIn('CONFLICTING_PICK_RELATIONSHIP',codes(self.plan()))

    def test_invalid_first_picker(self):
        self.change('UPDATE matchups SET first_picker_id=15 WHERE id=1')
        self.assertIn('INVALID_MATCHUP_RELATIONSHIP',codes(self.plan()))

    def test_original_slate_ids_and_order(self):
        rows=[r['values'] for r in self.plan()['new_rows']['league_week_fixtures']]
        self.assertEqual({(r['fixture_id'],r['display_order']) for r in rows},{(100,7),(101,9),(200,1)})

    def test_invalid_and_duplicate_slate_order(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE fixtures SET match_number=7 WHERE id=101')
        self.change('UPDATE fixtures SET match_number=0 WHERE id=101')
        self.assertIn('INVALID_OR_DUPLICATE_SLATE_ORDER',codes(self.plan()))

    def test_observed_roster_no_global_fallback(self):
        rows=[r['values'] for r in self.plan()['new_rows']['league_season_memberships']]
        self.assertEqual({(r['season_id'],r['membership_id']) for r in rows},{(1,2),(1,9),(2,2),(2,9)})

    def test_empty_season_unresolved(self):
        self.change("INSERT INTO seasons(id,code,name,is_active,is_archived) VALUES(3,'empty','Empty',0,0)")
        self.assertIn('EMPTY_SEASON_ROSTER_UNRESOLVED',codes(self.plan()))

    def test_completed_task_preserved(self):
        job=self.plan()['new_rows']['football_refresh_jobs'][0]['values']
        self.assertEqual((job['status'],job['created_at'],job['completed_at']),('completed','2026-01-01','2026-01-02'))

    def test_running_uncertain_and_stale_claims_block(self):
        for status,claim in [('running',None),('uncertain',None),('completed','stale-claim')]:
            with self.subTest(status=status):
                self.change('UPDATE pick_insight_refreshes SET status=?,claim_token=?',(status,claim))
                self.assertIn('UNSUPPORTED_OR_IN_FLIGHT_TASK',codes(self.plan()))

    def test_task_season_conflict(self):
        self.change('UPDATE pick_insight_refreshes SET season_id=2')
        self.assertIn('TASK_SEASON_WEEK_CONFLICT',codes(self.plan()))

    def test_explicit_recipient_no_association(self):
        r=self.plan()['new_rows']['league_notification_recipients'][0]['values']
        self.assertEqual((r['league_id'],r['email_normalized'],r['user_id']),(1,'synthetic@example.invalid',None))

    def test_recipient_guessing_refused(self):
        self.row('recipient')['user_id']=2
        with self.assertRaisesRegex(ManifestError,'RECIPIENT_IDENTITY_UNSUPPORTED'): self.plan()

    def test_recipient_permission_not_inferred(self):
        self.doc['mappings']=[r for r in self.doc['mappings'] if r['kind']!='recipient']
        self.assertIn('RECIPIENT_PERMISSION_MANIFEST_REQUIRED',codes(self.plan()))

    def test_unknown_identity_and_permission_null(self):
        p=self.plan()
        for r in p['new_rows']['users']:
            self.assertEqual(r['values']['status'],'unclaimed')
            for field in ('email','email_normalized','email_verified_at','favorite_club_id'): self.assertIsNone(r['values'][field])
        for r in p['nullable_updates']['players']:
            self.assertFalse(set(r['values']) & {'role','recap_opt_in','status'})
        self.assertTrue(p['intentionally_null'])

    def test_stable_row_write_counts(self):
        p=self.plan()
        self.assertEqual(p['expected_write_counts']['updates']['players'],3)
        self.assertEqual(p['expected_write_counts']['inserts']['league_season_memberships'],4)
        self.assertEqual(p['expected_write_counts'],self.plan()['expected_write_counts'])

    def test_no_writes_or_external_side_effects(self):
        before=self.path.read_bytes()
        with patch.object(stage1,'apply_database',side_effect=AssertionError('apply forbidden')), \
             patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')), \
             patch.object(subprocess,'Popen',side_effect=AssertionError('process forbidden')):
            p=self.plan()
        self.assertEqual(before,self.path.read_bytes())
        self.assertNotIn('schema_migrations',p['new_rows'])
        self.assertNotIn('results',p['nullable_updates'])

    def test_stale_fingerprint_refused(self):
        from foundation_backfill_planner import plan_database
        report=inspector.inspect_database(self.path)
        self.change("UPDATE players SET name='Changed' WHERE id=9")
        with self.assertRaisesRegex(inspector.InspectionError,'SOURCE_FINGERPRINT_MISMATCH'):
            plan_database(self.path,resign(self.doc),report)

    def test_supported_event_mapping(self):
        self.assertEqual(len(self.plan()['nullable_updates']['match_events']),1)

    def test_event_provider_conflict(self):
        self.change('UPDATE match_events SET team_provider_id=40')
        self.assertIn('EVENT_CLUB_CONFLICT',codes(self.plan()))

    def test_event_without_team_evidence_left_null(self):
        self.change('UPDATE match_events SET side=NULL,team_name=NULL,team_provider_id=NULL')
        p=self.plan()
        self.assertNotIn('match_events',p['nullable_updates'])
        self.assertTrue(any(r['table']=='match_events' for r in p['intentionally_null']))

    def test_cache_provider_conflict(self):
        self.change('UPDATE club_season_enrichment SET sportmonks_season_id=999')
        self.assertIn('CONFLICTING_PROVIDER_IDS',codes(self.plan()))

    def test_cli_plan_redacts_private_values_and_has_no_apply(self):
        manifest=Path(self.temp.name)/'manifest.json'
        manifest.write_text(canonical_json(self.doc))
        output=io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(['plan','--database',str(self.path),'--manifest',str(manifest)]),0)
        self.assertNotIn('synthetic@example.invalid',output.getvalue())
        self.assertNotIn('Alice',output.getvalue())
        self.assertFalse(json.loads(output.getvalue())['execution_authorized'])
        with redirect_stdout(io.StringIO()),self.assertRaises(SystemExit): main(['apply'])

    def test_canonical_task_collision_blocks_even_with_completed_receipts(self):
        from foundation_migrations import digest
        sk=digest('1')
        self.change('UPDATE seasons SET api_season_year=2025 WHERE id=2')
        self.change("INSERT INTO pick_insight_refreshes(week_id,season_id,status,created_at,completed_at) VALUES(20,2,'completed','2026-02-01','2026-02-02')")
        for row in self.doc['mappings']:
            if row['kind']=='season' and row['id']==2:
                row['football_season_key']=sk
                row['api_season_year']=2025
            if row['kind']=='week' and row['id']==20: row['football_season_key']=sk
            if row['kind']=='fixture' and row['id']==200:
                row['football_season_key']=sk
                for p in row['providers']:
                    p['external_season_id']='101' if p['provider']=='football-data' else '301'
        self.assertIn('CANONICAL_JOB_COLLISION',codes(self.plan()))

    def test_missing_reviewed_policy_and_evidence_block(self):
        self.doc['mappings']=[r for r in self.doc['mappings'] if r['kind']!='policy']
        self.row('season')['evidence']=[]
        p=self.plan()
        self.assertIn('REVIEWED_POLICY_REQUIRED',codes(p))
        self.assertIn('MISSING_OR_DUPLICATE_EVIDENCE',codes(p))
        self.assertFalse(p['counts_are_complete'])
        self.assertTrue(all(r['classification']=='unresolved / blocker' for rows in p['new_rows'].values() for r in rows))

    def test_missing_fixture_and_alias_are_review_requirements(self):
        self.doc['mappings']=[r for r in self.doc['mappings'] if not (r['kind']=='fixture' and r['id']==100)]
        p=self.plan()
        self.assertIn('FIXTURE_MAPPING_REQUIRED',codes(p))
        self.assertTrue(any(b['classification']=='requires reviewed manifest evidence' for b in p['blockers']))

    def test_duplicate_manifest_fixture_refused(self):
        self.doc['mappings'].append(deepcopy(self.row('fixture')))
        self.assertIn('DUPLICATE_CANONICAL_MAPPING',codes(self.plan()))

    def test_v2_unknown_fields_and_wrong_evidence_type_refused(self):
        doc=deepcopy(self.doc)
        self.row('recipient')['guessed_player_name']='Bob'
        with self.assertRaises(ManifestError): self.plan()
        self.doc=doc
        self.doc['evidence'][0]['kind']=[]
        with self.assertRaises(ManifestError): self.plan()

    def test_v2_checksum_verified(self):
        from foundation_backfill_planner import plan_database
        self.doc['checksum']='0'*64
        with self.assertRaisesRegex(ManifestError,'MANIFEST_CHECKSUM_MISMATCH'):
            plan_database(self.path,self.doc,inspector.inspect_database(self.path))

    def test_policy_cannot_enable_sync_or_grant_admin(self):
        for field in ('sync_enabled','user_is_platform_admin'):
            with self.subTest(field=field):
                self.row('policy')[field]=1
                with self.assertRaises(ManifestError): self.plan()
                self.row('policy')[field]=0

    def test_manifest_source_mismatch_refused(self):
        self.doc['source']['data_sha256']='0'*64
        with self.assertRaisesRegex(inspector.InspectionError,'SOURCE_FINGERPRINT_MISMATCH'): self.plan()

    def test_all_proposals_have_provenance_and_allowed_targets(self):
        from foundation_schema import ADDITIONS
        p=self.plan()
        for table,rows in p['nullable_updates'].items():
            for row in rows:
                self.assertTrue(set(row['values']) <= set(ADDITIONS[table]))
                self.assertTrue(row['provenance'])
        for table,rows in p['new_rows'].items():
            self.assertNotIn(table,{'schema_migrations','user_identities','league_invitations'})
            for row in rows: self.assertTrue(row['provenance'])

    def test_database_and_stage1_receipt_unchanged(self):
        before=inspector.inspect_database(self.path)
        self.plan()
        self.assertEqual(before,inspector.inspect_database(self.path))


if __name__ == '__main__':
    unittest.main()
