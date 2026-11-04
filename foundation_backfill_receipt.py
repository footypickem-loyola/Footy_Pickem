"""Evidence-bound Stage 2 lifecycle verification; no database writer or CLI.

Receipt checksum contract: SHA256 of UTF-8 canonical_json(metadata), with
metadata exactly:
  version: "private_beta_002_backfill"
  contract: "stage2-backfill-receipt-v1"
  manifest_checksum: validated reviewed manifest checksum
  canonical_plan_sha256: SHA256 of the canonical plan bytes (not summary digest)
  source: {schema_sha256, data_sha256} from that approved pre-backfill plan

No private contents or addresses enter the ledger. applied_at is a frozen UTC
invocation timestamp, excluded from the checksum so crash recovery can verify
the same receipt from reviewed hashes. Reruns read and preserve that timestamp.

verify_state requires all reviewed identities and checks exact Stage 1-only
baseline or exact completed population plus receipt. Stage 1's ordinary verifier
stays strict; its optional stage2_evidence path delegates here. Schema/DDL and
Stage 1 checksum semantics are unchanged. No arbitrary extra ledger rows pass.
"""
from datetime import datetime, timezone
import hashlib
import json
import sqlite3

from foundation_backfill_manifest import canonical_json, validate_planning_manifest, ManifestError
from foundation_backfill_validation import FOUNDATION, KNOWN_TABLES, _token
from foundation_backfill_synthetic import _columns, _primary_key
from foundation_migrations import (
    VERSION as STAGE1_VERSION, digest, quoted, schema, row_manifest,
    migration_checksum, check_integrity, validate_legacy, MigrationError,
)
from foundation_schema import ADDITIONS

VERSION = 'private_beta_002_backfill'
CONTRACT = 'stage2-backfill-receipt-v1'


class ReceiptError(ValueError):
    """Fixed reason codes only."""


def checksum_metadata(manifest_checksum, canonical_plan_sha256, source):
    return dict(version=VERSION, contract=CONTRACT, manifest_checksum=manifest_checksum,
                canonical_plan_sha256=canonical_plan_sha256, source=dict(source))


def checksum(manifest_checksum, canonical_plan_sha256, source):
    return hashlib.sha256(canonical_json(
        checksum_metadata(manifest_checksum, canonical_plan_sha256, source)).encode()).hexdigest()


def _fingerprint_rows(rows):
    h = hashlib.sha256()
    count = 0
    for row in rows:
        values = [dict(blob=v.hex()) if isinstance(v, bytes) else v for v in row]
        h.update((json.dumps(values, ensure_ascii=False, separators=(',', ':')) + '\n').encode())
        count += 1
    return dict(rows=count, legacy_values_sha256=h.hexdigest())


def _stage1_fingerprint(db, names):
    fields = ','.join(map(quoted, names))
    return _fingerprint_rows(db.execute(
        f'SELECT {fields} FROM schema_migrations WHERE version=? ORDER BY {fields}', (STAGE1_VERSION,)))


def source_projection(db, columns):
    """Project only approved Stage 2 additions and its already-validated receipt."""
    result = {}
    for table, names in sorted(columns.items()):
        if table == 'schema_migrations':
            result[table] = _stage1_fingerprint(db, names)
        elif table in FOUNDATION:
            result[table] = _fingerprint_rows([])
        else:
            fields = ','.join('NULL' if c in ADDITIONS.get(table, {}) else quoted(c) for c in names)
            result[table] = _fingerprint_rows(db.execute(f'SELECT {fields} FROM {quoted(table)} ORDER BY {fields}'))
    return dict(schema_sha256=digest(schema(db)), data_sha256=digest(result))


def _expected_population(db, plan, columns):
    for table in sorted(FOUNDATION - {'schema_migrations'}):
        names = columns[table]
        actual = [dict(zip(names, row)) for row in db.execute(
            f'SELECT {",".join(map(quoted,names))} FROM {quoted(table)}')]
        expected = [r['values'] for r in plan['new_rows'].get(table, [])]
        if canonical_json(actual) != canonical_json(expected):
            raise ReceiptError('RECEIPT_POPULATION_MISMATCH')
    for table, added in ADDITIONS.items():
        keys = _primary_key(db, table)
        expected = {}
        for record in plan['nullable_updates'].get(table, []):
            key = tuple(record['key'][k] for k in keys)
            if key in expected:
                raise ReceiptError('DUPLICATE_UPDATE_KEY')
            expected[key] = record['values']
        names, found = keys + tuple(added), set()
        for row in db.execute(f'SELECT {",".join(map(quoted,names))} FROM {quoted(table)}'):
            key = tuple(row[:len(keys)])
            if tuple(expected.get(key, {}).get(c) for c in added) != tuple(row[len(keys):]):
                raise ReceiptError('RECEIPT_POPULATION_MISMATCH')
            found.add(key)
        if set(expected) - found:
            raise ReceiptError('RECEIPT_POPULATION_MISMATCH')


