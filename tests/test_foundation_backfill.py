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


if __name__ == "__main__":
    unittest.main()
