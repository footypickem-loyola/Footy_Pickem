"""Short durable claims around the existing importer. No scheduler or provider loop."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3
import uuid

from football_reference import ReferenceClient, ReferenceError, database_path, sync_reference, write_guard
from football_reference_schema import TASK_DDL

CURRENT_SEASON = 28083
LEASE = timedelta(minutes=10)
MIN_REFRESH_INTERVAL = timedelta(hours=1)
FAILURE_BACKOFF = timedelta(minutes=15)
ERROR_STATUS = {"missing_token": 503, "rate_limited": 429, "provider_http_error": 502,
                "provider_unavailable": 502, "validation_failed": 502, "database_busy": 503,
                "database_error": 503, "lease_lost": 409, "unexpected_failure": 503}


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def connect(path):
    return sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=1)


def claim(path, now):
    """SQLite serializes only this tiny claim transaction, never the network fetch."""
    token = uuid.uuid4().hex
    with closing(connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(TASK_DDL)
        db.set_authorizer(write_guard)
        row = db.execute("SELECT status,lease_until,next_attempt_after FROM football_reference_tasks WHERE provider='sportmonks' AND external_season_id=?", (CURRENT_SEASON,)).fetchone()
        if row and row[0] == "running" and row[1] and row[1] > stamp(now):
            return None, dict(status="already_running", lease_until=row[1])
        if row and row[2] and row[2] > stamp(now):
            return None, dict(status="fresh" if row[0] == "completed" else "cooldown", next_attempt_after=row[2])
        db.execute("""INSERT INTO football_reference_tasks
            (provider,external_season_id,status,started_at,claim_token,lease_until)
            VALUES('sportmonks',?,'running',?,?,?)
            ON CONFLICT(provider,external_season_id) DO UPDATE SET status='running',started_at=excluded.started_at,
            completed_at=NULL,claim_token=excluded.claim_token,lease_until=excluded.lease_until,
            next_attempt_after=NULL,error_code=NULL""", (CURRENT_SEASON, stamp(now), token, stamp(now+LEASE)))
    return token, None


def assert_owner(db, token, now):
    row = db.execute("""SELECT 1 FROM football_reference_tasks WHERE provider='sportmonks'
        AND external_season_id=? AND status='running' AND claim_token=? AND lease_until>?""",
        (CURRENT_SEASON, token, stamp(now))).fetchone()
    if row is None:
        raise ReferenceError("Reference task lease lost", code="lease_lost")


def finish(db, token, now):
    assert_owner(db, token, now)
    db.execute("""UPDATE football_reference_tasks SET status='completed',completed_at=?,
        claim_token=NULL,lease_until=NULL,next_attempt_after=?,last_successful_sync_at=(
          SELECT last_successful_sync_at FROM football_reference_seasons
          WHERE provider='sportmonks' AND external_season_id=?),error_code=NULL
        WHERE provider='sportmonks' AND external_season_id=? AND claim_token=?""",
        (stamp(now), stamp(now+MIN_REFRESH_INTERVAL), CURRENT_SEASON, CURRENT_SEASON, token))


def fail(path, token, now, code, retry_after):
    # Token guard: an expired worker cannot clear or fail its replacement's lease.
    with closing(connect(path)) as db, db:
        db.set_authorizer(write_guard)
        changed = db.execute("""UPDATE football_reference_tasks SET status='failed',completed_at=?,
            claim_token=NULL,lease_until=NULL,error_code=?,next_attempt_after=?
            WHERE provider='sportmonks' AND external_season_id=? AND claim_token=?""",
            (stamp(now), code, stamp(now+max(FAILURE_BACKOFF, timedelta(seconds=retry_after))), CURRENT_SEASON, token))
        return changed.rowcount == 1


def run_reference_task(database, client=None, clock=utcnow):
    """One current-season attempt. Returns (safe JSON payload, HTTP status)."""
    token = None
    try:
        path = database_path(database)
        token, skipped = claim(path, clock())
        if skipped:
            return dict(ok=True, season_id=CURRENT_SEASON, **skipped), 200
        summary = sync_reference(database=path, season_id=CURRENT_SEASON, client=client or ReferenceClient(),
            before_write=lambda db: assert_owner(db, token, clock()),
            before_commit=lambda db: finish(db, token, clock()))
        return dict(ok=True, status="completed", **summary), 200
    except Exception as exc:
        code = getattr(exc, "code", "unexpected_failure")
        if isinstance(exc, sqlite3.OperationalError):
            code = "database_busy" if getattr(exc, "sqlite_errorcode", 0) & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else "database_error"
        if code not in ERROR_STATUS:
            code = "unexpected_failure"
        retry = getattr(exc, "retry_after", 60)
        retry = max(60, min(retry, 3600)) if type(retry) is int else 60
        recorded = False
        if token:
            try:
                recorded = fail(path, token, clock(), code, retry)
            except Exception:
                pass  # Busy/crashed storage: the durable lease still expires.
        return dict(ok=False, status="failed", season_id=CURRENT_SEASON, error_code=code,
                    retry_after=max(int(FAILURE_BACKOFF.total_seconds()), retry), failure_recorded=recorded), ERROR_STATUS[code]
