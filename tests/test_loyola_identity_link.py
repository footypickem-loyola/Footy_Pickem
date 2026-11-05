"""One-time linking tests on disposable, actually backfilled synthetic Stage 2 DBs."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import socket
import sqlite3
import unittest
from unittest.mock import patch

from test_foundation_backfill_offline_rehearsal import OfflineRehearsalTests
from foundation_backfill_validation import inspect_database
from foundation_backfill_rehearsal import _restrict_new_windows_artifact
import loyola_identity_link as linker

class LinkTests(unittest.TestCase):
    manifest=OfflineRehearsalTests.manifest
    plan=OfflineRehearsalTests.plan
    clone=OfflineRehearsalTests.clone
    apply_stage2=OfflineRehearsalTests.apply

    def setUp(self):
        self.enterContext(patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK_DISABLED')))
        OfflineRehearsalTests.setUp(self)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("INSERT INTO players(id,name) VALUES(20,'Synthetic Four'),(21,'Synthetic Five'),(22,'Synthetic Six')")
        source=inspect_database(self.path)['source']
        self.doc['source']=source;self.bindings['reviewed_source']=source;self.review['reviewed_source']=source
        next(r for r in self.doc['mappings'] if r['kind']=='policy')['slug']='loyola-pick-em'
        plan=self.plan()
        target=self.enterContext(self.clone(plan))
        self.apply_stage2(target,plan)
        self.database=target.path
        self.private=Path(self.temp.name)/'private-link-review';self.private.mkdir()
        self.evidence=self.private/'synthetic-evidence.json'
        linker.write_private(self.evidence,dict(synthetic=True,description='Non-sensitive owner/identity/membership review fixture only'))
        with closing(linker.connect(self.database)) as db:
            ids=db.execute('SELECT id,user_id FROM players ORDER BY id').fetchall()
        self.mapping=dict(contract=linker.CONTRACT,issuer='https://oidc.example.invalid/',
            owner_decision=self.ref(self.evidence),links=[dict(membership_id=m,user_id=u,subject='synthetic-subject-'+str(m),identity_evidence=self.ref(self.evidence),membership_evidence=self.ref(self.evidence)) for m,u in ids[:2]])
        self.mapping_path=self.private/'mapping.json';self.packet_path=self.private/'packet.json'
        self.audit_dir=self.private/'audit';self.audit_dir.mkdir()
        self.save_mapping()
        self.prepared=self.prepare()
        self.packet_sha=self.prepared['packet_sha256']

    def ref(self,path):return dict(path=str(path),sha256=linker.sha(path))
    def save_mapping(self):
        # Only replace test-owned drafts before preparing a fresh packet.
        if self.mapping_path.exists():self.mapping_path.unlink()
        linker.write_private(self.mapping_path,self.mapping)
    def prepare(self):
        return linker.prepare(database=self.database,mapping_path=self.mapping_path,mapping_sha256=linker.sha(self.mapping_path),expected_issuer=self.mapping['issuer'],packet_path=self.packet_path)
    def apply(self,**overrides):
        args=dict(packet_path=self.packet_path,packet_sha256=self.packet_sha,audit_directory=self.audit_dir,confirmation=linker.CONFIRM,writers_fenced=True)
        args.update(overrides);return linker.apply(**args)
    def fingerprint(self):
        with closing(linker.connect(self.database)) as db:return linker.fingerprint(db)
    def change(self,sql,args=()):
        with closing(sqlite3.connect(self.database)) as db,db:db.execute(sql,args)
    def reset_packet(self):
        self.packet_path.unlink();self.save_mapping()
    def packet(self):return json.loads(self.packet_path.read_text(encoding='utf-8'))

    def test_success_activates_only_reviewed_ids_without_emails_privileges_or_history_changes(self):
        packet=self.packet();result=self.apply()
        self.assertEqual(result['status'],'applied');self.assertEqual(result['writes'],6)
        with closing(linker.connect(self.database)) as db:
            self.assertEqual(linker.protected(db,packet['links']),packet['protected_sha256'])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE status='active'").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users WHERE status='unclaimed'").fetchone()[0],4)
            self.assertEqual(db.execute('SELECT DISTINCT role,status FROM players WHERE role IS NOT NULL').fetchall(),[('member','active')])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users WHERE email IS NOT NULL OR email_verified_at IS NOT NULL OR is_platform_admin!=0').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM players WHERE recap_opt_in IS NOT NULL').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM league_notification_recipients').fetchone()[0],0)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchall(),[('ok',)])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_exact_idempotent_rerun_has_no_writes(self):
        self.apply();before=self.fingerprint();result=self.apply()
        self.assertEqual(result['status'],'already_applied');self.assertEqual(result['writes'],0)
        self.assertTrue(result['receipt_present']);self.assertEqual(before,self.fingerprint())

    def test_confirmation_and_fence_required(self):
        for args in ({'confirmation':'wrong'},{'writers_fenced':False}):
            with self.subTest(args=args),self.assertRaises(linker.LinkRefused):self.apply(**args)
        self.assertEqual(list(self.audit_dir.iterdir()),[])

    def test_wrong_user_or_other_membership_refused(self):
        self.reset_packet();self.mapping['links'][0]['user_id']=3;self.save_mapping()
        with self.assertRaisesRegex(linker.LinkRefused,'PLACEHOLDER_MEMBERSHIP_MISMATCH'):self.prepare()
        self.mapping['links'][0]['user_id']=1;self.mapping['links'][0]['membership_id']=15;self.save_mapping()
        with self.assertRaises(linker.LinkRefused):self.prepare()

    def test_duplicate_subject_or_user_or_membership_refused(self):
        original=deepcopy(self.mapping)
        self.packet_path.unlink()
        for field in ('subject','user_id','membership_id'):
            with self.subTest(field=field):
                self.mapping=deepcopy(original);self.mapping['links'][1][field]=self.mapping['links'][0][field];self.save_mapping()
                with self.assertRaisesRegex(linker.LinkRefused,'DUPLICATE_MAPPING'):self.prepare()

    def test_existing_subject_or_user_identity_refused(self):
        self.packet_path.unlink()
        link=self.mapping['links'][0]
        self.change('INSERT INTO user_identities(id,user_id,issuer,subject,created_at) VALUES(99,?,?,?,?)',(link['user_id'],'https://other.example.invalid/','existing','synthetic-time'))
        with self.assertRaisesRegex(linker.LinkRefused,'EXISTING_IDENTITY_ASSOCIATION'):self.prepare()
        self.change('DELETE FROM user_identities WHERE id=99')
        self.change('INSERT INTO user_identities(id,user_id,issuer,subject,created_at) VALUES(99,?,?,?,?)',(3,self.mapping['issuer'],link['subject'],'synthetic-time'))
        with self.assertRaisesRegex(linker.LinkRefused,'EXISTING_IDENTITY_ASSOCIATION'):self.prepare()

    def test_role_admin_and_disabled_account_changes_are_not_supported(self):
        for field,value in (('role','commissioner'),('is_platform_admin',1),('email_verified',True)):
            with self.subTest(field=field):
                invalid=deepcopy(self.mapping);invalid['links'][0][field]=value
                with self.assertRaisesRegex(linker.LinkRefused,'UNEXPECTED_FIELDS'):linker.mapping(invalid)
        self.packet_path.unlink()
        self.change('UPDATE users SET is_platform_admin=1 WHERE id=?',(self.mapping['links'][0]['user_id'],))
        with self.assertRaisesRegex(linker.LinkRefused,'UNCLAIMED_NONPRIVILEGED_USER_REQUIRED'):self.prepare()
        self.change('UPDATE users SET is_platform_admin=0 WHERE id=?',(self.mapping['links'][0]['user_id'],))
        self.change("UPDATE players SET role='commissioner' WHERE id=?",(self.mapping['links'][0]['membership_id'],))
        with self.assertRaisesRegex(linker.LinkRefused,'PLACEHOLDER_MEMBERSHIP_MISMATCH'):self.prepare()

    def test_stale_source_or_tampered_mapping_does_not_apply(self):
        before=self.fingerprint();self.change("UPDATE players SET name='Synthetic Changed' WHERE id=22")
        stale=self.fingerprint()
        with self.assertRaisesRegex(linker.LinkRefused,'STALE_SOURCE_FINGERPRINT'):self.apply()
        self.assertEqual(stale,self.fingerprint())
        self.assertNotEqual(before,stale)

    def test_transaction_failure_rolls_back_and_does_not_retry(self):
        before=self.fingerprint()
        with self.assertRaisesRegex(linker.LinkRefused,'INJECTED_FAILURE'):self.apply(fail_at='after_user')
        self.assertEqual(before,self.fingerprint())
        with self.assertRaisesRegex(linker.LinkRefused,'ATTEMPT_REQUIRES_INDEPENDENT_REVIEW'):self.apply()
        self.assertEqual(before,self.fingerprint())

    def test_failure_after_intent_before_commit_rolls_back(self):
        before=self.fingerprint()
        with self.assertRaisesRegex(linker.LinkRefused,'INJECTED_FAILURE'):self.apply(fail_at='before_commit')
        self.assertEqual(before,self.fingerprint())
        with self.assertRaisesRegex(linker.LinkRefused,'OUTCOME_OR_STATE_DRIFT_REQUIRES_REVIEW'):self.apply()

    def test_postcommit_receipt_failure_can_only_be_verified_not_reapplied(self):
        real=linker.write_private
        def fail_receipt(path,value):
            if str(path).endswith('.receipt.json'):raise OSError('synthetic receipt failure')
            return real(path,value)
        with patch.object(linker,'write_private',side_effect=fail_receipt),self.assertRaises(OSError):self.apply()
        before=self.fingerprint();result=self.apply()
        self.assertEqual(result['status'],'applied_receipt_missing_review_required');self.assertFalse(result['receipt_present'])
        self.assertEqual(before,self.fingerprint())

    def test_trigger_effects_are_refused_and_fully_rolled_back(self):
        self.packet_path.unlink()
        self.change("CREATE TRIGGER forbidden_link_trigger AFTER UPDATE ON users BEGIN UPDATE users SET email='synthetic@example.invalid',email_normalized='synthetic@example.invalid' WHERE id=NEW.id; END")
        self.prepared=self.prepare();self.packet_sha=self.prepared['packet_sha256'];before=self.fingerprint()
        with self.assertRaises(sqlite3.DatabaseError):self.apply()
        self.assertEqual(before,self.fingerprint())

    def test_evidence_and_packet_hash_tampering_refused_before_writes(self):
        before=self.fingerprint()
        with self.assertRaisesRegex(linker.LinkRefused,'ARTIFACT_HASH_MISMATCH'):self.apply(packet_sha256='0'*64)
        # This is a test-owned fake review artifact, never real owner evidence.
        self.evidence.write_text('synthetic changed',encoding='utf-8')
        with self.assertRaisesRegex(linker.LinkRefused,'REVIEW_EVIDENCE_HASH_MISMATCH'):self.apply()
        self.assertEqual(before,self.fingerprint())

    def test_present_receipt_must_be_private_valid_and_exactly_bound(self):
        self.apply();before=self.fingerprint()
        receipt=next(self.audit_dir.glob('*.receipt.json'));original=receipt.read_bytes()
        bad=json.loads(original);bad['packet_sha256']='0'*64
        receipt.write_text(json.dumps(bad),encoding='utf-8')
        with self.assertRaisesRegex(linker.LinkRefused,'AUDIT_RECEIPT_MISMATCH'):self.apply()
        receipt.write_text('{',encoding='utf-8')
        with self.assertRaises(ValueError):self.apply()
        receipt.write_bytes(original)
        import os
        alias=self.audit_dir/'synthetic-receipt-hardlink.json'
        os.link(receipt,alias)
        try:
            with self.assertRaises(ValueError):self.apply()
        finally:alias.unlink()
        real=linker.private_file
        def reject_receipt(path):
            if Path(path)==receipt:raise linker.LinkRefused('PRIVATE_FILE_ACL_REQUIRED')
            return real(path)
        with patch.object(linker,'private_file',side_effect=reject_receipt):
            with self.assertRaisesRegex(linker.LinkRefused,'PRIVATE_FILE_ACL_REQUIRED'):self.apply()
        self.assertEqual(before,self.fingerprint())

    def test_subject_is_exact_and_issuer_is_pinned(self):
        self.packet_path.unlink()
        with self.assertRaisesRegex(linker.LinkRefused,'PINNED_ISSUER_MISMATCH'):
            linker.prepare(database=self.database,mapping_path=self.mapping_path,mapping_sha256=linker.sha(self.mapping_path),expected_issuer='https://wrong.example.invalid/',packet_path=self.packet_path)
        self.mapping['links'][0]['subject']=' synthetic-subject';self.save_mapping()
        with self.assertRaisesRegex(linker.LinkRefused,'EXACT_SUBJECT_REQUIRED'):self.prepare()

if __name__=='__main__':unittest.main()
