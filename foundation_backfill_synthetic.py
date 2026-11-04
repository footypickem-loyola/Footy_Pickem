"""Test-only atomic population of disposable synthetic databases.

Not imported by the maintenance CLI or application. No general path-based apply
API, environment target, production workflow, DDL, or completion receipt. A live
capability minted by synthetic_database() is mandatory. The harness must create
only synthetic contents at that capability's fixed path; this is not a security
boundary against malicious Python code or a caller disguising real data as a
synthetic fixture. Never copy real data into a test capability.
"""
from contextlib import contextmanager, closing
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from foundation_backfill_manifest import canonical_json, validate_planning_manifest, ManifestError
from foundation_backfill_planner import _build_plan
from foundation_backfill_validation import inspect_connection, InspectionError, FOUNDATION, READ_PRAGMAS
from foundation_migrations import digest, quoted, schema, table_columns, row_manifest, MigrationError
from foundation_schema import ADDITIONS, REQUIRED_COLUMNS


class SyntheticApplyError(ValueError):
    """Fixed reason codes only; no submitted values, SQL, paths or private data."""


@dataclass(frozen=True, eq=False)
class SyntheticTarget:
    path: Path


_ACTIVE = {}
# Explicit dependency order; user_identities, invitations and ledger excluded.
PHASES = (
    ('canonical_football', ('competitions', 'competition_provider_links', 'football_seasons',
                            'football_season_provider_links', 'football_matchweeks'), ()),
    ('clubs', ('clubs', 'club_provider_links', 'football_season_clubs', 'club_aliases'), ()),
    ('league_users', ('leagues', 'users'), ()),
    ('legacy_columns', (), ('players', 'seasons', 'weeks', 'fixtures', 'api_sync_states', 'club_season_enrichment')),
    ('slate_roster', ('league_week_fixtures', 'league_season_memberships'), ()),
    ('selections_events', (), ('picks', 'auto_draft_preferences', 'match_events')),
    ('tasks_recipients', ('football_refresh_jobs', 'league_notification_recipients'), ()),
)
INSERT_TABLES = frozenset(t for _, tables, _ in PHASES for t in tables)
FAILURE_POINTS = frozenset(name for name, _, _ in PHASES) | {'before_validation', 'before_commit'}
TRANSACTION_SECONDS = 10


@contextmanager
def synthetic_database(*, acknowledge_disposable=False):
    """Mint one test-controlled temporary target; no DB is implicitly created.

    Cannot register an existing directory or arbitrary file. Lifetime encloses all
    connections. Cleanup affects only the unique directory created here.
    """
    if acknowledge_disposable is not True:
        raise SyntheticApplyError('SYNTHETIC_ACKNOWLEDGEMENT_REQUIRED')
    with tempfile.TemporaryDirectory(prefix='footy-stage2-synthetic-') as directory:
        root = Path(directory).resolve(strict=True)
        target = SyntheticTarget(root / 'synthetic.db')
        _ACTIVE[target] = (root, target.path)
        try:
            yield target
        finally:
            _ACTIVE.pop(target, None)


def _target_path(target, database_path):
    if type(target) is not SyntheticTarget or target not in _ACTIVE:
        raise SyntheticApplyError('LIVE_SYNTHETIC_TARGET_REQUIRED')
    if database_path is None or not str(database_path).strip():
        raise SyntheticApplyError('EXPLICIT_DATABASE_REQUIRED')
    root, expected = _ACTIVE[target]
    try:
        path = Path(database_path)
        if not path.is_absolute() or path != expected or target.path != expected:
            raise SyntheticApplyError('SYNTHETIC_TARGET_PATH_MISMATCH')
        if root.is_symlink() or root.is_junction() or path.is_symlink() or path.is_junction():
            raise SyntheticApplyError('SYNTHETIC_LINK_REFUSED')
        if root.resolve(strict=True) != root or path.resolve(strict=True) != expected:
            raise SyntheticApplyError('SYNTHETIC_TARGET_PATH_MISMATCH')
        if not path.is_file() or path.stat().st_nlink != 1:
            raise SyntheticApplyError('SYNTHETIC_FILE_REQUIRED')
    except (OSError, ValueError) as exc:
        if isinstance(exc, SyntheticApplyError): raise
        raise SyntheticApplyError('SYNTHETIC_DATABASE_MISSING_OR_UNAVAILABLE') from None
    return expected


