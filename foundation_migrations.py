"""Offline, stdlib-only schema expansion. Importing this module does nothing.

No environment-selected database, web import, network, backfill, or downgrade.
Inspect is read-only. Apply requires a path, reviewed schema hash, fresh backup,
and explicit opt-in. Those flags are not permission to operate production.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from foundation_schema import ADDITIONS, INDEX_DDL, REQUIRED_COLUMNS, TABLE_DDL, VERSION


class MigrationError(RuntimeError):
    pass


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def statements():
    yield from TABLE_DDL
    for table, columns in ADDITIONS.items():
        for name, definition in columns.items():
            yield f'ALTER TABLE {quoted(table)} ADD COLUMN {quoted(name)} {definition}'
    yield from INDEX_DDL


def migration_checksum():
    return digest(dict(version=VERSION, statements=list(statements()), required=REQUIRED_COLUMNS))


def connect(path, *, writable=False):
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise MigrationError('Database must be an existing SQLite file')
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if writable else '?mode=ro'),
                         uri=True, timeout=2, isolation_level=None)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        if db.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            raise MigrationError('Foreign key enforcement unavailable')
        if not writable:
            db.execute('PRAGMA query_only=ON')
        return db
    except BaseException:
        db.close()
        raise


def schema(db):
    return db.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema "
                      "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()


def table_columns(db):
    return {name: tuple(row[1] for row in db.execute(f'PRAGMA table_info({quoted(name)})'))
            for kind, name, _, _ in schema(db) if kind == 'table'}


def row_manifest(db, columns):
    """Hash original values including exact JSON/text, without returning PII."""
    output = {}
    for table, names in sorted(columns.items()):
        names_sql = ','.join(map(quoted, names))
        h = hashlib.sha256()
        count = 0
        for row in db.execute(f'SELECT {names_sql} FROM {quoted(table)} ORDER BY {names_sql}'):
            values = [dict(blob=v.hex()) if isinstance(v, bytes) else v for v in row]
            h.update((json.dumps(values, ensure_ascii=False, separators=(',', ':')) + '\n').encode())
            count += 1
        output[table] = dict(rows=count, legacy_values_sha256=h.hexdigest())
    return output


def check_integrity(db):
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise MigrationError('SQLite integrity check failed; no automatic repair')
    if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise MigrationError('Foreign key violations found; no automatic repair')


def validate_legacy(db):
    columns = table_columns(db)
    for table, required in REQUIRED_COLUMNS.items():
        if table not in columns or not set(required) <= set(columns[table]):
            raise MigrationError(f'Legacy schema is incomplete at {table}; explicit legacy review required')
        # Every application-defined table must still have its original identity.
        pk = [row[1] for row in db.execute(f'PRAGMA table_info({quoted(table)})') if row[5]]
        expected = {'live_fixture_states': ['fixture_id'],
                    'club_season_enrichment': ['season_id', 'sportmonks_team_id'],
                    'pick_insight_refreshes': ['week_id']}.get(table, ['id'])
        if pk != expected:
            raise MigrationError(f'Unexpected legacy primary key at {table}')
    return columns


def check_unapplied(db):
    columns = validate_legacy(db)
    objects = {row[1] for row in schema(db)}
    reserved = {re.search(r'CREATE TABLE (\w+)', ddl)[1] for ddl in TABLE_DDL}
    reserved.update(re.search(r'CREATE (?:UNIQUE )?INDEX (\w+)', ddl)[1] for ddl in INDEX_DDL)
    if objects & reserved:
        raise MigrationError('Foundation objects already exist without a verified migration; refusing partial adoption')
    for table, added in ADDITIONS.items():
        if set(columns[table]) & set(added):
            raise MigrationError(f'Foundation columns already exist at {table}; refusing partial adoption')
    return columns


def verified_applied(db, expected_schema=None):
    if 'schema_migrations' not in table_columns(db):
        return False
    try:
        rows = db.execute('SELECT version,checksum,before_schema_hash,after_schema_hash FROM schema_migrations').fetchall()
    except sqlite3.Error as exc:
        raise MigrationError('Unrecognized migration ledger') from exc
    if len(rows) != 1 or rows[0][0] != VERSION:
        raise MigrationError('Unknown migration history; refusing automatic adoption or downgrade')
    _, checksum, before_hash, after_hash = rows[0]
    if checksum != migration_checksum():
        raise MigrationError('Migration checksum mismatch')
    if digest(schema(db)) != after_hash:
        raise MigrationError('Applied schema drift detected')
    if expected_schema is not None and expected_schema not in (before_hash, after_hash):
        raise MigrationError('Expected schema hash does not match this migration')
    return True


def inspect_database(path):
    with closing(connect(path)) as db:
        db.execute('BEGIN')  # One coherent read-only snapshot, including hashes.
        check_integrity(db)
        columns = validate_legacy(db)
        applied = verified_applied(db)
        if not applied:
            check_unapplied(db)
        return dict(version=VERSION, applied=applied, schema_sha256=digest(schema(db)),
                    migration_checksum=migration_checksum(), sqlite_version=sqlite3.sqlite_version,
                    schema_objects=schema(db), columns=columns,
                    inspection_connection_foreign_keys=True, tables=row_manifest(db, columns))


def backup_database(source, destination):
    """Called with the migration's write reservation held, before any DDL.

