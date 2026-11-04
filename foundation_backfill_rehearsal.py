"""Capability-scoped rehearsal on fresh disposable offline copies; no CLI.

Never register an arbitrary existing writable target. Only rehearsal_clone()
creates targets from an explicitly hashed standalone source: v2 uses siblings;
v3 requires an explicit private temporary directory. Source and approved inputs
are immutable; production gates stay open.
The synthetic target factory and apply entry point are neither changed nor used.
"""
from contextlib import contextmanager, closing
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time

from foundation_backfill_manifest import canonical_json, validate_planning_manifest, ManifestError
from foundation_backfill_validation import inspect_connection, _read_guard, InspectionError
from foundation_backfill_rehearsal_scoring import scoring_evidence
# Reuse tested connection-level primitives, never the synthetic capability/API.
from foundation_backfill_synthetic import (
    PHASES, INSERT_TABLES, FAILURE_POINTS, SyntheticApplyError,
    _guard, _columns, _source_projection, _rebuild_source_plan,
    _check_poststate, _insert_rows, _update_rows,
)
from foundation_migrations import digest, schema, MigrationError
import foundation_backfill_receipt as receipt
from foundation_schema import ADDITIONS

TRANSACTION_SECONDS = 30
GATES = (
    'WRITER_FENCING_AND_REVALIDATION_REQUIRED',
    'X_CLAIM_REVALIDATION_AND_FENCING_REQUIRED',
    'CURRENT_RECIPIENT_CONFIGURATION_REVALIDATION_REQUIRED',
    'SHARED_BRIEF_AND_SCHEDULER_REVALIDATION_REQUIRED',
    'SOURCE_SCHEMA_DATA_STAGE1_LEDGER_REVALIDATION_REQUIRED',
    'APPROVED_INDEPENDENT_CHECKPOINT_REQUIRED',
    'SCORING_AND_PRESERVATION_EQUALITY_REQUIRED',
    'TRIGGER_EFFECT_REVIEW_REQUIRED',
)


class RehearsalError(ValueError):
    """Fixed reason codes only; never expose SQL, addresses or private values."""


@dataclass(frozen=True, eq=False)
class RehearsalTarget:
    source: Path
    path: Path
    initial_sha256: str


_ACTIVE = {}


def file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plan_sha256(plan):
    return hashlib.sha256(canonical_json(plan).encode()).hexdigest()


def _identity(path):
    s = path.stat()
    return (s.st_dev, s.st_ino)


def _local_file(value):
    try:
        raw = str(value).replace('\\', '/')
        path = Path(value)
        if (not path.is_absolute() or raw.startswith('//') or raw.startswith('file:') or
                raw.lower().rstrip('/').endswith('/data/pickem.db')):
            raise RehearsalError('LOCAL_OFFLINE_PATH_REQUIRED')
        for part in (path, *path.parents):
            if part.is_symlink() or part.is_junction():
                raise RehearsalError('LINK_REFUSED')
        if any((parent / '.git').exists() for parent in path.parents):
            raise RehearsalError('REPOSITORY_DATABASE_REFUSED')
        path = path.resolve(strict=True)
        if not path.is_file() or path.stat().st_nlink != 1:
            raise RehearsalError('REGULAR_SINGLE_LINK_FILE_REQUIRED')
        return path
    except RehearsalError:
        raise
    except (OSError, ValueError, TypeError):
        raise RehearsalError('OFFLINE_FILE_UNAVAILABLE') from None


def _source_check(path, expected):
    path = _local_file(path)
    if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise RehearsalError('STANDALONE_SOURCE_REQUIRED')
    if file_sha256(path) != expected:
        raise RehearsalError('SOURCE_HASH_MISMATCH')
    return path


def _readonly_source(path):
    # Only a previously verified standalone offline artifact, never a live WAL DB.
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, isolation_level=None)
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA trusted_schema=OFF')
    db.execute('PRAGMA temp_store=MEMORY')
    db.set_authorizer(_read_guard)
    return db


