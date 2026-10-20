"""Explicit maintenance API; no CLI, startup hook, implicit target or retries.

prepare_execution is the read-only planning/review phase. execute_plan consumes
its unchanged packet and a separately approved packet hash; it never plans.
Acknowledgements are operator attestations, not proof of remote writer fences.
All eight operational gates require independently reviewed evidence digests,
bound to this exact target/source/plan/checkpoint packet. Offline simulation
never clears a production gate. No environment variables or services are read.
"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
from pathlib import Path
import re
import sqlite3
import time

from foundation_backfill_manifest import canonical_json
from foundation_backfill_rehearsal import _inputs, GATES, file_sha256, plan_sha256
from foundation_backfill_rehearsal_scoring import scoring_evidence
from foundation_backfill_validation import inspect_connection, _read_guard
from foundation_backfill_synthetic import (
    _guard, _columns, _rebuild_source_plan, _check_poststate,
    _insert_rows, _update_rows, PHASES, FAILURE_POINTS,
)
import foundation_backfill_receipt as receipt
from foundation_migrations import digest, schema, VERSION as STAGE1_VERSION

CONTRACT = 'stage2-explicit-execution-v1'
CONFIRMATION = 'EXECUTE_EXACT_REVIEWED_STAGE2_PLAN'
TRANSACTION_SECONDS = 30
EVIDENCE_MAX_AGE = timedelta(minutes=15)


class ExecutionError(ValueError):
    """Fixed, private-data-free reason codes."""


def _need(condition, code):
    if not condition:
        raise ExecutionError(code)


def _hash(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def packet_sha256(packet):
    return hashlib.sha256(canonical_json(packet).encode()).hexdigest()


def _path(value):
    p = Path(value)
    _need(p.is_absolute() and not str(value).replace('\\','/').startswith('//'),
          'ABSOLUTE_LOCAL_TARGET_REQUIRED')
    for part in (p, *p.parents):
        _need(not part.is_symlink() and not getattr(part, 'is_junction', lambda: False)(),
              'LINK_REFUSED')
    p = p.resolve(strict=True)
    _need(p.is_file() and p.stat().st_nlink == 1, 'REGULAR_SINGLE_LINK_FILE_REQUIRED')
    _need(not any((parent/'.git').exists() for parent in p.parents), 'REPOSITORY_DATABASE_REFUSED')
    return p


def _identity(p):
    stat = p.stat()
    return dict(path=str(p), device=stat.st_dev, inode=stat.st_ino)


def _connect(p, *, writable=False, artifact=False):
    # Immutable is exclusively for hash-verified, standalone checkpoint artifacts.
    suffix = '?mode=rw' if writable else '?mode=ro'
    if artifact:
        suffix += '&immutable=1'
    db = sqlite3.connect(p.as_uri()+suffix, uri=True, timeout=2, isolation_level=None)
    try:
        db.enable_load_extension(False)
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA temp_store=MEMORY')
        _need(db.execute('PRAGMA foreign_keys').fetchone() == (1,), 'FOREIGN_KEYS_REQUIRED')
        if not writable:
            db.execute('PRAGMA query_only=ON')
            db.set_authorizer(_read_guard)
        return db
    except BaseException:
        db.close()
        raise


def _ledger(db):
    rows = db.execute('SELECT version,checksum,applied_at,before_schema_hash,after_schema_hash '
                      'FROM schema_migrations WHERE version=?', (STAGE1_VERSION,)).fetchall()
    _need(len(rows) == 1, 'STAGE1_RECEIPT_REQUIRED')
    return digest(rows)


def _triggers(db):
    return digest([row for row in schema(db) if row[0]=='trigger'])


def _state(db, evidence):
    verified = receipt.verify_state(db, **evidence)
    return dict(lifecycle=verified, ledger_sha256=_ledger(db),
                trigger_sha256=_triggers(db), scoring=scoring_evidence(db))


def _checkpoint(target, evidence, checkpoint):
    _need(type(checkpoint) is dict and set(checkpoint)=={
        'path','sha256','restore_path','restore_sha256','independently_verified'}, 'CHECKPOINT_EVIDENCE_REQUIRED')
    _need(checkpoint['independently_verified'] is True, 'CHECKPOINT_ACK_REQUIRED')
    artifacts, results = [], []
    for key, hash_key in (('path','sha256'),('restore_path','restore_sha256')):
        p = _path(checkpoint[key])
        _need(p != target and all(p != other for other in artifacts), 'CHECKPOINT_MUST_BE_INDEPENDENT')
        _need(_hash(checkpoint[hash_key]), 'CHECKPOINT_HASH_REQUIRED')
        _need(not any(Path(str(p)+s).exists() for s in ('-wal','-shm','-journal')),
              'STANDALONE_CHECKPOINT_REQUIRED')
        _need(file_sha256(p)==checkpoint[hash_key], 'CHECKPOINT_HASH_MISMATCH')
        with closing(_connect(p, artifact=True)) as db:
            db.execute('BEGIN')
            result = _state(db, evidence)
            _need(result['lifecycle']['status']=='stage1_only', 'CHECKPOINT_BASELINE_REQUIRED')
        _need(file_sha256(p)==checkpoint[hash_key], 'CHECKPOINT_CHANGED')
        artifacts.append(p)
        results.append(result)
    _need(results[0]==results[1], 'RESTORE_VERIFICATION_MISMATCH')
    return results[0]


def _binding(path, evidence, checkpoint):
    baseline = _checkpoint(path, evidence, checkpoint)
    return dict(contract=CONTRACT, target=_identity(path),
                source=dict(evidence['plan']['source']),
                manifest_checksum=evidence['expected_manifest_checksum'],
                canonical_plan_sha256=evidence['expected_plan_sha256'],
                stage1_ledger_sha256=baseline['ledger_sha256'],
                schema_sha256=baseline['lifecycle']['source']['schema_sha256'],
                trigger_sha256=baseline['trigger_sha256'], scoring=baseline['scoring'],
                checkpoint=deepcopy(checkpoint))


def _evidence(manifest, plan, expected_manifest_checksum, expected_plan_sha256, expected_source,
              expected_stage1_ledger_sha256, expected_schema_sha256):
    m, p, validation = _inputs(manifest,plan,expected_manifest_checksum,expected_plan_sha256)
    _need(expected_source==p['source'], 'SOURCE_IDENTITY_MISMATCH')
    _need(_hash(expected_stage1_ledger_sha256), 'STAGE1_IDENTITY_REQUIRED')
    _need(expected_schema_sha256==p['source']['schema_sha256'], 'SCHEMA_IDENTITY_MISMATCH')
    return dict(manifest=m,plan=p,expected_manifest_checksum=expected_manifest_checksum,
                expected_plan_sha256=expected_plan_sha256), validation


def prepare_execution(*, database_path, manifest, plan, expected_manifest_checksum,
                      expected_plan_sha256, expected_source, expected_stage1_ledger_sha256,
                      expected_schema_sha256, checkpoint):
    """Read-only fresh deterministic plan validation; returns packet for human review.

    No apply or approval is performed. No new plan is substituted or persisted.
    Accept only pristine Stage 1 for a newly prepared approval packet.
    """
    try:
        evidence, validation = _evidence(manifest,plan,expected_manifest_checksum,expected_plan_sha256,
            expected_source,expected_stage1_ledger_sha256,expected_schema_sha256)
        path = _path(database_path)
        packet = _binding(path,evidence,checkpoint)
        _need(packet['stage1_ledger_sha256']==expected_stage1_ledger_sha256, 'STAGE1_IDENTITY_MISMATCH')
        with closing(_connect(path)) as db:
            db.execute('BEGIN')
            current = _state(db,evidence)
            _need(current['lifecycle']['status']=='stage1_only', 'FRESH_SOURCE_REQUIRED')
            _need(current['ledger_sha256']==packet['stage1_ledger_sha256'], 'STAGE1_IDENTITY_MISMATCH')
            _need(current['scoring']==packet['scoring'], 'SCORING_MISMATCH')
            inspection = inspect_connection(db)
            rebuilt = _rebuild_source_plan(db,evidence['manifest'],inspection,validation)
            _need(canonical_json(rebuilt)==canonical_json(evidence['plan']), 'DETERMINISTIC_PLAN_MISMATCH')
        return packet
    except ExecutionError:
        raise
    except Exception:
        raise ExecutionError('PREPARATION_REFUSED') from None


def _approval(packet, approved_packet_sha256, confirmation, writers_fenced, operational_evidence):
    _need(confirmation==CONFIRMATION, 'EXECUTION_CONFIRMATION_REQUIRED')
    _need(writers_fenced is True, 'WRITER_FENCE_ACK_REQUIRED')
    _need(_hash(approved_packet_sha256) and packet_sha256(packet)==approved_packet_sha256,
          'HUMAN_APPROVED_PACKET_MISMATCH')
    _need(type(operational_evidence) is dict and set(operational_evidence)=={
        'mode','approved_packet_sha256','observed_at','gates'}, 'OPERATIONAL_EVIDENCE_REQUIRED')
    _need(operational_evidence['mode'] in ('offline_simulation','production_attestation'), 'EVIDENCE_MODE_REQUIRED')
    _need(operational_evidence['approved_packet_sha256']==approved_packet_sha256, 'GATE_BINDING_MISMATCH')
    observed = datetime.fromisoformat(operational_evidence['observed_at'])
    _need(observed.tzinfo is not None and observed.utcoffset()==timedelta(0), 'UTC_GATE_TIME_REQUIRED')
    age = datetime.now(timezone.utc)-observed
    _need(timedelta(0)<=age<=EVIDENCE_MAX_AGE, 'STALE_OPERATIONAL_EVIDENCE')
    gates = operational_evidence['gates']
    _need(type(gates) is dict and set(gates)==set(GATES), 'ALL_OPERATIONAL_GATES_REQUIRED')
    for value in gates.values():
        _need(type(value) is dict and set(value)=={'passed','evidence_sha256'} and
              value['passed'] is True and _hash(value['evidence_sha256']), 'OPERATIONAL_GATE_NOT_SATISFIED')


def execute_plan(*, database_path, manifest, plan, expected_manifest_checksum,
                 expected_plan_sha256, expected_source, expected_stage1_ledger_sha256,
                 expected_schema_sha256, checkpoint, approved_packet,
                 approved_packet_sha256, confirmation, writers_fenced, operational_evidence,
                 fail_at=None):
    """No planning, backup, writer control, network, runtime import or retry.

    Input validation and independent checkpoint/restore verification precede
    BEGIN IMMEDIATE; target/evidence/schema/data are checked again under it.
    Success must be independently reopened/verified before writers resume.
    Ambiguous invocation outcomes require inspection, never automatic retries.
    """
    try:
        packet, gates = deepcopy(approved_packet), deepcopy(operational_evidence)
        _approval(packet,approved_packet_sha256,confirmation,writers_fenced,gates)
        evidence, _ = _evidence(manifest,plan,expected_manifest_checksum,expected_plan_sha256,
            expected_source,expected_stage1_ledger_sha256,expected_schema_sha256)
        checkpoint = deepcopy(checkpoint)
        path = _path(database_path)
        _need(_binding(path,evidence,checkpoint)==packet, 'APPROVED_BINDING_MISMATCH')
        _need(packet['stage1_ledger_sha256']==expected_stage1_ledger_sha256, 'STAGE1_IDENTITY_MISMATCH')
        _need(fail_at is None or fail_at in FAILURE_POINTS|{'after_receipt'}, 'UNKNOWN_FAILURE_POINT')
        # Refuse stale/partial targets before obtaining a write connection.
        with closing(_connect(path)) as db:
            db.execute('BEGIN')
            _state(db,evidence)
        _approval(packet,approved_packet_sha256,confirmation,writers_fenced,gates)
        _need(_identity(_path(database_path))==packet['target'], 'TARGET_CHANGED')
        applied_at = datetime.now(timezone.utc).isoformat()
        with closing(_connect(path,writable=True)) as db:
            armed = False
            def guard(action, first, second, database, trigger):
                if action==sqlite3.SQLITE_INSERT and first=='schema_migrations':
                    return sqlite3.SQLITE_OK if armed and database=='main' and trigger is None else sqlite3.SQLITE_DENY
                return _guard(action,first,second,database,trigger)
            db.set_authorizer(guard)
            deadline = time.monotonic()+TRANSACTION_SECONDS
            db.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
            def check(phase=None):
                _need(time.monotonic()<=deadline, 'TRANSACTION_DEADLINE_EXCEEDED')
                _need(phase is None or phase!=fail_at, 'INJECTED_FAILURE')
            try:
                db.execute('BEGIN IMMEDIATE')
                check()
                _approval(packet,approved_packet_sha256,confirmation,writers_fenced,gates)
                _need(_identity(_path(database_path))==packet['target'], 'TARGET_CHANGED')
                before = _state(db,evidence)
                _need(before['ledger_sha256']==packet['stage1_ledger_sha256'], 'STAGE1_IDENTITY_MISMATCH')
                _need(before['trigger_sha256']==packet['trigger_sha256'], 'TRIGGER_INVENTORY_CHANGED')
                _need(before['scoring']==packet['scoring'], 'SCORING_MISMATCH')
                before_schema = digest(schema(db))
                p = evidence['plan']
                counts = {'inserts':{},'updates':{}}
                if before['lifecycle']['status']=='stage2_complete':
                    check()
                    db.execute('ROLLBACK')
                    return _result('already_matches',before['lifecycle'],counts,0,before['scoring'],gates)
                initial_changes = db.total_changes
                for phase, inserts, updates in PHASES:
                    for table in inserts:
                        records = p['new_rows'].get(table,[])
                        if records: counts['inserts'][table] = _insert_rows(db,table,records)
                    for table in updates:
                        records = p['nullable_updates'].get(table,[])
                        if records: counts['updates'][table] = _update_rows(db,table,records)
                    check(phase)
                _need(counts==p['expected_write_counts'], 'WRITE_COUNTS_MISMATCH')
                _need(db.total_changes-initial_changes==sum(sum(v.values()) for v in counts.values()),
                      'UNEXPECTED_TRIGGER_WRITES')
                check('before_validation')
                _check_poststate(db,p,_columns(db))
                score = scoring_evidence(db)
                _need(score==packet['scoring'], 'SCORING_CHANGED')
                after_schema = digest(schema(db))
                _need(before_schema==after_schema==expected_schema_sha256, 'SCHEMA_CHANGED')
                armed = True
                try:
                    db.execute('INSERT INTO schema_migrations '
                        '(version,checksum,applied_at,before_schema_hash,after_schema_hash) VALUES(?,?,?,?,?)',
                        (receipt.VERSION,receipt.checksum(expected_manifest_checksum,expected_plan_sha256,p['source']),
                         applied_at,before_schema,after_schema))
                finally:
                    armed = False
                    db.set_authorizer(guard)
                _need(db.total_changes-initial_changes==1+sum(sum(v.values()) for v in counts.values()),
                      'UNEXPECTED_RECEIPT_WRITES')
                complete = receipt.verify_state(db,require_completed=True,**evidence)
                check('after_receipt')
                result = _result('applied',complete,counts,1,score,gates)
                check('before_commit')
                db.execute('COMMIT')
                return result
            except BaseException:
                db.set_progress_handler(None,0)
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise
    except ExecutionError:
        raise
    except Exception:
        raise ExecutionError('EXECUTION_REFUSED_OUTCOME_REQUIRES_VERIFICATION') from None


def _result(status, verified, counts, receipt_writes, score, gates):
    return dict(status=status,write_counts=counts,receipt_writes=receipt_writes,
                receipt=verified['receipt'],post_state=verified['post_state'],
                integrity=verified['integrity'],foreign_key_violations=verified['foreign_key_violations'],
                protected_sha256=verified['protected_sha256'],scoring=score,scoring_equal=True,
                schema_unchanged=True,stage1_receipt_unchanged=True,
                operational_evidence_mode=gates['mode'],production_gates_cleared=False,
                production_execution_gates=list(GATES))
