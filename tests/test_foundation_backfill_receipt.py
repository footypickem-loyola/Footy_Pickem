"""Receipt lifecycle, atomicity and strict evidence tests on synthetic fixtures."""
from contextlib import closing
from copy import deepcopy
import sqlite3
import unittest
from unittest.mock import patch

import test_foundation_backfill_rehearsal as fixtures
import foundation_backfill_rehearsal as rehearsal
import foundation_backfill_receipt as receipt
import foundation_migrations as migrations
from foundation_backfill_validation import inspect_database


class ReceiptTests(unittest.TestCase):
    setUp = fixtures.RehearsalTests.setUp
    manifest = fixtures.RehearsalTests.manifest
    row = fixtures.RehearsalTests.row
    plan = fixtures.RehearsalTests.plan
    change = fixtures.RehearsalTests.change
    clone = fixtures.RehearsalTests.clone

    def apply(self, target, plan, **kwargs):
        return fixtures.RehearsalTests.apply(self, target, plan, completion_receipt=True, **kwargs)

    def evidence(self, plan, **kwargs):
        result = dict(manifest=self.doc, plan=plan,
                      expected_manifest_checksum=self.doc['checksum'],
                      expected_plan_sha256=rehearsal.plan_sha256(plan))
        result.update(kwargs)
        return result

    def verify(self, path, plan, **kwargs):
        with closing(rehearsal._readonly_source(path)) as db:
            db.execute('BEGIN')
            return receipt.verify_state(db, **self.evidence(plan, **kwargs))

    def tamper(self, target, sql, args=()):
        with closing(sqlite3.connect(target.path)) as db, db:
            db.execute(sql, args)

    def test_baseline_and_completed_explicit_verifier_default_stays_strict(self):
        plan = self.plan()
        self.assertEqual(self.verify(self.path, plan)['status'], 'stage1_only')
        with closing(rehearsal._readonly_source(self.path)) as db:
            self.assertTrue(migrations.verified_applied(db))
            self.assertTrue(migrations.verified_applied(db, stage2_evidence=self.evidence(plan)))
        with self.clone(plan) as target:
            result = self.apply(target, plan)
            self.assertEqual(result['receipt_writes'], 1)
            self.assertEqual(result['write_counts'], plan['expected_write_counts'])
            self.assertEqual(self.verify(target.path,plan,require_completed=True)['status'], 'stage2_complete')
            with closing(rehearsal._readonly_source(target.path)) as db:
                with self.assertRaises(migrations.MigrationError):
                    migrations.verified_applied(db)
                self.assertTrue(migrations.verified_applied(db, stage2_evidence=self.evidence(plan)))
                with self.assertRaises(migrations.MigrationError):
                    migrations.verified_applied(db, expected_schema='0'*64, stage2_evidence=self.evidence(plan))
            self.assertFalse(result['execution_authorized'])
            self.assertEqual(result['production_execution_gates'], list(rehearsal.GATES))

    def test_rerun_preserves_entire_receipt_timestamp_and_file(self):
        plan = self.plan()
        with self.clone(plan) as target:
            first = self.apply(target, plan)
            before = rehearsal.file_sha256(target.path)
            second = self.apply(target, plan)
            self.assertEqual(second['status'], 'already_matches')
            self.assertEqual(second['receipt'], first['receipt'])
            self.assertEqual(second['receipt_writes'], 0)
            self.assertEqual(second['write_counts'], {'inserts':{},'updates':{}})
            self.assertEqual(before, rehearsal.file_sha256(target.path))
            self.assertEqual(first['receipt']['before_schema_hash'], first['receipt']['after_schema_hash'])

    def test_wrong_manifest_and_plan_identity_refuse(self):
        plan = self.plan()
        for args in ({'expected_manifest_checksum':'0'*64}, {'expected_plan_sha256':'0'*64},
                     {'plan':dict(plan,source={})}, {'manifest':dict(self.doc,checksum='0'*64)}):
            with self.subTest(keys=list(args)), closing(rehearsal._readonly_source(self.path)) as db:
                with self.assertRaises(receipt.ReceiptError):
                    receipt.verify_state(db, **dict(self.evidence(plan), **args))

    def test_receipt_checksum_and_schema_fields_and_timestamp_refuse(self):
        plan = self.plan()
        for field,value in [('checksum','0'*64),('before_schema_hash','0'*64),
                            ('after_schema_hash','0'*64),('applied_at','not-a-time'),
                            ('applied_at','2026-01-01T00:00:00')]:
            with self.subTest(field=field,value=value), self.clone(plan) as target:
                self.apply(target, plan)
                self.tamper(target, f'UPDATE schema_migrations SET {field}=? WHERE version=?',
                            (value,receipt.VERSION))
                with self.assertRaises(receipt.ReceiptError):
                    self.verify(target.path,plan)
                with self.assertRaises(rehearsal.RehearsalError):
                    self.apply(target,plan)

    def test_equal_but_wrong_receipt_schema_refuses(self):
        plan = self.plan()
        with self.clone(plan) as target:
            self.apply(target,plan)
            self.tamper(target, "UPDATE schema_migrations SET before_schema_hash=?,after_schema_hash=? WHERE version=?",
                        ('0'*64,'0'*64,receipt.VERSION))
            with self.assertRaisesRegex(receipt.ReceiptError,'SCHEMA_MISMATCH'):
                self.verify(target.path,plan)

    def test_changed_stage1_each_cell_refuses(self):
        plan = self.plan()
        for field in ('checksum','applied_at','before_schema_hash','after_schema_hash'):
            with self.subTest(field=field), self.clone(plan) as target:
                self.apply(target,plan)
                self.tamper(target,f'UPDATE schema_migrations SET {field}=? WHERE version=?',
                            ('changed',migrations.VERSION))
                with self.assertRaises(receipt.ReceiptError):
                    self.verify(target.path,plan)

    def test_unknown_third_row_and_missing_stage1_refuse(self):
        plan = self.plan()
        for sql,args in [
            ("INSERT INTO schema_migrations SELECT 'unknown',checksum,applied_at,before_schema_hash,after_schema_hash FROM schema_migrations WHERE version=?", (receipt.VERSION,)),
            ('DELETE FROM schema_migrations WHERE version=?',(migrations.VERSION,))]:
            with self.subTest(sql=sql), self.clone(plan) as target:
                self.apply(target,plan)
                self.tamper(target,sql,args)
                with self.assertRaisesRegex(receipt.ReceiptError,'LEDGER_HISTORY'):
                    self.verify(target.path,plan)

    def test_duplicate_history_refuses_even_if_driver_returns_duplicate(self):
        plan = self.plan()
        with self.clone(plan) as target:
            self.apply(target,plan)
            with closing(rehearsal._readonly_source(target.path)) as db:
                class DuplicateLedger:
                    def execute(self, sql, *args):
                        cursor = db.execute(sql,*args)
                        if sql.startswith('SELECT version,checksum,applied_at,'):
                            rows = cursor.fetchall()
                            class Rows:
                                def fetchall(self):
                                    return rows + [next(r for r in rows if r[0]==receipt.VERSION)]
                            return Rows()
                        return cursor
                with self.assertRaisesRegex(receipt.ReceiptError,'LEDGER_HISTORY'):
                    receipt.verify_state(DuplicateLedger(),**self.evidence(plan))

    def test_actual_schema_drift_refuses(self):
        plan = self.plan()
        with self.clone(plan) as target:
            self.apply(target,plan)
            self.tamper(target,'CREATE TABLE unexpected(value TEXT)')
            with self.assertRaisesRegex(receipt.ReceiptError,'SCHEMA_MISMATCH'):
                self.verify(target.path,plan)

    def test_receipt_with_missing_or_conflicting_population_refuses(self):
        plan = self.plan()
        for sql in ['UPDATE players SET user_id=NULL',
                    "UPDATE users SET display_name='conflicting'",
                    'DELETE FROM league_week_fixtures']:
            with self.subTest(sql=sql), self.clone(plan) as target:
                self.apply(target,plan)
                self.tamper(target,sql)
                with self.assertRaisesRegex(receipt.ReceiptError,'POPULATION_MISMATCH'):
                    self.verify(target.path,plan,require_completed=True)

    def test_population_without_receipt_refuses_completed_and_rerun(self):
        plan = self.plan()
        with self.clone(plan) as target:
            self.apply(target,plan)
            self.tamper(target,'DELETE FROM schema_migrations WHERE version=?',(receipt.VERSION,))
            with self.assertRaisesRegex(receipt.ReceiptError,'RECEIPT_REQUIRED'):
                self.verify(target.path,plan,require_completed=True)
            with self.assertRaisesRegex(receipt.ReceiptError,'POPULATION_WITHOUT_RECEIPT'):
                self.verify(target.path,plan)
            with self.assertRaises(rehearsal.RehearsalError):
                self.apply(target,plan)

    def test_failure_after_receipt_rolls_back_all_population_and_ledger(self):
        plan = self.plan()
        for phase in ('before_receipt','after_receipt','before_commit'):
            with self.subTest(phase=phase), self.clone(plan) as target:
                before = inspect_database(target.path)
                with self.assertRaisesRegex(rehearsal.RehearsalError,'INJECTED_FAILURE'):
                    self.apply(target,plan,fail_at=phase)
                self.assertEqual(inspect_database(target.path),before)
                self.assertEqual(self.verify(target.path,plan)['status'],'stage1_only')

    def test_insertion_failure_rolls_back(self):
        plan = self.plan()
        with self.clone(plan) as target:
            before = inspect_database(target.path)
            with patch.object(rehearsal,'_insert_receipt',side_effect=sqlite3.IntegrityError('synthetic')):
                with self.assertRaises(rehearsal.RehearsalError):
                    self.apply(target,plan)
            self.assertEqual(inspect_database(target.path),before)

    def test_post_insert_verification_failure_rolls_back(self):
        plan = self.plan()
        original = receipt.verify_state
        def fail(db,**kwargs):
            if kwargs.get('require_completed'):
                self.assertEqual(db.execute('SELECT count(*) FROM schema_migrations').fetchone(),(2,))
                raise receipt.ReceiptError('INJECTED_RECEIPT_VERIFICATION_FAILURE')
            return original(db,**kwargs)
        with self.clone(plan) as target:
            before = inspect_database(target.path)
            with patch.object(receipt,'verify_state',side_effect=fail):
                with self.assertRaises(rehearsal.RehearsalError):
                    self.apply(target,plan)
            self.assertEqual(inspect_database(target.path),before)

    def test_schema_hash_change_before_receipt_rolls_back(self):
        plan = self.plan()
        real_schema = rehearsal.schema
        calls = []
        def changed(db):
            calls.append(True)
            return real_schema(db) if len(calls)==1 else []
        with self.clone(plan) as target:
            before = inspect_database(target.path)
            with patch.object(rehearsal,'schema',side_effect=changed):
                with self.assertRaisesRegex(rehearsal.RehearsalError,'SCHEMA_MISMATCH'):
                    self.apply(target,plan)
            self.assertEqual(inspect_database(target.path),before)

    def test_receipt_mode_guard_only_allows_one_plain_insert(self):
        plan = self.plan()
        original = rehearsal._insert_receipt
        def guarded(db,*args):
            for sql in ('DELETE FROM schema_migrations',
                        "UPDATE schema_migrations SET applied_at='changed'",
                        'CREATE TABLE forbidden(id INTEGER)'):
                with self.assertRaises(sqlite3.DatabaseError):
                    db.execute(sql)
            original(db,*args)
        with self.clone(plan) as target, patch.object(rehearsal,'_insert_receipt',side_effect=guarded):
            self.assertEqual(self.apply(target,plan)['receipt_writes'],1)

    def test_extra_preserved_tables_use_inspector_private_name_tokens(self):
        # Model a separately reviewed Stage 1 schema containing legacy extras.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE preserved_extra(id INTEGER PRIMARY KEY AUTOINCREMENT,payload BLOB)')
            db.execute("INSERT INTO preserved_extra(payload) VALUES(X'00FF')")
            db.execute('UPDATE schema_migrations SET after_schema_hash=?',
                       (migrations.digest(migrations.schema(db)),))
        self.doc = self.manifest()
        plan = self.plan()
        with self.clone(plan) as target:
            self.apply(target,plan)
            self.assertEqual(self.verify(target.path,plan)['status'],'stage2_complete')
            self.tamper(target,"UPDATE preserved_extra SET payload=X'00FE'")
            with self.assertRaisesRegex(receipt.ReceiptError,'PROTECTED_STATE_CHANGED'):
                self.verify(target.path,plan)

    def test_checksum_binds_each_reviewed_identity(self):
        source = dict(schema_sha256='a'*64,data_sha256='b'*64)
        initial = receipt.checksum('c'*64,'d'*64,source)
        self.assertEqual(initial,receipt.checksum('c'*64,'d'*64,dict(reversed(list(source.items())))))
        for m,p,s in [('e'*64,'d'*64,source),('c'*64,'e'*64,source),
                      ('c'*64,'d'*64,dict(source,schema_sha256='e'*64)),
                      ('c'*64,'d'*64,dict(source,data_sha256='e'*64))]:
            self.assertNotEqual(initial,receipt.checksum(m,p,s))
        with patch.object(receipt,'CONTRACT','future'):
            self.assertNotEqual(initial,receipt.checksum('c'*64,'d'*64,source))
        with patch.object(receipt,'VERSION','future'):
            self.assertNotEqual(initial,receipt.checksum('c'*64,'d'*64,source))


if __name__ == '__main__':
    unittest.main()
