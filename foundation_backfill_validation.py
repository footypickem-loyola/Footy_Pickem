"""Read-only Stage 2 discovery against an explicit existing SQLite file.

No app import, ID allocation, apply path, schema initialization or network.
mode=ro uses normal SQLite WAL snapshots: never immutable=1 or nolock=1.
"""
from contextlib import closing
from pathlib import Path
import sqlite3

from foundation_migrations import (
    MigrationError, check_integrity, digest, migration_checksum, quoted, schema,
    validate_legacy, verified_applied, row_manifest,
)
from foundation_schema import ADDITIONS, INDEX_DDL, REQUIRED_COLUMNS, TABLE_DDL

FOUNDATION = {ddl.split()[2] for ddl in TABLE_DDL}
KNOWN_TABLES = set(REQUIRED_COLUMNS) | FOUNDATION
KNOWN_INDEXES = {ddl.split("INDEX ", 1)[1].split()[0] for ddl in INDEX_DDL}
KNOWN_COLUMNS = set().union(*(set(v) for v in REQUIRED_COLUMNS.values()),
                            *(set(v) for v in ADDITIONS.values()))
# Names in DDL can be inventoried as digests; raw SQL/defaults never leave this module.
READ_PRAGMAS = {"integrity_check", "foreign_key_check", "table_info", "table_xinfo",
                "foreign_key_list", "index_list", "index_xinfo", "foreign_keys",
                "query_only", "journal_mode"}
SAFE_FUNCTIONS = {"count", "sum", "min", "max", "typeof", "coalesce", "length", "like"}


class InspectionError(ValueError):
    """Fixed reason code only; never expose database or filesystem diagnostics."""


def _token(value, known):
    return value if value in known else "sha256:" + digest(value)


def _read_guard(action, first, second, database, trigger):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and (second or "").lower() in SAFE_FUNCTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_TRANSACTION and first in ("BEGIN", "ROLLBACK"):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and first.lower() in READ_PRAGMAS:
        if first.lower() in {"foreign_keys", "query_only", "journal_mode"} and second is not None:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def connect_readonly(path):
    if path is None or not str(path).strip():
        raise InspectionError("EXPLICIT_DATABASE_REQUIRED")
    raw = str(path).replace("\\", "/")
    if raw.startswith("//") or raw.startswith("file:"):
        raise InspectionError("LOCAL_FILESYSTEM_PATH_REQUIRED")
    if raw.lower().rstrip("/") == "/data/pickem.db":
        raise InspectionError("PROTECTED_DATABASE")
    try:
        target = Path(path).expanduser().resolve(strict=True)
        if target.as_posix().lower().endswith("/data/pickem.db"):
            raise InspectionError("PROTECTED_DATABASE")
        if not target.is_file():
            raise InspectionError("DATABASE_NOT_FILE")
        db = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True,
                             timeout=2, isolation_level=None)
    except FileNotFoundError:
        raise InspectionError("DATABASE_MISSING") from None
    except (OSError, sqlite3.Error, ValueError) as exc:
        if isinstance(exc, InspectionError):
            raise
        raise InspectionError("DATABASE_UNAVAILABLE") from None
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        if db.execute("PRAGMA query_only").fetchone()[0] != 1 or db.execute(
                "PRAGMA foreign_keys").fetchone()[0] != 1:
            raise InspectionError("READONLY_GUARDS_UNAVAILABLE")
        db.set_authorizer(_read_guard)
        return db
    except BaseException:
        db.close()
        raise


def _validate_baseline(db):
    try:
        check_integrity(db)
    except MigrationError:
        raise InspectionError("INTEGRITY_OR_FOREIGN_KEY_FAILURE") from None
    try:
        columns = validate_legacy(db)
    except MigrationError:
        raise InspectionError("LEGACY_SCHEMA_MISMATCH") from None
    try:
        if not verified_applied(db):
            raise InspectionError("STAGE1_REQUIRED")
    except MigrationError as exc:
        # Classify fixed Stage 1 messages without ever returning their text.
        code = "SCHEMA_MISMATCH" if str(exc) == "Applied schema drift detected" else "STAGE1_LEDGER_MISMATCH"
        raise InspectionError(code) from None
    if not FOUNDATION <= columns.keys():
        raise InspectionError("FOUNDATION_SCHEMA_MISMATCH")
    objects = {r[1] for r in schema(db)}
    if not KNOWN_INDEXES <= objects:
        raise InspectionError("FOUNDATION_SCHEMA_MISMATCH")
    for table, additions in ADDITIONS.items():
        info = {r[1]: r for r in db.execute(f"PRAGMA table_info({quoted(table)})")}
        if any(name not in info or info[name][3] or info[name][4] is not None for name in additions):
            raise InspectionError("FOUNDATION_SCHEMA_MISMATCH")
    return columns