Use a second read connection: backing up the connection with BEGIN IMMEDIATE
would wait on its own write transaction. O_EXCL prevents backup overwrite.
"""
    destination = Path(destination).expanduser().absolute()
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            check_integrity(dst)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def apply_database(path, *, expected_schema, backup, authorized=False):
    if not authorized:
        raise MigrationError('Explicit --authorize-additive-schema flag required')
    if not re.fullmatch(r'[0-9a-f]{64}', expected_schema or ''):
        raise MigrationError('An inspected --expect-schema SHA256 is required')
    if not backup:
        raise MigrationError('A fresh --backup path is required')
    source = Path(path).expanduser().resolve(strict=True)
    if Path(backup).expanduser().resolve() == source:
        raise MigrationError('Backup and source must differ')
    with closing(connect(source, writable=True)) as db:
        try:
            db.execute('BEGIN IMMEDIATE')  # Bounded 2-second wait; no retries.
            check_integrity(db)
            if verified_applied(db, expected_schema):
                db.rollback()
                return dict(status='already_applied', version=VERSION)
            before_schema = digest(schema(db))
            if before_schema != expected_schema:
                raise MigrationError('Schema changed since inspection')
            columns = check_unapplied(db)
            # Include allocator state when present, even though schema() excludes
            # SQLite internal objects. Never allocate replacement historical IDs.
            if db.execute("SELECT 1 FROM sqlite_schema WHERE name='sqlite_sequence'").fetchone():
                columns['sqlite_sequence'] = ('name', 'seq')
            before = row_manifest(db, columns)
            saved = backup_database(source, backup)
            with closing(connect(saved)) as backup_db:
                if digest(schema(backup_db)) != before_schema or row_manifest(backup_db, columns) != before:
                    raise MigrationError('Backup verification mismatch')
            # Do not use executescript: it may commit the current transaction.
            for statement in statements():
                db.execute(statement)
            if row_manifest(db, columns) != before:
                raise MigrationError('Legacy value preservation check failed')
            check_integrity(db)
            after_schema = digest(schema(db))
            db.execute('INSERT INTO schema_migrations VALUES (?,?,?,?,?)',
                       (VERSION, migration_checksum(), datetime.now(timezone.utc).isoformat(),
                        before_schema, after_schema))
            db.commit()
            return dict(status='applied', version=VERSION, before_schema_sha256=before_schema,
                        after_schema_sha256=after_schema, legacy_values_preserved=True)
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest='mode', required=True)
    inspect = modes.add_parser('inspect', help='Read-only preflight; never initializes a database')
    inspect.add_argument('--database', required=True)
    apply = modes.add_parser('apply', help='Explicit additive schema only; requires separate operational approval')
    apply.add_argument('--database', required=True)
    apply.add_argument('--expect-schema', required=True)
    apply.add_argument('--backup', required=True)
    apply.add_argument('--authorize-additive-schema', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.mode == 'inspect':
            result = inspect_database(args.database)
        else:
            result = apply_database(args.database, expected_schema=args.expect_schema,
                                    backup=args.backup, authorized=args.authorize_additive_schema)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (MigrationError, sqlite3.Error, OSError) as exc:
        # Error text contains schema/path diagnostics, never data rows or secrets.
        print(json.dumps(dict(status='refused', error=str(exc)), sort_keys=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
