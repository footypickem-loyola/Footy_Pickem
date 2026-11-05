"""Post-Stage-2 rebuild tests; actually backfilled disposable synthetic files only."""
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
import shutil
import unittest
from unittest.mock import patch
from test_foundation_backfill_offline_rehearsal import OfflineRehearsalTests
import foundation_backfill_rehearsal as rehearsal
from foundation_migrations import digest,schema
import foundation_league_keys as keys


class LeagueKeysTests(unittest.TestCase):
    use_synthetic_writer=True
    extra_schema="""CREATE INDEX preserved_name_lookup ON players(name);
    CREATE TRIGGER preserved_player_trigger AFTER UPDATE OF name ON players BEGIN SELECT 1; END;
    CREATE VIEW preserved_player_view AS SELECT id,name FROM players;"""

    def populate_preserved_history(self,db):pass

    manifest=OfflineRehearsalTests.manifest
    plan=OfflineRehearsalTests.plan
    clone=OfflineRehearsalTests.clone
    apply_stage2=OfflineRehearsalTests.apply

    def setUp(self):
        OfflineRehearsalTests.setUp(self)
        plan=self.plan();target=self.enterContext(self.clone(plan));self.apply_stage2(target,plan)
        self.path=target.path
        self.evidence=dict(manifest=self.doc,plan=plan,expected_manifest_checksum=self.doc['checksum'],
                           expected_plan_sha256=rehearsal.plan_sha256(plan),offline_review=self.review)
        self.enterContext(patch.object(keys,'private_directory',side_effect=lambda p:Path(p).resolve()))
        self.packet=keys.prepare(self.path,stage2_evidence=self.evidence)
        self.hash=digest(self.packet);self.backup=self.path.parent/'before-league-keys.db'

    def apply(self,**kw):
        args=dict(packet=self.packet,expected_packet_sha256=self.hash,stage2_evidence=self.evidence,
                  checkpoint=self.backup,authorized=True,writers_fenced=True,offline=True)
        args.update(kw);return keys.apply(self.path,**args)

    def snapshot(self):
        with closing(sqlite3.connect(self.path)) as db:
            return dict(schema=schema(db),rows=keys.fingerprints(db),ledger=keys.ledger(db))

    def test_rebuild_preserves_rows_foreign_keys_indexes_receipts_and_idempotency(self):
        before=self.snapshot();self.assertEqual(self.apply()['status'],'applied')
        after=self.snapshot();self.assertEqual(before['rows'],after['rows'])
        self.assertEqual(before['ledger'],[r for r in after['ledger'] if r[0]!=keys.VERSION])
        self.assertEqual(self.apply()['status'],'already_applied');self.assertEqual(after,self.snapshot())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchall(),[('ok',)])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertIn('uix_foundation_membership_name',{r[1] for r in db.execute('PRAGMA index_list(players)')})
            self.assertIn('uix_league_season_code',{r[1] for r in db.execute('PRAGMA index_list(seasons)')})
        restore=self.path.parent/'independent-restore.db'
        with self.backup.open('rb') as src,restore.open('xb') as dst:shutil.copyfileobj(src,dst)
        with closing(sqlite3.connect(restore)) as db:
            self.assertEqual(keys.fingerprints(db),before['rows'])
            self.assertEqual(schema(db),before['schema'])
            self.assertEqual(keys.verify_state(db,**dict(self.evidence,require_completed=True))['status'],'stage2_complete')

    def test_atomic_rollback_after_each_drop_preserves_everything(self):
        for table in ('players','seasons'):
            before=self.snapshot()
            with self.assertRaisesRegex(keys.CompatibilityError,'INJECTED_FAILURE'):
                self.apply(checkpoint=self.path.parent/('before-'+table+'.db'),fail_at=table)
            self.assertEqual(before,self.snapshot())

    def test_wrong_packet_missing_authorization_and_schema_drift_refuse(self):
        before=self.snapshot()
        for kw in ({'authorized':False},{'authorized':'false'},{'writers_fenced':False},{'writers_fenced':'true'},{'expected_packet_sha256':'0'*64},{'offline':False},{'offline':'true'}):
            with self.assertRaises(keys.CompatibilityError):self.apply(**kw)
            self.assertEqual(before,self.snapshot())
        with closing(sqlite3.connect(self.path)) as db,db:db.execute('CREATE INDEX unexpected_index ON players(name)')
        with self.assertRaisesRegex(keys.CompatibilityError,'BASE_SCHEMA_DRIFT'):self.apply()

    def test_unknown_ledger_and_partial_schema_refuse_rerun(self):
        self.apply()
        with closing(sqlite3.connect(self.path)) as db,db:db.execute("INSERT INTO schema_migrations VALUES('unknown','x','x','x','x')")
        with self.assertRaisesRegex(keys.CompatibilityError,'STAGE2_LEDGER_CHANGED'):self.apply()
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("DELETE FROM schema_migrations WHERE version='unknown'")
            db.execute('DROP INDEX uix_league_season_code')
        with self.assertRaisesRegex(keys.CompatibilityError,'POST_SCHEMA_DRIFT'):self.apply()

    def test_dependency_preservation_and_referenced_global_key_refusal(self):
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(':memory:',isolation_level=None)) as trial:
            source.backup(trial)
            trial.execute('PRAGMA foreign_keys=OFF');trial.execute('PRAGMA legacy_alter_table=ON')
            trial.execute('CREATE TABLE dependent_child(id INTEGER PRIMARY KEY,player_id INTEGER REFERENCES players(id) ON DELETE CASCADE)')
            trial.execute('INSERT INTO dependent_child SELECT 1,id FROM players LIMIT 1')
            trial.execute('BEGIN IMMEDIATE');keys.rebuild(trial)
            self.assertEqual(trial.execute('SELECT COUNT(*) FROM dependent_child').fetchone()[0],1)
            trial.rollback()
            trial.execute('CREATE TABLE unsafe_reference(id INTEGER PRIMARY KEY,name TEXT REFERENCES players(name))')
            with self.assertRaisesRegex(keys.CompatibilityError,'REFERENCED_GLOBAL_KEY'):keys.rebuild(trial)

    def test_prepare_requires_actual_completed_stage2_evidence(self):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("DELETE FROM schema_migrations WHERE version='private_beta_002_backfill'")
        with self.assertRaises(ValueError):keys.prepare(self.path,stage2_evidence=self.evidence)

    def test_stale_data_and_tampered_stage2_inputs_refuse(self):
        wrong=deepcopy(self.evidence)
        wrong['plan']['counts_are_complete']=False
        with self.assertRaises(ValueError):self.apply(stage2_evidence=wrong)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("UPDATE players SET name='Changed synthetic history' WHERE id=9")
        with self.assertRaises(ValueError):self.apply()
        self.assertFalse(self.backup.exists())

if __name__=='__main__':unittest.main()
