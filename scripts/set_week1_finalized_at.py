#!/usr/bin/env python3
"""Set finalized_at once for active year-2 Week 1 in staging."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url


EXPECTED_STAGING_DB_PATH = Path("/data/pickem_staging.db").resolve()
SEASON_CODE = "year-2"
WEEK_NUMBER = 1


class MaintenanceSafetyError(RuntimeError):
    pass


def validated_staging_db_path(database_url: str) -> Path:
    if not isinstance(database_url, str) or not database_url.strip():
        raise MaintenanceSafetyError("DB_PATH must be explicitly configured")
    try:
        parsed = make_url(database_url.strip())
    except Exception as exc:
        raise MaintenanceSafetyError("DB_PATH is not a valid SQLAlchemy URL") from exc
    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        raise MaintenanceSafetyError("DB_PATH must use SQLite")
    resolved = Path(parsed.database).expanduser().resolve()
    if resolved != EXPECTED_STAGING_DB_PATH:
        raise MaintenanceSafetyError(
            "Refusing to run: DB_PATH must resolve exactly to "
            "/data/pickem_staging.db"
        )
    if not resolved.is_file():
        raise MaintenanceSafetyError(f"Staging database does not exist: {resolved}")
    return resolved


def parse_utc_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MaintenanceSafetyError("An explicit ISO UTC timestamp is required")
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MaintenanceSafetyError(
            "Timestamp must be valid ISO-8601 UTC"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise MaintenanceSafetyError(
            "Timestamp must include an explicit UTC offset (Z or +00:00)"
        )
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def resolve_target(db: Any, app_module: Any) -> tuple[Any, Any]:
    seasons = db.query(app_module.Season).filter_by(code=SEASON_CODE).all()
    if len(seasons) != 1:
        raise MaintenanceSafetyError("Exactly one year-2 season must exist")
    season = seasons[0]
    if season.is_active != 1 or season.is_archived != 0:
        raise MaintenanceSafetyError("The year-2 season must be active and not archived")

    week = db.query(app_module.Week).filter_by(
        season_id=season.id,
        number=WEEK_NUMBER,
    ).one_or_none()
    if week is None:
        raise MaintenanceSafetyError("Active year-2 Week 1 was not found")
    if week.status != "finalized":
        raise MaintenanceSafetyError("Active year-2 Week 1 must be finalized")

    results = db.query(app_module.Result).join(app_module.Fixture).filter(
        app_module.Fixture.week_id == week.id
    ).all()
    if len(results) != 10 or any(
        result.outcome not in {"Home", "Away", "Draw"} for result in results
    ):
        raise MaintenanceSafetyError(
            "Active year-2 Week 1 must have exactly 10 completed results"
        )
    if week.finalized_at is not None:
        raise MaintenanceSafetyError("Active year-2 Week 1 finalized_at is already set")
    return season, week


def set_week1_finalized_at(
    db: Any,
    app_module: Any,
    timestamp: datetime,
) -> dict[str, Any]:
    if db.new or db.dirty or db.deleted:
        raise MaintenanceSafetyError("Database session has pending unrelated changes")
    season, week = resolve_target(db, app_module)
    before = week.finalized_at
    try:
        updated = db.query(app_module.Week).filter(
            app_module.Week.id == week.id,
            app_module.Week.finalized_at.is_(None),
        ).update(
            {app_module.Week.finalized_at: timestamp},
            synchronize_session=False,
        )
        if updated != 1:
            raise MaintenanceSafetyError("Week finalized_at changed before update")
        db.flush()
        after = db.query(app_module.Week).populate_existing().filter_by(
            id=week.id
        ).one().finalized_at
        if after != timestamp:
            raise MaintenanceSafetyError("Week finalized_at verification failed")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "season_id": season.id,
        "season_code": season.code,
        "week_id": week.id,
        "week_number": week.number,
        "before": before,
        "after": after,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("timestamp", help="Explicit ISO-8601 UTC timestamp")
    args = parser.parse_args()
    timestamp = parse_utc_timestamp(args.timestamp)
    database_path = validated_staging_db_path(os.environ.get("DB_PATH", ""))

    os.environ["INIT_ON_START"] = "0"
    app_module = importlib.import_module("pickem_flask_htmx_tabs")
    configured_path = app_module._database_file_path(app_module.engine)
    if configured_path != database_path:
        raise MaintenanceSafetyError(
            "Loaded application database does not match guarded DB_PATH"
        )
    db = app_module.SessionLocal()
    try:
        summary = set_week1_finalized_at(db, app_module, timestamp)
    finally:
        db.close()
        app_module.SessionLocal.remove()
        app_module.engine.dispose()

    print(f"DB path: {database_path}")
    print(
        f"Season: id={summary['season_id']} code={summary['season_code']}"
    )
    print(
        f"Week: id={summary['week_id']} number={summary['week_number']}"
    )
    print(f"Before finalized_at: {summary['before']}")
    print(f"After finalized_at: {summary['after'].isoformat()}Z")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MaintenanceSafetyError as exc:
        print(f"ABORTED: {exc}", file=sys.stderr)
        raise SystemExit(2)
