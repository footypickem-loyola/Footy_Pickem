"""Synthetic offline guard tests; never opens production-derived artifacts."""
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import foundation_backfill_operator as operator
from foundation_backfill_smoke import SmokeGuard, fingerprint
import foundation_backfill_execute as execute
import test_foundation_backfill_execute as fixtures

ROOT=Path(__file__).resolve().parents[1]


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.sessions=self.root/'sessions';self.sessions.mkdir()
        self.db=self.root/'synthetic.db'
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute('CREATE TABLE values_to_preserve(id INTEGER PRIMARY KEY,value TEXT)')
            db.execute("INSERT INTO values_to_preserve VALUES(1,'preserve')")

    def test_all_sql_writes_and_swallowed_pragma_attempts_recorded(self):
        before=fingerprint(self.db)
        commands=["UPDATE values_to_preserve SET value='changed'",'CREATE TABLE bad(id)',
                  'PRAGMA journal_mode=WAL','PRAGMA synchronous=NORMAL','PRAGMA user_version=7',
                  'PRAGMA query_only=OFF',"ATTACH DATABASE ':memory:' AS other",'VACUUM']
        with SmokeGuard([self.db],self.sessions) as guard:
            db=sqlite3.connect(self.db)
            for sql in commands:
                try:db.execute(sql)
                except sqlite3.DatabaseError:pass
            self.assertEqual(db.execute('SELECT value FROM values_to_preserve').fetchone(),('preserve',))
        self.assertGreaterEqual(len(guard.attempts),len(commands))
        self.assertEqual(before,fingerprint(self.db))

    def test_clone_initialization_never_allows_source_access_or_sql_writes(self):
        from foundation_backfill_smoke import consistent_copy
        area=self.root/'copies';area.mkdir()
        clone=area/'clone.db';consistent_copy(self.db,clone)
        before=fingerprint(self.db);original=self.db.read_bytes()
        with SmokeGuard([clone],self.sessions,clone_root=area,protected_sources=[self.db]) as guard:
            db=sqlite3.connect(clone)
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone(),('wal',))
            db.execute('PRAGMA synchronous=NORMAL')
            with self.assertRaises(sqlite3.DatabaseError):db.execute('DELETE FROM values_to_preserve')
            with self.assertRaises(operator.Refusal):sqlite3.connect(self.db)
            with self.assertRaises(operator.Refusal):self.db.write_bytes(b'bad')
            with self.assertRaises(operator.Refusal):self.db.read_bytes()
            with self.assertRaises(operator.Refusal):socket.create_connection(('example.invalid',443))
            with self.assertRaises(operator.Refusal):subprocess.run([sys.executable,'-c','pass'])
        self.assertEqual(before,fingerprint(self.db));self.assertEqual(original,self.db.read_bytes())
        self.assertEqual(before,fingerprint(clone))
        self.assertEqual(len(guard.initialization),2)

    def test_sqlite_backup_includes_committed_wal(self):
        from foundation_backfill_smoke import consistent_copy
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute("INSERT INTO values_to_preserve VALUES(2,'wal row')");db.commit()
            clone=self.root/'wal-copy.db';consistent_copy(self.db,clone)
            self.assertEqual(fingerprint(self.db),fingerprint(clone))
            with self.assertRaises(FileExistsError):consistent_copy(self.db,clone)

    def test_network_subprocess_native_and_background_threads_block(self):
        import ctypes,threading
        with SmokeGuard([self.db],self.sessions) as guard:
            for action in (lambda:socket.create_connection(('example.invalid',443)),
                           lambda:socket.getaddrinfo('example.invalid',443),
                           lambda:subprocess.run([sys.executable,'-c','pass']),
                           lambda:os.system('echo should-not-run'),
                           lambda:ctypes.CDLL('nonexistent-smoke-library'),lambda:threading.Thread(target=lambda:None).start()):
                with self.assertRaises(operator.Refusal):action()
        self.assertGreaterEqual(len(guard.attempts),6)

    def test_direct_sqlite_alias_guard_removal_and_unapproved_targets_block(self):
        direct=sqlite3.connect
        with SmokeGuard([self.db],self.sessions) as guard:
            for action in (lambda:direct(self.db),lambda:sqlite3.connect(':memory:'),
                           lambda:sqlite3.connect(self.db).set_authorizer(None),
                           lambda:sqlite3.connect(self.db).enable_load_extension(True)):
                with self.assertRaises(operator.Refusal):action()
        self.assertEqual(len(guard.attempts),4)

    def test_identity_checked_session_fdopen_and_outside_fd_refusal(self):
        with SmokeGuard([self.db],self.sessions) as guard:
            fd=os.open(self.sessions/'allowed',os.O_CREAT|os.O_WRONLY,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(b'session')
            fd=os.open(self.db,os.O_RDONLY)
            try:
                with self.assertRaises(operator.Refusal):os.fdopen(fd,'wb')
            finally:os.close(fd)
        self.assertIn('filesystem_write',guard.attempts)

    def test_only_private_session_files_may_be_written(self):
        with SmokeGuard([self.db],self.sessions) as guard:
            (self.sessions/'session').write_bytes(b'disposable')
            with self.assertRaises(operator.Refusal):(self.root/'outside').write_bytes(b'bad')
            with self.assertRaises(operator.Refusal):self.db.unlink()
        self.assertFalse((self.root/'outside').exists())
        self.assertTrue(self.db.exists())


class SmokeTests(unittest.TestCase):
    setUp=fixtures.ExecuteTests.setUp
    manifest=fixtures.ExecuteTests.manifest
    row=fixtures.ExecuteTests.row
    plan=fixtures.ExecuteTests.plan
    change=fixtures.ExecuteTests.change
    prepare=fixtures.ExecuteTests.prepare

    def request(self):
        args=self.prepare();execute.execute_plan(**args)
        from shared_brief_store import initialize
        from shared_brief_operations import initialize as initialize_scheduler
        root=Path(self.temp.name);brief=root/'brief.db';scheduler=root/'scheduler.db'
        initialize(brief);initialize_scheduler(scheduler)
        stores={'database':self.target,'brief_store':brief,'scheduler_store':scheduler}
        request=dict(acknowledge_disposable=True,disposable_root=str(root),
            **{k:str(p) for k,p in stores.items()},expected_file_sha256={k:operator.sha(p.read_bytes()) for k,p in stores.items()},
            application_root=str(ROOT),application_sha256=operator.sha((ROOT/'pickem_flask_htmx_tabs.py').read_bytes()),
            stage2_evidence={k:args[k] for k in ('manifest','plan','expected_manifest_checksum','expected_plan_sha256')},
            player_id=2,week_number=1)
        return request

    def invoke(self,request):
        p=Path(self.temp.name)/'smoke-request.json';p.write_bytes(operator.canonical(request))
        # Fresh process, no environment/site-path inherited application modules.
        launch="import sys;sys.path.insert(0,sys.argv.pop(1));from foundation_backfill_smoke import main;raise SystemExit(main())"
        return subprocess.run([sys.executable,'-I','-B','-c',launch,str(ROOT),str(p)],cwd=self.temp.name,
                              capture_output=True,text=True,timeout=60)

    def test_representative_render_on_verified_copies_preserves_sources(self):
        result=self.invoke(self.request())
        self.assertEqual(result.stderr,'')
        report=json.loads(result.stdout)
        self.assertTrue(report.get('render_paths_passed'),report)
        self.assertTrue(report['fingerprints_unchanged'],report)
        self.assertTrue(report['completed_stage2_state_valid'])
        self.assertEqual(set(report['fingerprints_before']),{'database','brief_store','scheduler_store'})
        self.assertEqual(report['fingerprints_before'],report['fingerprints_after'])
        self.assertEqual(report['blocked_attempts'],[])
        self.assertTrue(report['source_fingerprints_unchanged'])
        self.assertEqual(report['source_bytes_before'], report['source_bytes_after'])
        self.assertEqual(report['source_fingerprints_before'], report['source_fingerprints_after'])
        self.assertTrue(report['copies_verified_before_import'])
        self.assertEqual({x['pragma'] for x in report['disposable_initialization']}, {'journal_mode','synchronous'})
        self.assertEqual({x['store'] for x in report['disposable_initialization']}, {'database.db'})
        self.assertEqual(report['status'],'passed')
        self.assertFalse(report['writer_resume_authorized'])
        self.assertEqual(result.returncode,0)
        self.assertTrue(report['responses'][-1]['authenticated'])

    def test_wrong_artifact_hash_refuses_before_application_import(self):
        request=self.request();request['expected_file_sha256']['database']='0'*64
        result=self.invoke(request)
        self.assertEqual(result.returncode,2)
        self.assertEqual(json.loads(result.stdout)['detail'],'SMOKE_REFUSED')

    def test_non_disposable_target_or_missing_ack_refused(self):
        request=self.request();request['acknowledge_disposable']=False
        self.assertEqual(self.invoke(request).returncode,2)
        request['acknowledge_disposable']=True;request['disposable_root']=str(ROOT)
        self.assertEqual(self.invoke(request).returncode,2)


if __name__=='__main__':unittest.main()