def _guard(action, first, second, database, trigger):
    if database not in (None, 'main'):
        return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and (second or '').lower() in {
        'count', 'sum', 'min', 'max', 'typeof', 'coalesce', 'length', 'like', 'trim', 'lower'}:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_TRANSACTION and first in ('BEGIN', 'COMMIT', 'ROLLBACK'):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and first.lower() in READ_PRAGMAS:
        if first.lower() in {'foreign_keys', 'query_only', 'journal_mode'} and second is not None:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_INSERT and first in INSERT_TABLES:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_UPDATE and second in ADDITIONS.get(first, {}):
        return sqlite3.SQLITE_OK
    # No DELETE, DDL, ATTACH, extension loading, legacy or receipt writes.
    return sqlite3.SQLITE_DENY


def _columns(db):
    columns = table_columns(db)
    if db.execute("SELECT 1 FROM sqlite_schema WHERE name='sqlite_sequence'").fetchone():
        columns['sqlite_sequence'] = ('name', 'seq')
    return columns


def _source_projection(db, columns):
    """Reconstruct original empty-foundation fingerprints using SELECT only.

    SQLite orders projected values using the same expressions as row_manifest.
    All original cells, PKs, FKs, extra columns/tables and allocator state remain
    covered. Only new-table rows and the new nullable cells are projected away.
    Ledger rows are never projected away. No whole-file hash is used.
    """
    result = {}
    for table, names in sorted(columns.items()):
        h = hashlib.sha256()
        count = 0
        if table not in FOUNDATION - {'schema_migrations'}:
            expressions = ['NULL' if c in ADDITIONS.get(table, {}) else quoted(c) for c in names]
            fields = ','.join(expressions)
            for row in db.execute(f'SELECT {fields} FROM {quoted(table)} ORDER BY {fields}'):
                values = [dict(blob=v.hex()) if isinstance(v, bytes) else v for v in row]
                h.update((json.dumps(values, ensure_ascii=False, separators=(',', ':')) + '\n').encode())
                count += 1
        result[table] = dict(rows=count, legacy_values_sha256=h.hexdigest())
    return {'schema_sha256': digest(schema(db)), 'data_sha256': digest(result)}


def _rebuild_source_plan(db, manifest, inspection, validation):
    # Rebuild from immutable legacy evidence for first apply and exact reruns.
    # This does not persist or choose new IDs: require byte-exact agreement with
    # the reviewed plan's frozen ID proposal before using any of its values.
    tables = {}
    for table in sorted(set(REQUIRED_COLUMNS) | FOUNDATION):
        if table in FOUNDATION - {'schema_migrations'}:
            tables[table] = []
            continue
        cursor = db.execute('SELECT * FROM ' + quoted(table))
        names = [c[0] for c in cursor.description]
        rows = [dict(zip(names, r)) for r in cursor]
        for row in rows:
            for column in ADDITIONS.get(table, {}): row[column] = None
        tables[table] = sorted(rows, key=digest)
    source_inspection = deepcopy(inspection)
    source_inspection['source'] = manifest['source']
    source_inspection['blockers'] = [r for r in inspection['blockers'] if r['code'] not in {
        'FOUNDATION_ALREADY_POPULATED', 'FOUNDATION_COLUMN_POPULATED', 'CONFLICTING_LEAGUE1_IDENTITY'}]
    return _build_plan(tables, manifest, source_inspection, validation)


def _primary_key(db, table):
    return tuple(r[1] for r in sorted(db.execute(f'PRAGMA table_info({quoted(table)})'), key=lambda r: r[5]) if r[5])