def _offline_validation(manifest, plan, review):
    """Explicit private review admission, used only by offline capability/verification.

    This does not attest writer fencing or satisfy any production gate.
    """
    from foundation_backfill_manifest import validate_offline_planning_manifest
    from foundation_backfill_operator import private_directory
    fields = {'reviewed_source', 'owner_decision_path', 'owner_decision_sha256',
              'audit_evidence_path', 'audit_evidence_sha256'}
    if type(review) is not dict or set(review) != fields:
        raise RehearsalError('OFFLINE_REVIEW_REQUIRED')
    for prefix in ('owner_decision', 'audit_evidence'):
        artifact = _local_file(review[prefix + '_path'])
        private_directory(artifact.parent)
        if file_sha256(artifact) != review[prefix + '_sha256']:
            raise RehearsalError('OFFLINE_REVIEW_HASH_MISMATCH')
    validation = validate_offline_planning_manifest(
        manifest, reviewed_source=review['reviewed_source'],
        owner_decision_sha256=review['owner_decision_sha256'],
        audit_evidence_sha256=review['audit_evidence_sha256'])
    if (plan.get('plan_version') != 3 or plan.get('offline_only') is not True or
            plan.get('email_send_authorized') is not False or
            plan.get('recipient_disposition') != 'disabled_no_send' or
            plan.get('offline_no_send') != manifest['offline_no_send'] or
            plan.get('new_rows', {}).get('league_notification_recipients')):
        raise RehearsalError('OFFLINE_NO_SEND_PLAN_REQUIRED')
    return validation


def _rebuild_rehearsal_plan(db, manifest, inspection, validation):
    rebuilt = _rebuild_source_plan(db, manifest, inspection, validation)
    if manifest['schema_version'] == 3:
        rebuilt.update(plan_version=3, offline_only=True, email_send_authorized=False,
                       recipient_disposition='disabled_no_send',
                       offline_no_send=manifest['offline_no_send'])
    return rebuilt


def _inputs(manifest, plan, expected_manifest_checksum, expected_plan_sha256, *,
            offline_review=None):
    try:
        manifest, plan = deepcopy(manifest), deepcopy(plan)
        if offline_review is None:
            validation = validate_planning_manifest(manifest)
        else:
            validation = _offline_validation(manifest, plan, offline_review)
        if validation['status'] != 'valid' or manifest['checksum'] != expected_manifest_checksum:
            raise RehearsalError('MANIFEST_IDENTITY_MISMATCH')
        if plan_sha256(plan) != expected_plan_sha256:
            raise RehearsalError('PLAN_IDENTITY_MISMATCH')
        if plan['manifest_checksum'] != manifest['checksum'] or plan['source'] != manifest['source']:
            raise RehearsalError('MANIFEST_IDENTITY_MISMATCH')
        if (plan['status'] not in {'planned', 'gated'} or plan['blockers'] or
                plan['counts_are_complete'] is not True or plan['execution_authorized'] is not False):
            raise RehearsalError('BLOCKED_PLAN')
        gates = plan.get('execution_gates', [])
        if bool(gates) != (plan['status'] == 'gated') or any(
                g['code'] != 'X_CLAIM_REVALIDATION_AND_FENCING_REQUIRED' or
                g.get('table') != 'correspondent_x_collection_states' or
                type(g.get('row_id')) is not int for g in gates):
            raise RehearsalError('UNSUPPORTED_EXECUTION_GATE')
        if set(plan['new_rows']) - INSERT_TABLES or set(plan['nullable_updates']) - set(ADDITIONS):
            raise RehearsalError('WRITE_SCOPE_REFUSED')
        return manifest, plan, validation
    except RehearsalError:
        raise
    except (ManifestError, ValueError, TypeError, KeyError):
        raise RehearsalError('INVALID_MANIFEST_OR_PLAN') from None


