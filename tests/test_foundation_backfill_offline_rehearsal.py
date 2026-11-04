"""Synthetic-only qualification of v3 private offline rehearsal admission."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import socket
import sqlite3
import unittest
from unittest.mock import patch

from test_foundation_backfill_offline import OfflineTests, sha, sign
from foundation_backfill_validation import inspect_database
import foundation_backfill_operator as operator
import foundation_backfill_rehearsal as rehearsal
import foundation_backfill_receipt as receipt
import foundation_backfill_execute as execution


class OfflineRehearsalTests(unittest.TestCase):
    manifest = OfflineTests.manifest
    plan = OfflineTests.plan
    validate = OfflineTests.validate

    def setUp(self):
        OfflineTests.setUp(self)
        # Only synthetic fixture ACL checks are mocked; actual private-path checks
        # are performed independently before any production-derived copy is made.
        operator.private_directory.side_effect = lambda value: Path(value).resolve()
        self.execution = Path(self.temp.name) / 'execution'
        self.execution.mkdir()
        self.review = dict(self.bindings, owner_decision_path=self.owner,
                           audit_evidence_path=self.audit)

    def clone(self, plan, **overrides):
        args = dict(source_snapshot=self.path, expected_source_sha256=sha(self.path),
                    manifest=sign(self.doc), expected_manifest_checksum=self.doc['checksum'],
                    plan=plan, expected_plan_sha256=rehearsal.plan_sha256(plan),
                    acknowledge_disposable_production_derived=True,
                    offline_review=self.review, execution_directory=self.execution)
        args.update(overrides)
        return rehearsal.rehearsal_clone(**args)

    def apply(self, target, plan, **overrides):
        args = dict(database_path=target.path, source_snapshot=self.path,
                    expected_target_sha256=target.initial_sha256, manifest=self.doc,
                    expected_manifest_checksum=self.doc['checksum'], plan=plan,
                    expected_plan_sha256=rehearsal.plan_sha256(plan),
                    offline_review=self.review, completion_receipt=True)
        args.update(overrides)
        return rehearsal.apply_rehearsal(target, **args)

    def test_v3_receipt_scoring_preservation_and_idempotency(self):
        plan = self.plan()
        source_hash = sha(self.path)
        with self.clone(plan) as target:
            self.assertEqual(target.path.parent.parent, self.execution)
            self.assertEqual(sha(target.path), source_hash)
            first = self.apply(target, plan)
            self.assertEqual(first['status'], 'applied')
            self.assertTrue(first['scoring_equal'])
            self.assertEqual(first['write_counts'], plan['expected_write_counts'])
            self.assertEqual(first['receipt_writes'], 1)
            self.assertFalse(first['execution_authorized'])
            with closing(rehearsal._readonly_source(target.path)) as db:
                verified = receipt.verify_state(db, manifest=self.doc, plan=plan,
                    expected_manifest_checksum=self.doc['checksum'],
                    expected_plan_sha256=rehearsal.plan_sha256(plan),
                    offline_review=self.review, require_completed=True)
                self.assertEqual(verified['status'], 'stage2_complete')
                self.assertTrue(verified['offline_rehearsal_only'])
                self.assertFalse(verified['email_send_authorized'])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM league_notification_recipients').fetchone()[0],0)
                self.assertEqual(db.execute('SELECT status,correspondent_enabled FROM leagues').fetchone(),('setup',0))
            after = sha(target.path)
            second = self.apply(target, plan)
            self.assertEqual(second['status'], 'already_matches')
            self.assertEqual(second['receipt_writes'],0)
            self.assertEqual(after,sha(target.path))
        self.assertEqual(source_hash,sha(self.path))

    def test_explicit_offline_review_and_private_temp_target_required(self):
        plan = self.plan()
        for override in ({'offline_review':None},{'execution_directory':None},
                         {'acknowledge_disposable_production_derived':False}):
            with self.subTest(fields=list(override)), patch.object(rehearsal.tempfile,'mkdtemp',side_effect=AssertionError('no copy')):
                with self.assertRaises(rehearsal.RehearsalError):
                    with self.clone(plan,**override): pass
        with patch.object(operator,'private_directory',side_effect=ValueError('synthetic ACL refusal')):
            with self.assertRaises(ValueError):
                with self.clone(plan): pass

    def test_wrong_review_hash_and_manifest_identity_refuse(self):
        plan = self.plan()
        wrong = deepcopy(self.review)
        wrong['owner_decision_sha256']='0'*64
        for override in ({'offline_review':wrong},{'expected_manifest_checksum':'0'*64},
                         {'expected_plan_sha256':'0'*64},{'expected_source_sha256':'0'*64}):
            with self.subTest(fields=list(override)):
                with self.assertRaises(rehearsal.RehearsalError):
                    with self.clone(plan,**override): pass

    def test_forged_rehashed_plan_refused_by_rebuild(self):
        plan = self.plan()
        plan['new_rows']['users'][0]['values']['name']='Synthetic forged name'
        with self.assertRaisesRegex(rehearsal.RehearsalError,'REVALIDATED_PLAN_MISMATCH'):
            with self.clone(plan): pass

    def test_interruption_after_receipt_rolls_back_population_and_ledger(self):
        plan = self.plan()
        with self.clone(plan) as target:
            before = inspect_database(target.path)
            with self.assertRaisesRegex(rehearsal.RehearsalError,'INJECTED_FAILURE'):
                self.apply(target,plan,fail_at='after_receipt')
            self.assertEqual(before,inspect_database(target.path))
            with closing(rehearsal._readonly_source(target.path)) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0],1)

    def test_timeout_rolls_back(self):
        plan = self.plan()
        with self.clone(plan) as target:
            before = inspect_database(target.path)
            with patch.object(rehearsal,'TRANSACTION_SECONDS',-1):
                with self.assertRaises(rehearsal.RehearsalError): self.apply(target,plan)
            self.assertEqual(before,inspect_database(target.path))

    def claim(self, expiry):
        with closing(sqlite3.connect(self.path)) as db, db:
            values=dict(id=2,week_id=10,status='claimed',lease_token='synthetic',
                        lease_expires_at=expiry,page_count=0,retrieved_count=0,persisted_count=0)
            for _,name,kind,required,default,pk in db.execute('PRAGMA table_info(correspondent_x_collection_states)'):
                if required and name not in values:
                    values[name]=0 if kind in {'INTEGER','BOOLEAN'} else '2000-01-01'
            names=tuple(values)
            db.execute('INSERT INTO correspondent_x_collection_states ('+','.join(names)+
                       ') VALUES ('+','.join('?' for _ in names)+')',tuple(values[n] for n in names))
        self.doc['source']=deepcopy(inspect_database(self.path)['source'])
        self.bindings['reviewed_source']=deepcopy(self.doc['source'])
        self.review['reviewed_source']=deepcopy(self.doc['source'])

    def test_expired_x_claim_is_preserved_with_cutover_gate_not_cleared(self):
        self.claim('2000-01-01')
        plan=self.plan()
        self.assertEqual(plan['status'],'gated')
        with self.clone(plan) as target:
            result=self.apply(target,plan)
            self.assertEqual(result['preserved_plan_gates'],plan['execution_gates'])
            self.assertEqual(result['production_execution_gates'],list(rehearsal.GATES))
            with closing(rehearsal._readonly_source(target.path)) as db:
                self.assertEqual(db.execute('SELECT status,lease_token,lease_expires_at FROM correspondent_x_collection_states').fetchone(),('claimed','synthetic','2000-01-01'))

    def test_active_x_claim_still_blocks_before_clone(self):
        self.claim('2099-01-01')
        plan=self.plan()
        self.assertEqual(plan['status'],'blocked')
        with patch.object(rehearsal.tempfile,'mkdtemp',side_effect=AssertionError('no copy')):
            with self.assertRaisesRegex(rehearsal.RehearsalError,'BLOCKED_PLAN'):
                with self.clone(plan): pass

    def test_missing_gate_cannot_be_hidden_in_rehashed_plan(self):
        self.claim('2000-01-01')
        plan=self.plan()
        plan['execution_gates']=[]
        plan['status']='planned'
        with self.assertRaisesRegex(rehearsal.RehearsalError,'REVALIDATED_PLAN_MISMATCH'):
            with self.clone(plan): pass

    def test_v3_cannot_enter_production_execution_or_default_receipt_verifier(self):
        plan=self.plan()
        with self.assertRaises(execution.ExecutionError):
            execution.prepare_execution(database_path=self.path, manifest=self.doc,plan=plan,
                expected_manifest_checksum=self.doc['checksum'],expected_plan_sha256=rehearsal.plan_sha256(plan),
                expected_source=self.doc['source'],expected_stage1_ledger_sha256='0'*64,
                expected_schema_sha256=self.doc['source']['schema_sha256'],checkpoint={})
        with closing(rehearsal._readonly_source(self.path)) as db:
            with self.assertRaises(receipt.ReceiptError):
                receipt.verify_state(db,manifest=self.doc,plan=plan,
                    expected_manifest_checksum=self.doc['checksum'],expected_plan_sha256=rehearsal.plan_sha256(plan))

    def test_no_network_or_application_runtime_import(self):
        import builtins
        original=builtins.__import__
        def guarded(name,*args,**kwargs):
            if name.split('.')[0] in {'pickem_flask_htmx_tabs','flask','sqlalchemy','requests','openai'}:
                raise AssertionError('runtime import')
            return original(name,*args,**kwargs)
        plan=self.plan()
        with patch.object(builtins,'__import__',side_effect=guarded), patch.object(socket.socket,'connect',side_effect=AssertionError('network')):
            with self.clone(plan) as target:
                self.assertEqual(self.apply(target,plan)['status'],'applied')


if __name__=='__main__': unittest.main()
