"""Synthetic end-to-end explicit execution contract tests; no service access."""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch

import test_foundation_backfill_rehearsal as fixtures
import foundation_backfill_execute as execute
import foundation_backfill_receipt as receipt
import foundation_backfill_rehearsal as rehearsal
from foundation_migrations import schema, digest
from foundation_backfill_validation import inspect_database


class ExecuteTests(unittest.TestCase):
    setUp = fixtures.RehearsalTests.setUp
    manifest = fixtures.RehearsalTests.manifest
    row = fixtures.RehearsalTests.row
    plan = fixtures.RehearsalTests.plan
    change = fixtures.RehearsalTests.change

    def prepare(self):
        p = self.plan()
        root = Path(self.temp.name)
        checkpoint, restore, target = [root/n for n in ('checkpoint.db','restore.db','execution.db')]
        checkpoint.touch(exist_ok=False)
        with closing(rehearsal._readonly_source(self.path)) as src, closing(sqlite3.connect(checkpoint)) as dst:
            src.backup(dst)
        with checkpoint.open('rb') as src, restore.open('xb') as dst:
            shutil.copyfileobj(src,dst)
        with restore.open('rb') as src, target.open('xb') as dst:
            shutil.copyfileobj(src,dst)
        with closing(rehearsal._readonly_source(self.path)) as db:
            ledger = execute._ledger(db)
        args = dict(database_path=target,manifest=self.doc,plan=p,
            expected_manifest_checksum=self.doc['checksum'],expected_plan_sha256=rehearsal.plan_sha256(p),
            expected_source=p['source'],expected_stage1_ledger_sha256=ledger,
            expected_schema_sha256=p['source']['schema_sha256'],
            checkpoint=dict(path=str(checkpoint),sha256=rehearsal.file_sha256(checkpoint),
                restore_path=str(restore),restore_sha256=rehearsal.file_sha256(restore),independently_verified=True))
        packet = execute.prepare_execution(**args)
        approved = execute.packet_sha256(packet)
        args.update(approved_packet=packet,approved_packet_sha256=approved,confirmation=execute.CONFIRMATION,
                    writers_fenced=True,operational_evidence=dict(mode='offline_simulation',
                        approved_packet_sha256=approved,observed_at=datetime.now(timezone.utc).isoformat(),
                        gates={g:dict(passed=True,evidence_sha256=digest('synthetic:'+g)) for g in execute.GATES}))
        self.target = target
        return args

    def mutate(self, sql, args=()):
        with closing(sqlite3.connect(self.target)) as db, db:
            db.execute(sql,args)

    def verify(self, args):
        with closing(execute._connect(self.target)) as db:
            db.execute('BEGIN')
            return receipt.verify_state(db,**{k:args[k] for k in
                ('manifest','plan','expected_manifest_checksum','expected_plan_sha256')})

    def test_fresh_approved_execution_single_commit_and_zero_write_rerun(self):
        args = self.prepare()
        before = rehearsal.file_sha256(self.path)
        original_connect = execute._connect
        transactions = []
        def traced(*a,**kw):
            db = original_connect(*a,**kw)
            if kw.get('writable'):
                db.set_trace_callback(lambda sql: transactions.append(sql) if sql in
                                      ('BEGIN IMMEDIATE','COMMIT','ROLLBACK') else None)
            return db
        with patch.object(execute,'_connect',side_effect=traced):
            result = execute.execute_plan(**args)
        self.assertEqual(transactions,['BEGIN IMMEDIATE','COMMIT'])
        self.assertEqual(result['write_counts'],args['plan']['expected_write_counts'])
        self.assertEqual(result['receipt_writes'],1)
        self.assertFalse(result['production_gates_cleared'])
        self.assertEqual(result['production_execution_gates'],list(execute.GATES))
        self.assertEqual(self.verify(args)['status'],'stage2_complete')
        target_hash = rehearsal.file_sha256(self.target)
        rerun = execute.execute_plan(**args)
        self.assertEqual(rerun['write_counts'],{'inserts':{},'updates':{}})
        self.assertEqual(rerun['receipt_writes'],0)
        self.assertEqual(rerun['receipt'],result['receipt'])
        self.assertEqual(target_hash,rehearsal.file_sha256(self.target))
        self.assertEqual(before,rehearsal.file_sha256(self.path))

    def test_wrong_manifest_plan_and_source_identities_refuse_without_writable_open(self):
        args = self.prepare()
        original = execute._connect
        def ro_only(*a,**kw):
            self.assertFalse(kw.get('writable'))
            return original(*a,**kw)
        for changed in ({'expected_manifest_checksum':'0'*64},{'expected_plan_sha256':'0'*64},
                        {'expected_source':dict(args['expected_source'],data_sha256='0'*64)},
                        {'expected_stage1_ledger_sha256':'0'*64},{'expected_schema_sha256':'0'*64}):
            with self.subTest(keys=list(changed)),patch.object(execute,'_connect',side_effect=ro_only):
                with self.assertRaises(execute.ExecutionError):
                    execute.execute_plan(**dict(args,**changed))

    def test_required_confirmation_writer_fence_and_checkpoint(self):
        args = self.prepare()
        for changed in ({'confirmation':None},{'writers_fenced':False},{'checkpoint':None},
                        {'checkpoint':dict(args['checkpoint'],independently_verified=False)},
                        {'approved_packet_sha256':'0'*64}):
            with self.subTest(keys=list(changed)):
                with self.assertRaises(execute.ExecutionError):
                    execute.execute_plan(**dict(args,**changed))
        self.assertEqual(self.verify(args)['status'],'stage1_only')

    def test_each_operational_gate_must_be_positive_and_bound(self):
        args = self.prepare()
        for gate in execute.GATES:
            changed = deepcopy(args)
            changed['operational_evidence']['gates'][gate]['passed'] = False
            with self.subTest(gate=gate),self.assertRaisesRegex(execute.ExecutionError,'GATE_NOT_SATISFIED'):
                execute.execute_plan(**changed)
        for key,value in [('observed_at','2000-01-01T00:00:00+00:00'),
                          ('approved_packet_sha256','0'*64),('gates',{}),('mode','unknown')]:
            changed = deepcopy(args)
            changed['operational_evidence'][key]=value
            with self.subTest(key=key),self.assertRaises(execute.ExecutionError):
                execute.execute_plan(**changed)

    def test_stale_legacy_source_refuses(self):
        args = self.prepare()
        self.mutate("UPDATE results SET outcome='Away'")
        before = rehearsal.file_sha256(self.target)
        with self.assertRaises(execute.ExecutionError):
            execute.execute_plan(**args)
        self.assertEqual(before,rehearsal.file_sha256(self.target))

    def test_missing_stage1_refuses(self):
        args = self.prepare()
        self.mutate('DELETE FROM schema_migrations')
        with self.assertRaises(execute.ExecutionError):
            execute.execute_plan(**args)

    def test_schema_and_trigger_change_refuse(self):
        args = self.prepare()
        self.mutate('CREATE TRIGGER unexpected AFTER UPDATE ON players BEGIN SELECT 1; END')
        with self.assertRaises(execute.ExecutionError):
            execute.execute_plan(**args)

    def test_partial_population_refuses(self):
        args = self.prepare()
        self.mutate("INSERT INTO competitions(id,code,name) VALUES(999,'bad','bad')")
        with self.assertRaises(execute.ExecutionError):
            execute.execute_plan(**args)

    def test_plan_bound_to_different_source_refuses_even_when_rehashed(self):
        args = self.prepare()
        changed = deepcopy(args)
        changed['plan']['source']['data_sha256']='0'*64
        changed['expected_plan_sha256']=rehearsal.plan_sha256(changed['plan'])
        with self.assertRaises(execute.ExecutionError):
            execute.execute_plan(**changed)

    def test_changed_checkpoint_or_restore_refuses(self):
        args = self.prepare()
        cp = Path(args['checkpoint']['restore_path'])
        with cp.open('ab') as stream:
            stream.write(b'changed')
        with self.assertRaisesRegex(execute.ExecutionError,'CHECKPOINT_HASH_MISMATCH'):
            execute.execute_plan(**args)

    def test_checkpoint_target_alias_refuses(self):
        args = self.prepare()
        args['checkpoint']['path']=str(self.target)
        with self.assertRaisesRegex(execute.ExecutionError,'INDEPENDENT'):
            execute.execute_plan(**args)

    def test_changed_target_after_preflight_refuses_under_reservation(self):
        args = self.prepare()
        original = execute._connect
        def race(*a,**kw):
            if kw.get('writable'):
                self.mutate("UPDATE results SET outcome='Away'")
            return original(*a,**kw)
        with patch.object(execute,'_connect',side_effect=race):
            with self.assertRaises(execute.ExecutionError):
                execute.execute_plan(**args)
        with closing(execute._connect(self.target)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM schema_migrations').fetchone(),(1,))
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone(),(0,))

    def test_after_receipt_failure_rolls_back_all_state(self):
        args = self.prepare()
        before = inspect_database(self.target)
        with self.assertRaisesRegex(execute.ExecutionError,'INJECTED_FAILURE'):
            execute.execute_plan(**args,fail_at='after_receipt')
        self.assertEqual(inspect_database(self.target),before)
        self.assertEqual(self.verify(args)['status'],'stage1_only')

    def test_scoring_failure_rolls_back(self):
        args = self.prepare()
        before = inspect_database(self.target)
        real = execute.scoring_evidence
        def wrong_after_write(db):
            result=real(db)
            if db.execute('SELECT count(*) FROM users').fetchone()[0]:
                result['outputs_sha256']='0'*64
            return result
        with patch.object(execute,'scoring_evidence',side_effect=wrong_after_write):
            with self.assertRaisesRegex(execute.ExecutionError,'SCORING_CHANGED'):
                execute.execute_plan(**args)
        self.assertEqual(inspect_database(self.target),before)

    def test_apply_never_calls_planner_or_backup_or_app_network(self):
        args = self.prepare()
        import builtins, socket
        original = builtins.__import__
        def guarded(name,*a,**kw):
            if name.split('.')[0] in {'flask','pickem_flask_htmx_tabs','requests','openai'}:
                raise AssertionError('forbidden runtime')
            return original(name,*a,**kw)
        with patch.object(execute,'_rebuild_source_plan',side_effect=AssertionError('replan')), \
             patch.object(socket.socket,'connect',side_effect=AssertionError('network')), \
             patch.object(builtins,'__import__',side_effect=guarded):
            self.assertEqual(execute.execute_plan(**args)['status'],'applied')

    def test_preparation_is_read_only_and_rejects_forged_plan(self):
        args = self.prepare()
        keys=('database_path','manifest','plan','expected_manifest_checksum','expected_plan_sha256',
              'expected_source','expected_stage1_ledger_sha256','expected_schema_sha256','checkpoint')
        prepare_args={k:deepcopy(args[k]) for k in keys}
        before=rehearsal.file_sha256(self.target)
        self.assertEqual(execute.prepare_execution(**prepare_args),args['approved_packet'])
        self.assertEqual(before,rehearsal.file_sha256(self.target))
        prepare_args['plan']['new_rows']['users'][0]['values']['is_platform_admin']=1
        prepare_args['expected_plan_sha256']=rehearsal.plan_sha256(prepare_args['plan'])
        with self.assertRaisesRegex(execute.ExecutionError,'DETERMINISTIC_PLAN_MISMATCH'):
            execute.prepare_execution(**prepare_args)

    def test_wal_target_uses_normal_read_connections(self):
        self.change('PRAGMA journal_mode=WAL')
        args = self.prepare()
        with closing(execute._connect(self.target)) as db:
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone(),('wal',))
        result=execute.execute_plan(**args)
        self.assertEqual(result['status'],'applied')
        self.assertEqual(self.verify(args)['status'],'stage2_complete')

    def test_deadline_failure_rolls_back(self):
        args = self.prepare()
        before=inspect_database(self.target)
        with patch.object(execute,'TRANSACTION_SECONDS',-1):
            with self.assertRaises(execute.ExecutionError):
                execute.execute_plan(**args)
        self.assertEqual(before,inspect_database(self.target))


if __name__=='__main__':
    unittest.main()