def _restrict_new_windows_artifact(path, *, directory=False):
    """Only the clone factory's newly created directory/empty file, never parents.

    Replace inherited OWNER RIGHTS with the current owner's explicit SID before
    private content is copied. Preserve ownership; refuse on any ACL failure.
    """
    import os
    if os.name != 'nt':
        return
    import base64
    import subprocess
    from foundation_backfill_operator import path as checked_path
    path = checked_path(path, directory=directory)
    identity = _identity(path)
    literal = "'" + str(path).replace("'", "''") + "'"
    kind = 'Directory' if directory else 'File'
    inheritance = 'ContainerInherit,ObjectInherit' if directory else 'None'
    command = "$ErrorActionPreference='Stop'; $p=" + literal + "; "
    command += "$sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; "
    command += "$old=[System.IO." + kind + "]::GetAccessControl($p); "
    command += "if($old.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $sid.Value){throw 'owner'}; "
    command += "$acl=New-Object System.Security.AccessControl." + kind + "Security; "
    command += "$acl.SetOwner($sid); $acl.SetAccessRuleProtection($true,$false); "
    command += "foreach($id in @($sid.Value,'S-1-5-18','S-1-5-32-544')){ "
    command += "$principal=New-Object System.Security.Principal.SecurityIdentifier($id); "
    command += "$rule=New-Object System.Security.AccessControl.FileSystemAccessRule($principal,'FullControl','" + inheritance + "','None','Allow'); $acl.AddAccessRule($rule)}; "
    command += "[System.IO." + kind + "]::SetAccessControl($p,$acl); "
    command += "$a=[System.IO." + kind + "]::GetAccessControl($p); "
    command += "if(-not $a.AreAccessRulesProtected -or $a.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $sid.Value){throw 'owner_or_inheritance'}; "
    command += "$rules=@($a.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])); if($rules.Count -ne 3){throw 'count'}; "
    command += "foreach($r in $rules){if($r.IdentityReference.Value -notin @($sid.Value,'S-1-5-18','S-1-5-32-544') -or $r.IsInherited -or $r.AccessControlType -ne 'Allow' -or $r.FileSystemRights -ne 'FullControl'){throw 'grant'}}; 'PRIVATE'"
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
            '-EncodedCommand', base64.b64encode(command.encode('utf-16le')).decode()],
            capture_output=True, timeout=15)
        if result.returncode != 0 or result.stdout.strip() != b'PRIVATE' or _identity(path) != identity:
            raise RehearsalError('NEW_ARTIFACT_ACL_REQUIRED')
    except (OSError, subprocess.TimeoutExpired):
        raise RehearsalError('NEW_ARTIFACT_ACL_REQUIRED') from None


