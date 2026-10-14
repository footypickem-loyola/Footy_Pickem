"""Stage 1 safety tests: disposable files only, no app import or external clients."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import foundation_migrations as migration
from foundation_schema import ADDITIONS, TABLE_DDL, VERSION

ROOT = Path(__file__).resolve().parents[1]
LEGACY_DDL = (ROOT / 'tests/fixtures/foundation_legacy_schema.sql').read_text()


def make_legacy(path):
    """All 26 legacy ORM tables populated, including operational receipts.

Static schema is independent of current ORM metadata. Values are synthetic;
outcome-only results and serialized text are deliberately preserved verbatim.
"""
    with sqlite3.connect(path) as db:
        db.executescript(LEGACY_DDL)
        tables = migration.table_columns(db)
        pending = set(tables)
        created = set()
        while pending:
            progress = False
            for table in sorted(pending):
                fks = list(db.execute(f'PRAGMA foreign_key_list({migration.quoted(table)})'))
                if not {r[2] for r in fks} <= created:
                    continue
                columns = list(db.execute(f'PRAGMA table_info({migration.quoted(table)})'))
                values = {}
                for _, name, kind, required, _, pk in columns:
                    if required or pk:
                        values[name] = 1 if kind in ('INTEGER', 'BOOLEAN') else f'{table}:{name}'
                        if kind == 'DATETIME': values[name] = '2026-09-01 12:00:00.000000'
                        if kind == 'JSON': values[name] = '[{"preserve": "  exact text  "}]'
                values.update({r[3]: 1 for r in fks})
                values.update({
                    'players': dict(name='Steve'),
                    'seasons': dict(code='year-1', name='Year 1', is_active=0, is_archived=1),
                    'weeks': dict(number=1, status='finalized', finalized_at='2026-09-01 13:00:00.000000'),
                    'fixtures': dict(home='Arsenal', away='Chelsea', match_number=1),
                    'matchups': dict(player_b_id=2),
                    'picks': dict(team='Arsenal'),
                    'results': dict(outcome='Home', source='manual'),
                    'weekly_recaps': dict(context_json='{"keep":  "spacing"}', status='ready', body_markdown='Original recap'),
                    'correspondent_email_deliveries': dict(status='sent', gmail_message_id='sent-keep'),
                    'correspondent_x_collection_states': dict(status='completed', page_count=2),
                    'auto_draft_preferences': dict(team='Arsenal'),
                }.get(table, {}))
                names = ','.join(map(migration.quoted, values))
                db.execute(f'INSERT INTO {migration.quoted(table)} ({names}) VALUES ({",".join("?" for _ in values)})', tuple(values.values()))
                if table == 'players': db.execute("INSERT INTO players VALUES (2,'Joe')")
                created.add(table)
                pending.remove(table)
                progress = True
                break
            if not progress: raise AssertionError('Unresolved legacy dependency')
        # Extra non-ORM data/index/trigger must survive unchanged too.
        db.executescript("""CREATE TABLE private_audit(id INTEGER PRIMARY KEY AUTOINCREMENT, value BLOB);
            INSERT INTO private_audit(value) VALUES (X'0001FF');
            CREATE INDEX private_audit_value ON private_audit(value);
            CREATE TRIGGER preserve_result_audit AFTER UPDATE ON results BEGIN
                INSERT INTO private_audit(value) VALUES ('updated'); END;""")
        if db.execute('PRAGMA foreign_key_check').fetchall(): raise AssertionError('Bad test seed')


def dump(path):
    with sqlite3.connect(path) as db:
        return '\n'.join(db.iterdump())


class FoundationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'legacy.db'
        self.backup = Path(self.temp.name) / 'before.db'
        make_legacy(self.path)
        self.before = migration.inspect_database(self.path)

    def apply(self, **kwargs):
        options = dict(expected_schema=self.before['schema_sha256'], backup=self.backup, authorized=True)
        options.update(kwargs)
        return migration.apply_database(self.path, **options)

    def test_inspection_is_read_only_and_does_not_create_missing_file(self):
        original = self.path.read_bytes()
        self.assertFalse(self.before['applied'])
        migration.inspect_database(self.path)
        self.assertEqual(original, self.path.read_bytes())
        with self.assertRaises(FileNotFoundError): migration.inspect_database(self.path.with_name('missing.db'))
        self.assertFalse(self.path.with_name('missing.db').exists())

    def test_additive_preserves_every_legacy_value_identity_and_backup(self):
        original = dump(self.path)
        with sqlite3.connect(self.path) as db:
            columns = migration.table_columns(db)
            objects = {(t,n): sql for t,n,_,sql in migration.schema(db) if t in ('index','trigger','view')}
        self.assertEqual(self.apply()['status'], 'applied')
        self.assertEqual(dump(self.backup), original)
        with closing(migration.connect(self.path)) as db:
            self.assertEqual(migration.row_manifest(db, columns), self.before['tables'])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            actual_objects = {(t,n): sql for t,n,_,sql in migration.schema(db)}
            for key, sql in objects.items(): self.assertEqual(actual_objects[key], sql)
            for table, names in ADDITIONS.items():
                for name in names:
                    self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table} WHERE {name} IS NOT NULL').fetchone()[0], 0)
                    column = next(r for r in db.execute(f'PRAGMA table_info({table})') if r[1] == name)
                    self.assertEqual(column[3:5], (0, None))
            for ddl in TABLE_DDL:
                table = ddl.split()[2]
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], int(table == 'schema_migrations'))
            self.assertNotIn('league_week_result_snapshots', migration.table_columns(db))
            self.assertEqual(db.execute('SELECT id,outcome,home_score,away_score FROM results').fetchall(), [(1,'Home',None,None)])

    def test_exact_retry_is_noop_and_does_not_replace_backup(self):
        self.apply()
        after, saved = dump(self.path), self.backup.read_bytes()
        self.assertEqual(self.apply()['status'], 'already_applied')
        self.assertEqual(dump(self.path), after)
        self.assertEqual(self.backup.read_bytes(), saved)
        self.assertTrue(migration.inspect_database(self.path)['applied'])

    def test_explicit_opt_in_schema_hash_and_distinct_fresh_backup_required(self):
        for options in (dict(authorized=False), dict(expected_schema='0'*64), dict(backup=self.path), dict(backup=None)):
            with self.subTest(options=options), self.assertRaises(migration.MigrationError): self.apply(**options)
            self.assertEqual(migration.inspect_database(self.path), self.before)
        self.backup.write_bytes(b'do not replace')
        with self.assertRaises(FileExistsError): self.apply()
        self.assertEqual(self.backup.read_bytes(), b'do not replace')
        self.assertEqual(migration.inspect_database(self.path), self.before)

    def test_partial_or_foreign_schema_is_not_adopted(self):
        with sqlite3.connect(self.path) as db: db.execute('ALTER TABLE players ADD COLUMN league_id INTEGER')
        before = dump(self.path)
        with self.assertRaisesRegex(migration.MigrationError, 'already exist'): migration.inspect_database(self.path)
        with self.assertRaises(migration.MigrationError): self.apply()
        self.assertEqual(dump(self.path), before)
        self.assertFalse(self.backup.exists())

    def test_missing_current_columns_or_bad_foreign_keys_refuse_before_ddl(self):
        with sqlite3.connect(self.path) as db: db.execute('UPDATE picks SET player_id=999')
        before = dump(self.path)
        with self.assertRaisesRegex(migration.MigrationError, 'Foreign key'): self.apply()
        self.assertEqual(dump(self.path), before)
        self.assertFalse(self.backup.exists())

    def test_legacy_schema_without_current_fields_refuses_automatic_upgrade(self):
        with sqlite3.connect(self.path) as db: db.execute('ALTER TABLE fixtures DROP COLUMN venue')
        before = dump(self.path)
        with self.assertRaisesRegex(migration.MigrationError, 'incomplete'): migration.inspect_database(self.path)
        self.assertEqual(dump(self.path), before)

    def test_mid_migration_failure_rolls_back_schema_and_ledger_then_retry_succeeds(self):
        before = dump(self.path)
        valid = list(migration.statements())
        with patch.object(migration, 'statements', return_value=iter(valid[:23] + ['INVALID DDL'])):
            with self.assertRaises(sqlite3.Error): self.apply()
        self.assertEqual(dump(self.path), before)
        self.assertEqual(dump(self.backup), before)
        self.assertEqual(self.apply(backup=self.path.with_name('retry.db'))['status'], 'applied')

    def test_failure_at_commit_rolls_back_ledger_and_all_ddl(self):
        before = dump(self.path)
        real_connect = migration.connect
        class FailedCommit:
            def __init__(self, db): self.db = db
            def __getattr__(self, name): return getattr(self.db, name)
            def commit(self): raise sqlite3.OperationalError('simulated commit failure')
        def connect(path, *, writable=False):
            db = real_connect(path, writable=writable)
            return FailedCommit(db) if writable else db
        with patch.object(migration, 'connect', side_effect=connect):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'commit failure'): self.apply()
        self.assertEqual(dump(self.path), before)
        self.assertFalse(migration.inspect_database(self.path)['applied'])

    def test_drift_and_checksum_mismatch_refuse_retry(self):
        self.apply()
        with sqlite3.connect(self.path) as db: db.execute('ALTER TABLE users ADD COLUMN surprise TEXT')
        before = dump(self.path)
        with self.assertRaisesRegex(migration.MigrationError, 'drift'): self.apply()
        self.assertEqual(dump(self.path), before)
        with sqlite3.connect(self.path) as db:
            db.execute('ALTER TABLE users DROP COLUMN surprise')
            db.execute("UPDATE schema_migrations SET checksum='changed'")
        with self.assertRaisesRegex(migration.MigrationError, 'checksum'): self.apply()

    def test_unknown_ledger_refuses_instead_of_overwriting(self):
        with sqlite3.connect(self.path) as db: db.execute('CREATE TABLE schema_migrations (version TEXT)')
        with self.assertRaisesRegex(migration.MigrationError, 'ledger'): self.apply()
        self.assertFalse(self.backup.exists())

    def test_two_connections_serialize_migration_without_partial_state(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as holder:
            holder.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'): self.apply()
            holder.rollback()
        self.assertFalse(self.backup.exists())
        self.assertEqual(migration.inspect_database(self.path), self.before)

    def test_wal_database_backup_contains_committed_rows(self):
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("UPDATE players SET name='Steve WAL' WHERE id=1")
            writer.commit()
            self.assertTrue(Path(str(self.path)+'-wal').exists())
            self.apply()
            with sqlite3.connect(self.backup) as backup:
                self.assertEqual(backup.execute('SELECT name FROM players WHERE id=1').fetchone()[0], 'Steve WAL')

    def test_user_names_nonunique_membership_keys_unique_per_league_and_club_fk(self):
        self.apply()
        with closing(migration.connect(self.path, writable=True)) as db:
            db.execute("INSERT INTO clubs(id,name) VALUES(1,'Arsenal')")
            for i in (1,2):
                db.execute("INSERT INTO users(id,display_name,created_at,updated_at) VALUES(?,'Steve','now','now')", (i,))
                db.execute("INSERT INTO leagues(id,slug,name,created_at,updated_at) VALUES(?,?,'League','now','now')", (i,str(i)))
            db.execute("UPDATE players SET league_id=1,user_id=1,name_key='steve' WHERE id=1")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE players SET league_id=1,user_id=2,name_key='steve' WHERE id=2")
            db.execute("UPDATE players SET league_id=2,user_id=1,name_key='steve' WHERE id=2")
            with self.assertRaises(sqlite3.IntegrityError): db.execute('UPDATE users SET favorite_club_id=999 WHERE id=1')
            db.execute('UPDATE users SET favorite_club_id=1 WHERE id=1')
            db.execute('UPDATE users SET favorite_club_id=NULL WHERE id=1')
            # Stage 1 intentionally retains the stronger old global name rule.
            with self.assertRaises(sqlite3.IntegrityError): db.execute("INSERT INTO players(name) VALUES('Steve')")

    def test_cli_has_no_implicit_target_or_apply_default(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT), DB_PATH=str(self.path))
        for args in ([], ['inspect'], ['apply','--database',str(self.path)]):
            result = subprocess.run([sys.executable,'-m','foundation_migrations',*args], env=env,
                                    cwd=self.temp.name, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
        self.assertEqual(migration.inspect_database(self.path), self.before)

    def test_cli_inspect_and_authorized_apply_on_disposable_database(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        def run(*args):
            return subprocess.run([sys.executable,'-m','foundation_migrations',*args], env=env,
                                  cwd=self.temp.name, capture_output=True,text=True,timeout=15)
        inspected = run('inspect','--database',str(self.path))
        self.assertEqual(inspected.returncode,0,inspected.stderr)
        self.assertEqual(json.loads(inspected.stdout)['schema_sha256'],self.before['schema_sha256'])
        args=('apply','--database',str(self.path),'--expect-schema',self.before['schema_sha256'],'--backup',str(self.backup))
        self.assertEqual(run(*args).returncode,1)
        self.assertFalse(self.backup.exists())
        applied=run(*args,'--authorize-additive-schema')
        self.assertEqual(applied.returncode,0,applied.stdout+applied.stderr)
        self.assertEqual(json.loads(applied.stdout)['status'],'applied')
        self.assertEqual(json.loads(run(*args,'--authorize-additive-schema').stdout)['status'],'already_applied')


class FoundationStartupTests(unittest.TestCase):
    def test_explicit_legacy_initialization_is_separate_from_foundation(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'new-local.db'
            env=dict(os.environ,PYTHONPATH=str(ROOT),FLASK_SECRET='test-only',INIT_ON_START='0')
            env.pop('DB_PATH',None)
            command=[sys.executable,str(ROOT/'pickem_flask_htmx_tabs.py'),'--initialize-legacy-schema']
            refused=subprocess.run(command,env=env,cwd=folder,capture_output=True,text=True,timeout=30)
            self.assertNotEqual(refused.returncode,0)
            self.assertFalse((Path(folder)/'pickem.db').exists())
            env['DB_PATH']='sqlite:///'+str(path)
            result=subprocess.run(command,env=env,cwd=folder,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            with sqlite3.connect(path) as db:
                self.assertEqual(len(migration.table_columns(db)),26)
                self.assertNotIn('users',migration.table_columns(db))
                self.assertTrue(all(v['rows']==0 for v in migration.row_manifest(db,migration.table_columns(db)).values()))

    def test_web_import_main_and_create_all_are_foundation_inert(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for state in ('current','old','missing','expanded'):
                with self.subTest(state=state):
                    path = folder / f'{state}.db'
                    if state != 'missing': make_legacy(path)
                    if state == 'old':
                        with sqlite3.connect(path) as db:
                            db.execute('ALTER TABLE fixtures DROP COLUMN venue')
                            db.execute('DROP TABLE auto_draft_settings')
                    if state == 'expanded':
                        info = migration.inspect_database(path)
                        migration.apply_database(path, expected_schema=info['schema_sha256'],
                                                 backup=folder/'expanded-backup.db', authorized=True)
                    original = dump(path) if path.exists() else None
                    script = """