def verify_state(db, *, manifest, plan, expected_manifest_checksum,
                 expected_plan_sha256, require_completed=False, offline_review=None):
    """Caller owns a stable read/write transaction; this function only reads.

    Stage 1 receipt preservation includes every cell, including applied_at and
    before_schema_hash, through the approved source data fingerprint (including the entire ledger row).
    A valid receipt alone is never sufficient proof of completed population.
    """
    try:
        if offline_review is None:
            validation = validate_planning_manifest(manifest)
        else:
            # Read-only verification admission only; production execution never
            # supplies this argument and retains its strict v2 admission.
            from foundation_backfill_rehearsal import _inputs
            _, _, validation = _inputs(manifest, plan, expected_manifest_checksum,
                                       expected_plan_sha256, offline_review=offline_review)
        if validation['status'] != 'valid' or manifest['checksum'] != expected_manifest_checksum:
            raise ReceiptError('MANIFEST_IDENTITY_MISMATCH')
        if hashlib.sha256(canonical_json(plan).encode()).hexdigest() != expected_plan_sha256:
            raise ReceiptError('PLAN_IDENTITY_MISMATCH')
        if (plan['manifest_checksum'] != expected_manifest_checksum or plan['source'] != manifest['source'] or
                plan['status'] not in {'planned', 'gated'} or plan['blockers'] or
                plan['counts_are_complete'] is not True or plan['execution_authorized'] is not False):
            raise ReceiptError('INVALID_REVIEWED_PLAN')
        check_integrity(db)
        validate_legacy(db)
        columns = _columns(db)
        if not FOUNDATION <= columns.keys():
            raise ReceiptError('FOUNDATION_SCHEMA_MISMATCH')
        actual_schema = digest(schema(db))
        if actual_schema != plan['source']['schema_sha256']:
            raise ReceiptError('RECEIPT_SCHEMA_MISMATCH')
        rows = db.execute('SELECT version,checksum,applied_at,before_schema_hash,after_schema_hash '
                          'FROM schema_migrations ORDER BY version').fetchall()
        versions = [r[0] for r in rows]
        if versions not in ([STAGE1_VERSION], sorted([STAGE1_VERSION, VERSION])):
            raise ReceiptError('UNAPPROVED_LEDGER_HISTORY')
        stage1 = next(r for r in rows if r[0] == STAGE1_VERSION)
        if stage1[1] != migration_checksum() or stage1[4] != actual_schema:
            raise ReceiptError('STAGE1_RECEIPT_CHANGED')
        receipt = next((r for r in rows if r[0] == VERSION), None)
        if receipt is not None:
            if receipt[1] != checksum(expected_manifest_checksum, expected_plan_sha256, plan['source']):
                raise ReceiptError('RECEIPT_CHECKSUM_MISMATCH')
            if receipt[3] != receipt[4] or receipt[3] != actual_schema:
                raise ReceiptError('RECEIPT_SCHEMA_MISMATCH')
            try:
                timestamp = datetime.fromisoformat(receipt[2])
                if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
                    raise ValueError()
            except (ValueError, TypeError, OverflowError):
                raise ReceiptError('RECEIPT_TIMESTAMP_INVALID') from None
        elif require_completed:
            raise ReceiptError('STAGE2_RECEIPT_REQUIRED')
        if source_projection(db, columns) != plan['source']:
            raise ReceiptError('PROTECTED_STATE_CHANGED')
        protected_columns = {t: tuple(c for c in names if c not in ADDITIONS.get(t, {}))
                             for t, names in columns.items() if t not in FOUNDATION}
        protected = {_token(t, KNOWN_TABLES): v for t, v in row_manifest(db, protected_columns).items()}
        # The pre-backfill inspector excludes foundation tables, including ledger,
        # from protected_fingerprints. Source projection separately covers ledger.
        expected_protected = plan['preservation']['protected_fingerprints']
        if protected != expected_protected:
            raise ReceiptError('PROTECTED_STATE_CHANGED')
        full = row_manifest(db, columns)
        if receipt is None:
            if dict(schema_sha256=actual_schema, data_sha256=digest(full)) != plan['source']:
                raise ReceiptError('POPULATION_WITHOUT_RECEIPT')
            status = 'stage1_only'
        else:
            _expected_population(db, plan, columns)
            status = 'stage2_complete'
        return dict(status=status, execution_authorized=False,
                    receipt=None if receipt is None else dict(zip(
                        ('version','checksum','applied_at','before_schema_hash','after_schema_hash'),receipt)),
                    source=plan['source'], post_state=dict(schema_sha256=actual_schema,data_sha256=digest(full)),
                    protected_sha256=digest(protected), integrity='ok', foreign_key_violations=0,
                    **({'offline_rehearsal_only': True, 'email_send_authorized': False}
                       if offline_review is not None else {}))
    except ReceiptError:
        raise
    except (ManifestError, MigrationError, sqlite3.Error, ValueError, TypeError, KeyError, IndexError):
        raise ReceiptError('STAGE2_VERIFICATION_REFUSED') from None