@contextmanager
def rehearsal_clone(*, source_snapshot, expected_source_sha256, manifest,
                    expected_manifest_checksum, plan, expected_plan_sha256,
                    acknowledge_disposable_production_derived=False,
                    offline_review=None, execution_directory=None):
    """Create a new exclusive clone; retain artifacts after capability closes.

    Does not accept an existing destination file, overwrite a file, change source metadata,
    create a writable source connection, or create a directory in a repository.
    """
    if acknowledge_disposable_production_derived is not True:
        raise RehearsalError('DISPOSABLE_OFFLINE_CLONE_ACKNOWLEDGEMENT_REQUIRED')
    manifest, plan, validation = _inputs(manifest, plan, expected_manifest_checksum, expected_plan_sha256,
                                         offline_review=offline_review)
    source = _source_check(source_snapshot, expected_source_sha256)
    source_identity = _identity(source)
    execution_parent = source.parent
    if manifest['schema_version'] == 3:
        from foundation_backfill_operator import private_directory
        private_directory(source.parent)
        if execution_directory is None:
            raise RehearsalError('PRIVATE_TEMP_EXECUTION_DIRECTORY_REQUIRED')
        execution_parent = private_directory(execution_directory)
        if Path(tempfile.gettempdir()).resolve() not in execution_parent.parents:
            raise RehearsalError('PRIVATE_TEMP_EXECUTION_DIRECTORY_REQUIRED')
    elif execution_directory is not None:
        raise RehearsalError('V3_OFFLINE_CONTRACT_REQUIRED')
    with closing(_readonly_source(source)) as db:
        db.execute('BEGIN')
        inspection = inspect_connection(db)
        if inspection['source'] != plan['source']:
            raise RehearsalError('SOURCE_FINGERPRINT_MISMATCH')
        columns = _columns(db)
        if _source_projection(db, columns) != inspection['source']:
            raise RehearsalError('FOUNDATION_EMPTY_BASELINE_REQUIRED')
        rebuilt = _rebuild_rehearsal_plan(db, manifest, inspection, validation)
        if canonical_json(rebuilt) != canonical_json(plan):
            raise RehearsalError('REVALIDATED_PLAN_MISMATCH')
        scoring = scoring_evidence(db)
    # Fresh directory and exclusive file creation; no existing target adoption.
    directory = Path(tempfile.mkdtemp(
        prefix='rehearsal-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-',
        dir=execution_parent)).resolve(strict=True)
    if manifest['schema_version'] == 3:
        _restrict_new_windows_artifact(directory, directory=True)
        private_directory(directory)
    path = directory / 'pickem.rehearsal.db'
    with source.open('rb') as src, path.open('xb') as dst:
        if manifest['schema_version'] == 3:
            _restrict_new_windows_artifact(path)
        shutil.copyfileobj(src, dst)
    if (_source_check(source, expected_source_sha256) != source or
            _identity(source) != source_identity or file_sha256(path) != expected_source_sha256):
        raise RehearsalError('COPY_IDENTITY_MISMATCH')
    target = RehearsalTarget(source, path, expected_source_sha256)
    _ACTIVE[target] = dict(source_identity=source_identity, target_identity=_identity(path),
                           directory_identity=_identity(directory),
                           manifest_checksum=expected_manifest_checksum,
                           plan_sha256=expected_plan_sha256, scoring=scoring,
                           execution_parent=execution_parent,
                           offline_review=deepcopy(offline_review))
    try:
        yield target
    finally:
        _ACTIVE.pop(target, None)


def _target(target, database_path, source_snapshot, expected_target_sha256):
    if type(target) is not RehearsalTarget or target not in _ACTIVE:
        raise RehearsalError('LIVE_REHEARSAL_CAPABILITY_REQUIRED')
    state = _ACTIVE[target]
    source, path = _local_file(source_snapshot), _local_file(database_path)
    if (path == source or source != target.source or path != target.path or
            path.parent.parent != state['execution_parent'] or expected_target_sha256 != target.initial_sha256 or
            _identity(source) != state['source_identity'] or
            _identity(path) != state['target_identity'] or
            _identity(path.parent) != state['directory_identity']):
        raise RehearsalError('REHEARSAL_TARGET_IDENTITY_MISMATCH')
    _source_check(source, target.initial_sha256)
    if state['offline_review'] is not None:
        from foundation_backfill_operator import private_directory
        private_directory(path.parent)
        private_directory(state['execution_parent'])
    return path, state


