"""Disposable synthetic fixtures for the separate offline-copy rehearsal boundary."""
from contextlib import closing
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import socket
import sqlite3
import unittest
from unittest.mock import patch

import test_foundation_backfill as fixtures
from foundation_backfill_manifest import canonical_json
from foundation_backfill_validation import inspect_database
import foundation_backfill_rehearsal as rehearsal


class RehearsalTests(unittest.TestCase):
    setUp = fixtures.PlannerTests.setUp
    manifest = fixtures.PlannerTests.manifest
    row = fixtures.PlannerTests.row
    plan = fixtures.PlannerTests.plan
    change = fixtures.PlannerTests.change

    def clone(self, plan=None, **overrides):
        plan = self.plan() if plan is None else plan
        args = dict(source_snapshot=self.path, expected_source_sha256=rehearsal.file_sha256(self.path),
                    manifest=self.doc, expected_manifest_checksum=self.doc['checksum'], plan=plan,
                    expected_plan_sha256=rehearsal.plan_sha256(plan),
                    acknowledge_disposable_production_derived=True)
        args.update(overrides)
        return rehearsal.rehearsal_clone(**args)

    def apply(self, target, plan, **overrides):
        args = dict(database_path=target.path, source_snapshot=self.path,
                    expected_target_sha256=target.initial_sha256, manifest=self.doc,
                    expected_manifest_checksum=self.doc['checksum'], plan=plan,
                    expected_plan_sha256=rehearsal.plan_sha256(plan))
        args.update(overrides)
        return rehearsal.apply_rehearsal(target, **args)

    def test_exact_clone_atomic_apply_and_idempotent_poststate(self):
        plan = self.plan()
        original = rehearsal.file_sha256(self.path)
        with self.clone(plan) as target:
            self.assertEqual(rehearsal.file_sha256(target.path), original)
            first = self.apply(target, plan)
            post = inspect_database(target.path)
            after_hash = rehearsal.file_sha256(target.path)
            second = self.apply(target, plan)
            self.assertEqual(first['write_counts'], plan['expected_write_counts'])
            self.assertTrue(first['scoring_equal'])
            self.assertEqual(first['scoring']['weeks'], 2)
            self.assertEqual(second['status'], 'already_matches')
            self.assertEqual(second['write_counts'], {'inserts': {}, 'updates': {}})
            self.assertEqual(post, inspect_database(target.path))
            self.assertEqual(after_hash, rehearsal.file_sha256(target.path))
            self.assertFalse(first['stage2_receipt_written'])
            self.assertEqual(first['production_execution_gates'], list(rehearsal.GATES))
        self.assertEqual(rehearsal.file_sha256(self.path), original)

    def test_late_failure_rolls_back_complete_state(self):
        plan = self.plan()
        with self.clone(plan) as target:
            before = inspect_database(target.path)
            with self.assertRaisesRegex(rehearsal.RehearsalError, 'INJECTED_FAILURE'):
                self.apply(target, plan, fail_at='before_commit')
            self.assertEqual(inspect_database(target.path), before)

    def test_acknowledgement_and_hashes_required_before_clone(self):
        plan = self.plan()
        for override, code in [
            ({'acknowledge_disposable_production_derived':False}, 'ACKNOWLEDGEMENT'),
            ({'expected_source_sha256':'0'*64}, 'SOURCE_HASH'),
            ({'expected_manifest_checksum':'0'*64}, 'MANIFEST_IDENTITY'),
            ({'expected_plan_sha256':'0'*64}, 'PLAN_IDENTITY'),
        ]:
            with self.subTest(code=code), patch.object(rehearsal.tempfile, 'mkdtemp', side_effect=AssertionError('must not create')):
                with self.assertRaisesRegex(rehearsal.RehearsalError, code):
                    with self.clone(plan, **override): pass

    def test_source_target_and_explicit_identity_must_match(self):
        plan = self.plan()
        with self.clone(plan) as target:
            for args in ({'database_path':self.path}, {'expected_target_sha256':'0'*64},
                         {'source_snapshot':target.path}, {'database_path':None}):
                with self.subTest(args=list(args)), patch.object(sqlite3, 'connect', side_effect=AssertionError('must not open')):
                    with self.assertRaises(rehearsal.RehearsalError):
                        self.apply(target, plan, **args)

    def test_forged_and_closed_capability_refuse(self):
        plan = self.plan()
        with self.clone(plan) as target:
            forged = rehearsal.RehearsalTarget(target.source,target.path,target.initial_sha256)
            with self.assertRaisesRegex(rehearsal.RehearsalError, 'LIVE_REHEARSAL'):
                self.apply(forged, plan)
        with self.assertRaisesRegex(rehearsal.RehearsalError, 'LIVE_REHEARSAL'):
            self.apply(target, plan)

    def test_replaced_target_and_hardlink_refuse(self):
        plan = self.plan()
        with self.clone(plan) as target:
            moved = target.path.with_name('moved.db')
            target.path.rename(moved)
            target.path.write_bytes(moved.read_bytes())
            with self.assertRaisesRegex(rehearsal.RehearsalError, 'TARGET_IDENTITY'):
                self.apply(target, plan)
        with self.clone(plan) as target:
            alias = target.path.with_name('hardlink.db')
            os.link(target.path, alias)
            with self.assertRaisesRegex(rehearsal.RehearsalError, 'SINGLE_LINK'):
                self.apply(target, plan)

    def test_changed_source_refuses_before_target_open(self):
        plan = self.plan()
        with self.clone(plan) as target:
            self.change("UPDATE results SET outcome='Away' WHERE id=1")
            with patch.object(sqlite3, 'connect', side_effect=AssertionError('must not open')):
                with self.assertRaisesRegex(rehearsal.RehearsalError, 'SOURCE_HASH'):
                    self.apply(target, plan)

    def test_partial_population_is_never_resumed(self):
        plan = self.plan()
        with self.clone(plan) as target:
            with closing(sqlite3.connect(target.path)) as db, db:
                db.execute("INSERT INTO competitions(id,code,name) VALUES(1,'wrong','Wrong')")
            before = inspect_database(target.path)
            with self.assertRaisesRegex(rehearsal.RehearsalError, 'PARTIAL_OR_CONFLICTING'):
                self.apply(target, plan)
            self.assertEqual(before, inspect_database(target.path))

    def test_rehashed_forged_plan_refused_by_rebuild(self):
        plan = self.plan()
        plan['new_rows']['users'][0]['values']['is_platform_admin'] = 1
        with self.assertRaisesRegex(rehearsal.RehearsalError, 'REVALIDATED_PLAN'):
            with self.clone(plan): pass

    def test_new_plan_cannot_rebind_existing_capability(self):
        plan = self.plan()
        with self.clone(plan) as target:
            changed = deepcopy(plan)
            changed['new_rows']['users'][0]['values']['display_name'] = 'Changed'
            with self.assertRaisesRegex(rehearsal.RehearsalError, 'CAPABILITY_INPUT'):
                self.apply(target, changed)

    def test_gated_offline_plan_preserves_claim_and_all_gates(self):
        self.change("INSERT INTO correspondent_x_collection_states(id,week_id,status,window_start,window_end,page_count,retrieved_count,persisted_count,lease_token,lease_expires_at,created_at,updated_at) VALUES(8,10,'claimed','2000-01-01','2000-01-02',0,0,0,'synthetic-secret','2000-01-03','2000-01-01','2000-01-01')")
        plan = self.plan()
        self.assertEqual(plan['status'], 'gated')
        with self.clone(plan) as target:
            result = self.apply(target, plan)
            self.assertFalse(result['execution_authorized'])
            self.assertEqual(result['preserved_plan_gates'], plan['execution_gates'])
            with closing(sqlite3.connect(target.path)) as db:
                self.assertEqual(db.execute('SELECT status,lease_token FROM correspondent_x_collection_states WHERE id=8').fetchone(),
                                 ('claimed','synthetic-secret'))
        unknown = deepcopy(plan)
        unknown['execution_gates'][0]['code'] = 'UNKNOWN_GATE'
        with self.assertRaisesRegex(rehearsal.RehearsalError, 'UNSUPPORTED_EXECUTION_GATE'):
            with self.clone(unknown): pass

    def test_scoring_difference_rolls_back_before_commit(self):
        plan = self.plan()
        with self.clone(plan) as target:
            before = inspect_database(target.path)
            real = rehearsal.scoring_evidence
            calls = []
            def changed(db):
                result = real(db)
                calls.append(True)
                if len(calls) == 2: result['outputs_sha256'] = '0'*64
                return result
            with patch.object(rehearsal, 'scoring_evidence', side_effect=changed):
                with self.assertRaisesRegex(rehearsal.RehearsalError, 'SCORING_CHANGED'):
                    self.apply(target, plan)
            self.assertEqual(before, inspect_database(target.path))

    def test_deadline_refusal_leaves_baseline(self):
        plan = self.plan()
        with self.clone(plan) as target:
            before = inspect_database(target.path)
            with patch.object(rehearsal, 'TRANSACTION_SECONDS', -1):
                with self.assertRaises(rehearsal.RehearsalError):
                    self.apply(target, plan)
            self.assertEqual(before, inspect_database(target.path))

    def test_source_sidecars_refused_without_sqlite_open(self):
        plan = self.plan()
        sidecar = Path(str(self.path)+'-wal')
        sidecar.write_bytes(b'not an approved standalone artifact')
        with patch.object(sqlite3, 'connect', side_effect=AssertionError('must not open')):
            with self.assertRaisesRegex(rehearsal.RehearsalError, 'STANDALONE_SOURCE'):
                with self.clone(plan): pass

    def test_no_network_app_import_or_synthetic_capability(self):
        import builtins
        import foundation_backfill_synthetic as synthetic
        original = builtins.__import__
        def guarded(name,*args,**kwargs):
            if name.split('.')[0] in {'pickem_flask_htmx_tabs','flask','sqlalchemy','requests','openai'}:
                raise AssertionError('runtime import')
            return original(name,*args,**kwargs)
        plan = self.plan()
        with patch.object(builtins,'__import__',side_effect=guarded), \
             patch.object(socket.socket,'connect',side_effect=AssertionError('network')), \
             patch.object(synthetic,'apply_synthetic',side_effect=AssertionError('synthetic boundary')), \
             patch.object(synthetic,'synthetic_database',side_effect=AssertionError('synthetic capability')):
            with self.clone(plan) as target:
                self.assertEqual(self.apply(target,plan)['status'],'applied')

    def test_actual_scoring_outputs_match_known_fixture(self):
        import foundation_backfill_rehearsal_scoring as scoring
        self.change("UPDATE weeks SET status='finalized' WHERE id=10")
        captured = []
        real_digest = scoring.digest
        def record(value):
            if isinstance(value, list) and value and isinstance(value[0], dict) and 'week_id' in value[0]:
                captured.append(deepcopy(value))
            return real_digest(value)
        with closing(rehearsal._readonly_source(self.path)) as db, patch.object(scoring, 'digest', side_effect=record):
            scoring.scoring_evidence(db)
        week = captured[0][0]
        self.assertEqual(week['points'], {2:0,9:1,15:0})
        self.assertEqual(week['payouts'], [{'from':'Bob','to':'Alice','points':1,'payout':5}])
        self.assertEqual([(r['player_id'],r['net_points'],r['correct'],r['rank']) for r in week['standings']],
                         [(9,1,1,1),(2,-1,0,2)])
        self.change("UPDATE results SET outcome='Draw' WHERE id=1")
        with closing(rehearsal._readonly_source(self.path)) as db, patch.object(scoring, 'digest', side_effect=record):
            scoring.scoring_evidence(db)
        week = captured[1][0]
        self.assertEqual(week['points'], {2:0,9:0,15:0})
        self.assertEqual(week['payouts'], [{'from':'-','to':'-','points':0,'payout':0}])
        self.assertEqual([(r['player_id'],r['draws'],r['rank']) for r in week['standings']],[(9,1,1),(2,0,1)])


if __name__ == '__main__':
    unittest.main()