def _inventory(db, columns):
    objects = [{"kind": kind, "name": _token(name, KNOWN_TABLES | KNOWN_INDEXES),
                "table": _token(table, KNOWN_TABLES), "definition_sha256": digest(sql)}
               for kind, name, table, sql in schema(db)]
    tables = {}
    for table in sorted(columns):
        pk = []
        structure = []
        for _, name, sql_type, required, default, ordinal, hidden in db.execute(
                f"PRAGMA table_xinfo({quoted(table)})"):
            token = _token(name, KNOWN_COLUMNS)
            structure.append({"column": token, "type_sha256": digest(sql_type),
                              "not_null": bool(required), "default_sha256": digest(default),
                              "pk_position": ordinal, "hidden": hidden})
            if ordinal:
                pk.append((ordinal, token))
        fks = [{"id": r[0], "sequence": r[1], "target_table": _token(r[2], KNOWN_TABLES),
                "from": _token(r[3], KNOWN_COLUMNS), "to": _token(r[4], KNOWN_COLUMNS),
                "on_update": r[5], "on_delete": r[6], "match": r[7]}
               for r in db.execute(f"PRAGMA foreign_key_list({quoted(table)})")]
        indexes = []
        for _, name, unique, origin, partial in db.execute(f"PRAGMA index_list({quoted(table)})"):
            indexes.append({"name": _token(name, KNOWN_INDEXES), "unique": bool(unique),
                            "origin": origin, "partial": bool(partial),
                            "columns": [{"position": r[0], "column": _token(r[2], KNOWN_COLUMNS),
                                         "descending": bool(r[3]), "key": bool(r[5])}
                                        for r in db.execute(f"PRAGMA index_xinfo({quoted(name)})")]})
        tables[_token(table, KNOWN_TABLES)] = {
            "columns": structure, "primary_key": [name for _, name in sorted(pk)],
            "foreign_keys": fks, "indexes": sorted(indexes, key=lambda r: r["name"])}
    return {"objects": sorted(objects, key=lambda r: (r["kind"], r["name"])), "tables": tables}


def _population(db, block):
    counts = {name: db.execute(f"SELECT COUNT(*) FROM {quoted(name)}").fetchone()[0]
              for name in sorted(FOUNDATION - {"schema_migrations"})}
    for table, count in counts.items():
        if count:
            block("FOUNDATION_ALREADY_POPULATED", table=table, count=count)
    added = {}
    for table, columns in sorted(ADDITIONS.items()):
        added[table] = {}
        for column in sorted(columns):
            count = db.execute(f"SELECT COUNT(*) FROM {quoted(table)} WHERE {quoted(column)} IS NOT NULL").fetchone()[0]
            added[table][column] = count
            if count:
                block("FOUNDATION_COLUMN_POPULATED", table=table, column=column, count=count)
    row = db.execute("SELECT name FROM leagues WHERE id=1").fetchone()
    if row and row[0] != "Loyola Pick \u2019Em":
        block("CONFLICTING_LEAGUE1_IDENTITY", table="leagues", row_id=1)
    return {"foundation_rows": counts, "populated_nullable_columns": added}


def _evidence(db, block):
    seasons = []
    for sid, competition, year in db.execute(
            "SELECT id,api_competition_code,api_season_year FROM seasons ORDER BY id"):
        fixture_ids = [r[0] for r in db.execute(
            "SELECT f.id FROM fixtures f JOIN weeks w ON w.id=f.week_id WHERE w.season_id=? ORDER BY f.id", (sid,))]
        metadata = bool(competition) and type(year) is int
        seasons.append({"season_id": sid, "fixture_ids": fixture_ids,
                        "provider_query_metadata_present": metadata})
        block("SEASON_MAPPING_REVIEW_REQUIRED" if metadata else "SEASON_EVIDENCE_MISSING",
              table="seasons", row_id=sid)
    weeks = []
    for wid, sid, number in db.execute("SELECT id,season_id,number FROM weeks ORDER BY id"):
        weeks.append({"week_id": wid, "season_id": sid, "number": number if type(number) is int else None})
        if type(number) is not int or number <= 0:
            block("INVALID_ROUND", table="weeks", row_id=wid)
    fixtures = []
    for fid, wid, home, away, external in db.execute(
            "SELECT id,week_id,home,away,external_match_id FROM fixtures ORDER BY id"):
        links = db.execute("SELECT COUNT(*) FROM fixture_provider_links WHERE fixture_id=?", (fid,)).fetchone()[0]
        fixtures.append({"fixture_id": fid, "week_id": wid,
                         "football_data_id_present": external is not None, "provider_link_count": links})
        if home == away:
            block("CONFLICTING_FIXTURE_PARTICIPANTS", table="fixtures", row_id=fid)
    for table in ("picks", "auto_draft_preferences"):
        for rid, team, home, away in db.execute(
                f"SELECT p.id,p.team,f.home,f.away FROM {table} p JOIN fixtures f ON f.id=p.fixture_id ORDER BY p.id"):
            if (team == home) + (team == away) != 1:
                block("UNRESOLVED_PICK_PARTICIPANT", table=table, row_id=rid)
    for row in db.execute("SELECT p.id FROM picks p JOIN matchups m ON m.id=p.matchup_id "
                          "JOIN fixtures f ON f.id=p.fixture_id "
                          "WHERE p.player_id NOT IN (m.player_a_id,m.player_b_id) OR f.week_id<>m.week_id ORDER BY p.id"):
        block("CONFLICTING_PICK_RELATIONSHIP", table="picks", row_id=row[0])
    if fixtures:
        block("CLUB_AND_ROUND_MANIFEST_REQUIRED")
    block("RECIPIENT_PERMISSION_MANIFEST_REQUIRED")
    block("REVIEWED_MAPPING_MANIFEST_REQUIRED")
    return {"label": "Observed", "player_ids": [r[0] for r in db.execute("SELECT id FROM players ORDER BY id")],
            "seasons": seasons, "weeks": weeks, "fixtures": fixtures}