def apply_rehearsal(target, *, database_path, source_snapshot, expected_target_sha256,
                    manifest, expected_manifest_checksum, plan, expected_plan_sha256, fail_at=None,
                    completion_receipt=False, offline_review=None):
    """Atomic offline rehearsal only; exact reruns verify then roll back read-only.

    Production-time gates are retained, never cleared. Only this factory's live
    capability is accepted. No caller callback can run within the transaction.
    """
    path, state = _target(target, database_path, source_snapshot, expected_target_sha256)
    manifest, plan, validation = _inputs(manifest, plan, expected_manifest_checksum, expected_plan_sha256,
                                         offline_review=offline_review)
    if offline_review != state['offline_review']:
        raise RehearsalError('CAPABILITY_INPUT_MISMATCH')
    if (expected_manifest_checksum != state['manifest_checksum'] or
            expected_plan_sha256 != state['plan_sha256']):
        raise RehearsalError('CAPABILITY_INPUT_MISMATCH')
    if type(completion_receipt) is not bool:
        raise RehearsalError('EXPLICIT_RECEIPT_MODE_REQUIRED')
    receipt_points = {'before_receipt', 'after_receipt'} if completion_receipt else set()
    if fail_at is not None and fail_at not in FAILURE_POINTS | receipt_points:
        raise RehearsalError('UNKNOWN_FAILURE_POINT')
    applied_at = datetime.now(timezone.utc).isoformat()
    receipt_evidence = dict(manifest=manifest, plan=plan,
                            expected_manifest_checksum=expected_manifest_checksum,
                            expected_plan_sha256=expected_plan_sha256)
    if offline_review is not None:
        receipt_evidence['offline_review'] = deepcopy(offline_review)
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=2,
                                    isolation_level=None)) as db:
            db.enable_load_extension(False)
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('PRAGMA temp_store=MEMORY')
            if db.execute('PRAGMA foreign_keys').fetchone() != (1,):
                raise RehearsalError('FOREIGN_KEYS_REQUIRED')
            receipt_insert_armed = False

            def authorizer(action, first, second, database, trigger):
                if action == sqlite3.SQLITE_INSERT and first == 'schema_migrations':
                    return (sqlite3.SQLITE_OK if receipt_insert_armed and
                            database == 'main' and trigger is None else sqlite3.SQLITE_DENY)
                return _guard(action, first, second, database, trigger)

            db.set_authorizer(authorizer)
            deadline = time.monotonic() + TRANSACTION_SECONDS
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)

            def checkpoint(phase=None):
                if time.monotonic() > deadline:
                    raise RehearsalError('TRANSACTION_DEADLINE_EXCEEDED')
                if phase is not None and phase == fail_at:
                    raise RehearsalError('INJECTED_FAILURE')

            def evidence(status, post, counts, score):
                return dict(status=status, offline_rehearsal_only=True, execution_authorized=False,
                            source_sha256=target.initial_sha256, manifest_checksum=expected_manifest_checksum,
                            plan_sha256=expected_plan_sha256, post_state=post['source'], write_counts=counts,
                            protected_sha256=digest(post['protected_fingerprints']),
                            schema_unchanged=True, stage1_receipt_unchanged=True, stage2_receipt_written=False,
                            exact_poststate_verified=True, scoring_equal=True, scoring=score,
                            production_execution_gates=list(GATES), preserved_plan_gates=plan.get('execution_gates', []))

            try:
                db.execute('BEGIN IMMEDIATE')
                checkpoint()
                before_schema = digest(schema(db))
                if completion_receipt:
                    lifecycle = receipt.verify_state(db, **receipt_evidence)
                    if lifecycle['status'] == 'stage2_complete':
                        score = scoring_evidence(db)
                        if score != state['scoring']:
                            raise RehearsalError('SCORING_SOURCE_MISMATCH')
                        _source_check(target.source, target.initial_sha256)
                        checkpoint()
                        result = evidence('already_matches', dict(source=lifecycle['post_state'],
                            protected_fingerprints=plan['preservation']['protected_fingerprints']),
                            {'inserts': {}, 'updates': {}}, score)
                        result.update(receipt=lifecycle['receipt'], receipt_writes=0,
                                      lifecycle_verification=lifecycle['status'])
                        db.execute('ROLLBACK')
                        return result
                current = inspect_connection(db)
                columns = _columns(db)
                if _source_projection(db, columns) != plan['source']:
                    raise RehearsalError('STALE_SOURCE_FINGERPRINT')
                rebuilt = _rebuild_rehearsal_plan(db, manifest, current, validation)
                if canonical_json(rebuilt) != canonical_json(plan):
                    raise RehearsalError('REVALIDATED_PLAN_MISMATCH')
                score_before = scoring_evidence(db)
                if score_before != state['scoring']:
                    raise RehearsalError('SCORING_SOURCE_MISMATCH')
                checkpoint()
                if current['source'] != plan['source']:
                    try:
                        post = _check_poststate(db, plan, columns)
                    except SyntheticApplyError:
                        raise RehearsalError('PARTIAL_OR_CONFLICTING_POPULATION') from None
                    checkpoint()
                    _source_check(target.source, target.initial_sha256)
                    db.execute('ROLLBACK')
                    return evidence('already_matches', post, {'inserts': {}, 'updates': {}}, score_before)
                counts = {'inserts': {}, 'updates': {}}
                changes_before = db.total_changes
                for phase, inserts, updates in PHASES:
                    for table in inserts:
                        records = plan['new_rows'].get(table, [])
                        if records: counts['inserts'][table] = _insert_rows(db, table, records)
                    for table in updates:
                        records = plan['nullable_updates'].get(table, [])
                        if records: counts['updates'][table] = _update_rows(db, table, records)
                    checkpoint(phase)
                if db.total_changes - changes_before != sum(sum(v.values()) for v in counts.values()):
                    raise RehearsalError('UNEXPECTED_TRIGGER_WRITES')
                if counts != plan['expected_write_counts']:
                    raise RehearsalError('WRITE_COUNTS_MISMATCH')
                checkpoint('before_validation')
                post = _check_poststate(db, plan, columns)
                score_after = scoring_evidence(db)
                if score_after != score_before:
                    raise RehearsalError('SCORING_CHANGED')
                _source_check(target.source, target.initial_sha256)
                result = evidence('applied', post, counts, score_after)
                if completion_receipt:
                    checkpoint('before_receipt')
                    after_schema = digest(schema(db))
                    if before_schema != after_schema or after_schema != plan['source']['schema_sha256']:
                        raise RehearsalError('RECEIPT_SCHEMA_MISMATCH')
                    changes_before_receipt = db.total_changes
                    receipt_insert_armed = True
                    try:
                        _insert_receipt(db, receipt.checksum(expected_manifest_checksum,
                            expected_plan_sha256, plan['source']), applied_at, before_schema, after_schema)
                    finally:
                        receipt_insert_armed = False
                        # Clear cached authorizations after disarming ledger insertion.
                        db.set_authorizer(authorizer)
                    if db.total_changes - changes_before_receipt != 1:
                        raise RehearsalError('UNEXPECTED_RECEIPT_WRITES')
                    lifecycle = receipt.verify_state(db, require_completed=True, **receipt_evidence)
                    result.update(stage2_receipt_written=True, receipt_writes=1,
                                  receipt=lifecycle['receipt'], post_state=lifecycle['post_state'],
                                  lifecycle_verification=lifecycle['status'])
                    checkpoint('after_receipt')
                json.dumps(result, allow_nan=False)
                checkpoint('before_commit')
                db.execute('COMMIT')
                return result
            except BaseException:
                db.set_progress_handler(None, 0)
                if db.in_transaction: db.execute('ROLLBACK')
                raise
    except RehearsalError:
        raise
    except SyntheticApplyError as exc:
        raise RehearsalError(str(exc)) from None
    except (sqlite3.Error, MigrationError, InspectionError, ValueError, TypeError, KeyError, OSError):
        raise RehearsalError('REHEARSAL_REFUSED') from None


def _insert_receipt(db, checksum, applied_at, before_schema, after_schema):
    # Only called with the bounded rehearsal transaction's insertion gate armed.
    db.execute('INSERT INTO schema_migrations '
               '(version,checksum,applied_at,before_schema_hash,after_schema_hash) VALUES(?,?,?,?,?)',
               (receipt.VERSION, checksum, applied_at, before_schema, after_schema))
