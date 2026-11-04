"""Focused synthetic qualification of the separate v3 no-send planning contract."""
from contextlib import closing
from copy import deepcopy
import hashlib
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_foundation_backfill as fixtures
from foundation_backfill_manifest import (ManifestError, OFFLINE_CONTRACT,
    manifest_checksum, validate_planning_manifest, validate_offline_planning_manifest)
from foundation_backfill_planner import plan_database, plan_offline_database
from foundation_backfill_validation import InspectionError, inspect_database
from foundation_backfill_rehearsal import _inputs, RehearsalError


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sign(doc):
    doc['checksum'] = manifest_checksum(doc)
    return doc


class OfflineTests(unittest.TestCase):
    manifest = fixtures.PlannerTests.manifest

    def setUp(self):
        fixtures.PlannerTests.setUp(self)
        self.v2 = deepcopy(self.doc)
        self.owner = Path(self.temp.name) / 'synthetic-owner.json'
        self.audit = Path(self.temp.name) / 'synthetic-audit.json'
        self.owner.write_text('{"synthetic": "offline owner decision"}')
        self.audit.write_text('{"synthetic": "private review evidence"}')
        self.bindings = dict(reviewed_source=deepcopy(self.doc['source']),
                            owner_decision_sha256=sha(self.owner),
                            audit_evidence_sha256=sha(self.audit))
        self.doc['schema_version'] = 3
        self.doc['mappings'] = [r for r in self.doc['mappings'] if r['kind'] != 'recipient']
        self.doc['evidence'] = [e for e in self.doc['evidence'] if e['kind'] != 'permission']
        self.doc['evidence'] += [dict(kind='product_decision', sha256=self.bindings[k])
                                for k in ('owner_decision_sha256', 'audit_evidence_sha256')]
        self.policy = next(r for r in self.doc['mappings'] if r['kind'] == 'policy')
        self.policy['evidence'] = [self.bindings['owner_decision_sha256']]
        self.doc['offline_no_send'] = dict(contract=OFFLINE_CONTRACT, scope='offline_rehearsal',
            mode='disabled_no_send', **{k:v for k,v in self.bindings.items() if k != 'reviewed_source'})
        sign(self.doc)
        # Platform ACL behavior already has operator coverage; these tests exercise
        # synthetic planner semantics without depending on a developer's temp ACL.
        self.enterContext(patch('foundation_backfill_operator.private_directory'))

    def validate(self):
        return validate_offline_planning_manifest(sign(self.doc), **self.bindings)

    def plan(self, inspection=None, **overrides):
        kwargs = dict(self.bindings, checkpoint_sha256=sha(self.path),
                      owner_decision_path=self.owner, audit_evidence_path=self.audit)
        kwargs.update(overrides)
        return plan_offline_database(self.path, sign(self.doc),
            inspection if inspection is not None else inspect_database(self.path), **kwargs)

    def test_zero_recipients_valid_offline_plan_only(self):
        self.assertEqual(self.validate()['status'], 'valid')
        before = sha(self.path)
        p = self.plan()
        self.assertEqual(p['status'], 'planned')
        self.assertEqual(p['plan_version'], 3)
        self.assertEqual(p['blockers'], [])
        self.assertEqual(p['new_rows'].get('league_notification_recipients', []), [])
        self.assertFalse(p['email_send_authorized'])
        self.assertFalse(p['execution_authorized'])
        self.assertTrue(p['offline_only'])
        self.assertEqual(before, sha(self.path))

    def test_v2_missing_recipients_still_refused(self):
        self.v2['mappings'] = [r for r in self.v2['mappings'] if r['kind'] != 'recipient']
        self.assertIn('RECIPIENT_PERMISSION_MANIFEST_REQUIRED',
                      fixtures.codes(validate_planning_manifest(sign(self.v2))))

    def test_v2_valid_behavior_preserved(self):
        self.assertEqual(validate_planning_manifest(self.v2)['status'], 'valid')
        self.assertEqual(plan_database(self.path, self.v2, inspect_database(self.path))['status'], 'planned')

    def test_v2_planner_and_execution_inputs_refuse_v3(self):
        with self.assertRaises(ManifestError): validate_planning_manifest(self.doc)
        with self.assertRaises(ManifestError): plan_database(self.path, self.doc, {})
        with self.assertRaises(RehearsalError): _inputs(self.doc, {}, self.doc['checksum'], '0'*64)

    def test_explicit_disposition_and_offline_scope_required(self):
        original = deepcopy(self.doc)
        for field, value in [('contract', 'other'), ('scope', 'production'), ('mode', 'send')]:
            with self.subTest(field=field):
                self.doc = deepcopy(original)
                self.doc['offline_no_send'][field] = value
                with self.assertRaises(ManifestError): self.validate()
        self.doc = deepcopy(original)
        del self.doc['offline_no_send']
        with self.assertRaises(ManifestError): self.validate()

    def test_exact_reviewed_source_required(self):
        self.doc['source']['data_sha256'] = '0'*64
        with self.assertRaisesRegex(ManifestError, 'SOURCE_FINGERPRINT_MISMATCH'): self.validate()

    def test_review_references_and_policy_evidence_required(self):
        original = deepcopy(self.doc)
        for change in ('owner', 'audit', 'evidence', 'policy'):
            with self.subTest(change=change):
                self.doc = deepcopy(original)
                if change in ('owner','audit'):
                    key = 'owner_decision_sha256' if change == 'owner' else 'audit_evidence_sha256'
                    self.doc['offline_no_send'][key] = '0'*64
                elif change == 'evidence':
                    self.doc['evidence'] = [e for e in self.doc['evidence']
                        if e['sha256'] != self.bindings['audit_evidence_sha256']]
                else:
                    next(r for r in self.doc['mappings'] if r['kind']=='policy')['evidence'] = []
                with self.assertRaises(ManifestError): self.validate()

    def test_setup_and_disabled_delivery_sync_required(self):
        for field, value in [('status','active'), ('correspondent_enabled',1), ('sync_enabled',1)]:
            with self.subTest(field=field):
                old = self.policy[field]
                self.policy[field] = value
                with self.assertRaises(ManifestError): self.validate()
                self.policy[field] = old

    def test_even_disabled_recipient_forbidden(self):
        row = deepcopy(next(r for r in self.v2['mappings'] if r['kind']=='recipient'))
        row['enabled'] = 0
        self.doc['mappings'].append(row)
        self.doc['evidence'] += [e for e in self.v2['evidence'] if e['kind']=='permission']
        with self.assertRaisesRegex(ManifestError, 'OFFLINE_RECIPIENTS_FORBIDDEN'): self.validate()

    def test_checksum_tampering_refused(self):
        self.doc['checksum'] = '0'*64
        with self.assertRaisesRegex(ManifestError, 'MANIFEST_CHECKSUM_MISMATCH'):
            validate_offline_planning_manifest(self.doc, **self.bindings)

    def test_file_tamper_and_sidecars_refused(self):
        with self.assertRaisesRegex(InspectionError, 'OFFLINE_ARTIFACT_HASH_MISMATCH'):
            self.plan(checkpoint_sha256='0'*64)
        self.owner.write_text('synthetic changed')
        with self.assertRaisesRegex(InspectionError, 'OFFLINE_ARTIFACT_HASH_MISMATCH'): self.plan()
        self.owner.write_text('{"synthetic": "offline owner decision"}')
        sidecar = Path(str(self.path)+'-wal')
        sidecar.write_bytes(b'synthetic')
        try:
            with self.assertRaisesRegex(InspectionError, 'OFFLINE_STANDALONE_REQUIRED'):
                self.plan(inspection={})
        finally: sidecar.unlink()

    def test_inspection_and_mapping_guards_preserved(self):
        with self.assertRaisesRegex(InspectionError, 'SOURCE_FINGERPRINT_MISMATCH'):
            self.plan(inspection={})
        row = next(r for r in self.doc['mappings'] if r['kind']=='fixture')
        row['home'] = 'Synthetic conflicting identity'
        self.assertEqual(self.plan()['status'], 'blocked')

    def test_expired_x_claim_gate_preserved(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            # Full Stage 1 synthetic schema; never a real claim or provider token.
            values = dict(id=2, week_id=10, status='claimed', lease_token='synthetic',
                          lease_expires_at='2000-01-01', page_count=0,
                          retrieved_count=0, persisted_count=0)
            for _, name, kind, required, default, pk in db.execute(
                    'PRAGMA table_info(correspondent_x_collection_states)'):
                if required and name not in values:
                    values[name] = 0 if kind in {'INTEGER','BOOLEAN'} else '2000-01-01'
            names = tuple(values)
            db.execute('INSERT INTO correspondent_x_collection_states (' + ','.join(names) +
                       ') VALUES (' + ','.join('?' for _ in names) + ')',
                       tuple(values[n] for n in names))
        current = inspect_database(self.path)
        self.doc['source'] = deepcopy(current['source'])
        self.bindings['reviewed_source'] = deepcopy(current['source'])
        p = self.plan()
        self.assertEqual(p['status'], 'gated')
        self.assertIn('X_CLAIM_REVALIDATION_AND_FENCING_REQUIRED',
                      {g['code'] for g in p['execution_gates']})
        # A live claim remains a hard blocker, not an offline exemption.
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE correspondent_x_collection_states SET lease_expires_at='2099-01-01' WHERE id=2")
        current = inspect_database(self.path)
        self.doc['source'] = deepcopy(current['source'])
        self.bindings['reviewed_source'] = deepcopy(current['source'])
        blocked = self.plan()
        self.assertEqual(blocked['status'], 'blocked')
        self.assertIn('UNSUPPORTED_OR_IN_FLIGHT_TASK', fixtures.codes(blocked))


if __name__ == '__main__': unittest.main()