def _task_blockers(db, columns, block):
    policies = {
        "pick_insight_refreshes": ("week_id", {"pending", "completed"}, ("claim_token", "lease_until")),
        "correspondent_classification_jobs": ("id", {"completed", "cancelled", "expired", "failed"}, ()),
        "correspondent_classification_job_items": ("id", {"completed", "imported"}, ("active_reservation_key",)),
        "correspondent_x_collection_states": ("id", {"completed", "capped", "cap_reached"}, ("lease_token", "lease_expires_at")),
        "correspondent_email_deliveries": ("id", {"sent"}, ("claim_token", "claim_expires_at")),
        "correspondent_notifications": ("id", {"sent"}, ("claim_token", "claim_expires_at")),
    }
    for table, (key, terminal, claims) in policies.items():
        query = f"SELECT {key},status" + "".join("," + c for c in claims) + f" FROM {table} ORDER BY {key}"
        for row in db.execute(query):
            if row[1] not in terminal or any(value is not None for value in row[2:]):
                block("UNSUPPORTED_OR_IN_FLIGHT_TASK", table=table, row_id=row[0])
    # Additional stores are reported, never opened automatically or rekeyed.
    for table in ("football_reference_tasks", "team_briefs", "scheduler"):
        if table in columns and db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
            block("SHARED_STORE_STATE_REVIEW_REQUIRED", table_sha256=digest(table))


def inspect_database(path):
    try:
        with closing(connect_readonly(path)) as db:
            db.execute("BEGIN")
            return inspect_connection(db)
    except InspectionError:
        raise
    except (sqlite3.Error, OSError, ValueError, TypeError):
        raise InspectionError("DATABASE_INSPECTION_REFUSED") from None


def inspect_connection(db):
    """Inspect an already-reserved read-only snapshot; caller owns connection."""
    columns = _validate_baseline(db)
    issues = []

    def block(code, **details):
        issues.append({"label": "Unresolved", "code": code, **details})

    inventory = _inventory(db, columns)
    population = _population(db, block)
    evidence = _evidence(db, block)
    _task_blockers(db, columns, block)
    if db.execute("SELECT 1 FROM sqlite_schema WHERE name='sqlite_sequence'").fetchone():
        columns = dict(columns, sqlite_sequence=("name", "seq"))
    full = row_manifest(db, columns)
    protected = {t: tuple(c for c in names if c not in ADDITIONS.get(t, {}))
                 for t, names in columns.items() if t not in FOUNDATION}
    hashes = row_manifest(db, protected)
    return {"report_version": 1, "label": "Observed", "stage1_valid": True,
            "status": "blocked" if issues else "inspected", "execution_authorized": False,
            "source": {"schema_sha256": digest(schema(db)), "data_sha256": digest(full)},
            "stage1_checksum": migration_checksum(), "inventory": inventory,
            "table_fingerprints": {_token(t, KNOWN_TABLES): v for t, v in sorted(full.items())},
            "protected_fingerprints": {_token(t, KNOWN_TABLES): v for t, v in sorted(hashes.items())},
            "population": population, "evidence": evidence,
            "limitations": [{"label": "Inferred", "code": "EVIDENCE_PRESENCE_IS_NOT_IDENTITY_PROOF"},
                            {"label": "Unresolved", "code": "SEPARATE_STORES_NOT_INSPECTED"}],
            "blockers": sorted(issues, key=lambda r: (r["code"], digest(r)))}