def _check_poststate(db, plan, columns):
    inspection = inspect_connection(db)  # Integrity, FKs, unchanged schema/Stage 1 ledger.
    if _source_projection(db, columns) != plan['source']:
        raise SyntheticApplyError('PROTECTED_STATE_CHANGED')
    if inspection['protected_fingerprints'] != plan['preservation']['protected_fingerprints']:
        raise SyntheticApplyError('PROTECTED_STATE_CHANGED')
    for table in sorted(FOUNDATION - {'schema_migrations'}):
        names = columns[table]
        actual = [dict(zip(names, row)) for row in db.execute(
            f'SELECT {",".join(map(quoted,names))} FROM {quoted(table)}')]
        expected = [record['values'] for record in plan['new_rows'].get(table, [])]
        if canonical_json(actual) != canonical_json(expected):
            raise SyntheticApplyError('EXPECTED_POSTSTATE_MISMATCH')
    for table, added in ADDITIONS.items():
        keys = _primary_key(db, table)
        expected = {}
        for record in plan['nullable_updates'].get(table, []):
            key = tuple(record['key'][k] for k in keys)
            if key in expected: raise SyntheticApplyError('DUPLICATE_UPDATE_KEY')
            expected[key] = record['values']
        names = keys + tuple(added)
        found = set()
        for row in db.execute(f'SELECT {",".join(map(quoted,names))} FROM {quoted(table)}'):
            key = tuple(row[:len(keys)])
            values = expected.get(key, {})
            if tuple(values.get(c) for c in added) != tuple(row[len(keys):]):
                raise SyntheticApplyError('EXPECTED_POSTSTATE_MISMATCH')
            found.add(key)
        if set(expected) - found:
            raise SyntheticApplyError('EXPECTED_POSTSTATE_MISMATCH')
    return inspection


def _insert_rows(db, table, records):
    count = 0
    if table not in INSERT_TABLES:
        raise SyntheticApplyError('WRITE_SCOPE_REFUSED')
    for record in records:
        values = record['values']
        names = tuple(sorted(values))
        db.execute(f'INSERT INTO {quoted(table)} ({",".join(map(quoted,names))}) '
                   f'VALUES ({",".join("?" for _ in names)})', tuple(values[c] for c in names))
        count += 1
    return count


def _update_rows(db, table, records):
    count = 0
    for record in records:
        values, key = record['values'], record['key']
        if not values or not set(values) <= set(ADDITIONS.get(table, {})) or set(key) != set(_primary_key(db, table)):
            raise SyntheticApplyError('WRITE_SCOPE_REFUSED')
        names, keys = tuple(sorted(values)), tuple(sorted(key))
        where = ' AND '.join(quoted(k) + '=?' for k in keys)
        args = tuple(key[k] for k in keys)
        old = db.execute(f'SELECT {",".join(map(quoted,names))} FROM {quoted(table)} WHERE {where}', args).fetchone()
        if old is None or any(v is not None and v != values[c] for c, v in zip(names, old)):
            raise SyntheticApplyError('CONFLICTING_TARGET_VALUE')
        fill = tuple(c for c, v in zip(names, old) if v is None)
        if not fill: continue
        guards = ' AND '.join(quoted(c) + ' IS NULL' for c in fill)
        cursor = db.execute(f'UPDATE {quoted(table)} SET ' + ','.join(quoted(c) + '=?' for c in fill) +
                            f' WHERE {where} AND {guards}', tuple(values[c] for c in fill) + args)
        if cursor.rowcount != 1: raise SyntheticApplyError('CONFLICTING_TARGET_VALUE')
        count += 1
    return count