import sys
from unittest.mock import patch
from sqlalchemy import MetaData
with patch.object(MetaData, 'create_all', side_effect=AssertionError('startup DDL')):
    import pickem_flask_htmx_tabs as app
    import foundation_schema, foundation_migrations, run_web_with_briefs
    assert 'users' not in app.Base.metadata.tables
    with patch.object(app, 'ensure_database_schema', side_effect=AssertionError('startup migration')), patch.object(app.app, 'run'):
        sys.argv=['pickem_flask_htmx_tabs.py']
        app.main()
app.engine.dispose()
"""
                    env = dict(os.environ, PYTHONPATH=str(ROOT), DB_PATH='sqlite:///'+str(path),
                               INIT_ON_START='0', FLASK_SECRET='test-only')
                    result = subprocess.run([sys.executable,'-c',script],env=env,cwd=folder,
                                            capture_output=True,text=True,timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(dump(path) if path.exists() else None, original)

    def test_orm_create_all_does_not_register_foundation_and_old_app_can_read_after_expansion(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'game.db'
            make_legacy(path)
            info = migration.inspect_database(path)
            migration.apply_database(path, expected_schema=info['schema_sha256'], backup=Path(folder)/'backup.db',authorized=True)
            original = dump(path)
            script = """
import pickem_flask_htmx_tabs as app
app.Base.metadata.create_all(app.engine)
db=app.SessionLocal()
assert app.weekly_points_map(db, db.get(app.Week,1)) == {1:1,2:0}
assert app.payouts_for_week(db, db.get(app.Week,1))[0]['payout'] == 5
assert db.get(app.Pick,1).player_id == 1
assert db.get(app.Result,1).home_score is None
db.close(); app.SessionLocal.remove(); app.engine.dispose()
"""
            env=dict(os.environ,PYTHONPATH=str(ROOT),DB_PATH='sqlite:///'+str(path),FLASK_SECRET='test-only',INIT_ON_START='0')
            result=subprocess.run([sys.executable,'-c',script],env=env,cwd=folder,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(dump(path),original)


if __name__ == '__main__':
    unittest.main()