def apply_synthetic(target, *, database_path, manifest, plan, expected_plan_sha256, fail_at=None):
    """One bounded atomic transaction, only within a live synthetic test target.

    No target creation, backup, hold/resume, external calls, app import, receipt,
    persisted allocation, or general apply command. Failure hooks name fixed
    phases and raise only; no caller-supplied callback runs inside the transaction.
    """
    path = _target_path(target, database_path)
    try:
        # Detach mutable caller inputs; preserve the reviewed values exactly.
        manifest, plan = deepcopy(manifest), deepcopy(plan)
        validation = validate_planning_manifest(manifest)
        if (type(expected_plan_sha256) is not str or len(expected_plan_sha256) != 64 or
            expected_plan_sha256 != digest(canonical_json(plan))):
            raise SyntheticApplyError('PLAN_IDENTITY_MISMATCH')
        if (validation['status'] != 'valid' or plan['manifest_checksum'] != manifest['checksum'] or
            plan['source'] != manifest['source']):
            raise SyntheticApplyError('MANIFEST_IDENTITY_MISMATCH')
        if plan['status'] != 'planned' or plan['blockers'] or plan['counts_are_complete'] is not True:
            raise SyntheticApplyError('BLOCKED_PLAN')
        if set(plan['new_rows']) - INSERT_TABLES or set(plan['nullable_updates']) - set(ADDITIONS):
            raise SyntheticApplyError('WRITE_SCOPE_REFUSED')
        if fail_at is not None and fail_at not in FAILURE_POINTS:
            raise SyntheticApplyError('UNKNOWN_FAILURE_POINT')
    except SyntheticApplyError:
        raise
    except (ManifestError, ValueError, TypeError, KeyError):
        raise SyntheticApplyError('INVALID_MANIFEST_OR_PLAN') from None

    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=2,
                                    isolation_level=None)) as db:
            db.enable_load_extension(False)
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            if db.execute('PRAGMA foreign_keys').fetchone() != (1,):
                raise SyntheticApplyError('FOREIGN_KEYS_REQUIRED')
            db.set_authorizer(_guard)
            deadline = time.monotonic() + TRANSACTION_SECONDS
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)

            def checkpoint(phase):
                if time.monotonic() > deadline:
                    raise SyntheticApplyError('TRANSACTION_DEADLINE_EXCEEDED')
                if fail_at is not None and fail_at == phase:
                    raise SyntheticApplyError('INJECTED_FAILURE')

            try:
                db.execute('BEGIN IMMEDIATE')  # At most two seconds acquiring reservation.
                checkpoint(None)
                current = inspect_connection(db)
                columns = _columns(db)
                if _source_projection(db, columns) != plan['source']:
                    raise SyntheticApplyError('STALE_SOURCE_FINGERPRINT')
                rebuilt = _rebuild_source_plan(db, manifest, current, validation)
                if canonical_json(rebuilt) != canonical_json(plan):
                    raise SyntheticApplyError('REVALIDATED_PLAN_MISMATCH')
                checkpoint(None)
                if current['source'] != plan['source']:
                    # Only the complete exact expected population is accepted.
                    # Never resume, repair, or fill a partial/different population.
                    try:
                        post = _check_poststate(db, plan, columns)
                    except SyntheticApplyError:
                        raise SyntheticApplyError('PARTIAL_OR_CONFLICTING_POPULATION') from None
                    checkpoint(None)
                    db.execute('ROLLBACK')
                    return _evidence('already_matches', plan, expected_plan_sha256, post,
                                     {'inserts': {}, 'updates': {}})
                counts = {'inserts': {}, 'updates': {}}
                changes_before = db.total_changes
                for phase, inserts, updates in PHASES:
                    for table in inserts:
                        rows = plan['new_rows'].get(table, [])
                        if rows: counts['inserts'][table] = _insert_rows(db, table, rows)
                    for table in updates:
                        rows = plan['nullable_updates'].get(table, [])
                        if rows: counts['updates'][table] = _update_rows(db, table, rows)
                    checkpoint(phase)
                direct_changes = sum(sum(rows.values()) for rows in counts.values())
                if db.total_changes - changes_before != direct_changes:
                    raise SyntheticApplyError('UNEXPECTED_TRIGGER_WRITES')
                if counts != plan['expected_write_counts']:
                    raise SyntheticApplyError('WRITE_COUNTS_MISMATCH')
                checkpoint('before_validation')
                post = _check_poststate(db, plan, columns)
                # Preconstruct the redacted result so serialization cannot fail
                # after commit. The post fingerprint includes all populated cells.
                evidence = _evidence('applied', plan, expected_plan_sha256, post, counts)
                checkpoint('before_commit')
                db.execute('COMMIT')
                return evidence
            except BaseException:
                db.set_progress_handler(None, 0)
                if db.in_transaction: db.execute('ROLLBACK')
                raise
    except SyntheticApplyError:
        raise
    except (sqlite3.Error, MigrationError, InspectionError, ValueError, TypeError, KeyError, OSError):
        raise SyntheticApplyError('SYNTHETIC_APPLY_REFUSED') from None


def _evidence(status, plan, checksum, inspection, counts):
    return dict(status=status, synthetic_only=True, plan_sha256=checksum,
                manifest_checksum=plan['manifest_checksum'], source=plan['source'],
                post_state=inspection['source'], write_counts=counts,
                protected_sha256=digest(inspection['protected_fingerprints']),
                schema_unchanged=True, stage1_receipt_unchanged=True, stage2_receipt_written=False)
