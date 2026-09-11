#!/usr/bin/env python3
"""One-off guarded copy of selected Week 1 X sources into staging Year 2."""

from __future__ import annotations

import argparse
import importlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.engine import make_url


DESTINATION_SEASON_CODE = "year-2"
WINDOW_START = datetime(2026, 8, 21, 0, 0, 0)
WINDOW_END = datetime(2026, 8, 24, 0, 0, 0)
CLASSIFIER_PROMPT_VERSION = "semantic-classifier-v2"


class MaintenanceSafetyError(RuntimeError):
    pass


def validated_staging_db_path(database_url: str) -> Path:
    """Return the guarded SQLite path, rejecting every non-staging database."""
    if not isinstance(database_url, str) or not database_url.strip():
        raise MaintenanceSafetyError("DB_PATH must be explicitly configured")
    try:
        parsed = make_url(database_url.strip())
    except Exception as exc:
        raise MaintenanceSafetyError("DB_PATH is not a valid SQLAlchemy URL") from exc
    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        raise MaintenanceSafetyError("This script only supports the staging SQLite DB")
    database_path = Path(parsed.database).expanduser().resolve()
    if database_path.name.lower() != "pickem_staging.db":
        raise MaintenanceSafetyError(
            "Refusing to run: DB_PATH must point to pickem_staging.db"
        )
    if not database_path.is_file():
        raise MaintenanceSafetyError(f"Staging database does not exist: {database_path}")
    return database_path


def create_sqlite_backup(database_path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = database_path.with_name(
        f"{database_path.stem}.backup-{timestamp}{database_path.suffix}"
    )
    source = sqlite3.connect(str(database_path))
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return backup_path


def print_seasons(db: Any, app_module: Any) -> list[Any]:
    seasons = db.query(app_module.Season).order_by(app_module.Season.id.asc()).all()
    print("Seasons:")
    for season in seasons:
        print(
            f"  id={season.id} code={season.code!r} name={season.name!r} "
            f"active={int(bool(season.is_active))} "
            f"archived={int(bool(season.is_archived))}"
        )
    return seasons


def resolve_week1(db: Any, app_module: Any) -> tuple[Any, Any]:
    """Resolve the unique source-bearing Week 1 and sole active Year 2 Week 1."""
    seasons = print_seasons(db, app_module)
    active_seasons = [
        season for season in seasons
        if bool(season.is_active) and not bool(season.is_archived)
    ]
    if len(active_seasons) != 1 or active_seasons[0].code != DESTINATION_SEASON_CODE:
        raise MaintenanceSafetyError(
            "The sole active, non-archived season must be year-2 (2026-27)"
        )
    destination_season = active_seasons[0]
    if destination_season.api_season_year not in {None, 2026}:
        raise MaintenanceSafetyError("The year-2 destination is not the 2026-27 season")

    destination_week = db.query(app_module.Week).filter_by(
        season_id=destination_season.id,
        number=1,
    ).one_or_none()
    if destination_week is None:
        raise MaintenanceSafetyError("Active year-2 Week 1 was not found")

    source_candidates = []
    for season in seasons:
        if season.id == destination_season.id:
            continue
        week = db.query(app_module.Week).filter_by(
            season_id=season.id,
            number=1,
        ).one_or_none()
        if week is None:
            continue
        source_count = len(matching_sources(db, app_module, week.id))
        if source_count:
            source_candidates.append((season, week, source_count))

    print("Source Week 1 candidates with matching accepted sources:")
    for season, _week, source_count in source_candidates:
        print(
            f"  id={season.id} code={season.code!r} name={season.name!r} "
            f"matching_sources={source_count}"
        )
    if not source_candidates:
        raise MaintenanceSafetyError(
            "No non-destination Week 1 contains matching accepted sources"
        )
    if len(source_candidates) != 1:
        raise MaintenanceSafetyError(
            "More than one non-destination source season contains matching accepted sources"
        )
    _source_season, source_week, _source_count = source_candidates[0]
    return source_week, destination_week


def destination_result_count(db: Any, app_module: Any, week_id: int) -> int:
    return db.query(app_module.Result).join(app_module.Fixture).filter(
        app_module.Fixture.week_id == week_id
    ).count()


def validate_destination(db: Any, app_module: Any, destination_week: Any) -> int:
    result_count = destination_result_count(db, app_module, destination_week.id)
    if destination_week.status != "finalized" or result_count != 10:
        raise MaintenanceSafetyError(
            "Destination year-2 Week 1 must be finalized with exactly 10 results"
        )
    return result_count


def matching_sources(db: Any, app_module: Any, source_week_id: int) -> list[Any]:
    return db.query(app_module.CorrespondentSource).filter(
        app_module.CorrespondentSource.week_id == source_week_id,
        app_module.CorrespondentSource.status == "accepted",
        app_module.CorrespondentSource.provider == "x",
        app_module.CorrespondentSource.published_at >= WINDOW_START,
        app_module.CorrespondentSource.published_at < WINDOW_END,
    ).order_by(
        app_module.CorrespondentSource.published_at.asc(),
        app_module.CorrespondentSource.id.asc(),
    ).all()


def copy_sources(
    db: Any,
    app_module: Any,
    *,
    dry_run: bool,
    backup_path: Optional[Path] = None,
) -> dict[str, Any]:
    source_week, destination_week = resolve_week1(db, app_module)
    original_result_count = validate_destination(db, app_module, destination_week)
    sources = matching_sources(db, app_module, source_week.id)
    source_matching_count = len(sources)
    print(f"Matching accepted source count: {source_matching_count}")
    for source in sources[:5]:
        timestamp = source.published_at.isoformat() if source.published_at else "None"
        print(f"  source_id={source.id} published_at={timestamp} author={source.author_name!r}")

    destination_keys = {
        (row.provider, row.content_hash)
        for row in db.query(app_module.CorrespondentSource).filter_by(
            week_id=destination_week.id
        ).all()
    }
    destination_matchups = db.query(app_module.Matchup).join(app_module.Week).filter(
        app_module.Week.season_id == destination_week.season_id
    ).all()
    destination_player_ids = {
        player_id
        for matchup in destination_matchups
        for player_id in (matchup.player_a_id, matchup.player_b_id)
    }
    jobs_before = db.query(app_module.CorrespondentClassificationJob).count()
    inserted_rows = []
    skipped = 0
    for source in sources:
        key = (source.provider, source.content_hash)
        if key in destination_keys:
            skipped += 1
            continue
        destination_keys.add(key)
        inserted_rows.append(app_module.CorrespondentSource(
            week_id=destination_week.id,
            provider=source.provider,
            source_type=source.source_type,
            external_id=source.external_id,
            canonical_url=source.canonical_url,
            author_name=source.author_name,
            body_text=source.body_text,
            published_at=source.published_at,
            submitted_by_player_id=(
                source.submitted_by_player_id
                if source.submitted_by_player_id in destination_player_ids
                else None
            ),
            submission_note=source.submission_note,
            metadata_json=source.metadata_json,
            content_hash=source.content_hash,
            status=source.status,
        ))

    print(f"Planned insertions: {len(inserted_rows)}; already present/skipped: {skipped}")
    if dry_run:
        db.rollback()
        print("Dry run complete: no backup created and no rows written.")
        return {
            "source_matching_count": source_matching_count,
            "destination_copied_count": source_matching_count - len(inserted_rows),
            "inserted": 0,
            "would_insert": len(inserted_rows),
            "skipped": skipped,
            "backup_path": None,
        }

    if backup_path is None:
        raise MaintenanceSafetyError("A verified staging backup is required before writes")
    try:
        db.add_all(inserted_rows)
        db.flush()
        inserted_ids = [row.id for row in inserted_rows]
        source_count_after = len(matching_sources(db, app_module, source_week.id))
        source_keys = {(source.provider, source.content_hash) for source in sources}
        destination_copied_count = sum(
            (row.provider, row.content_hash) in source_keys
            for row in db.query(app_module.CorrespondentSource).filter_by(
                week_id=destination_week.id
            ).all()
        )
        classification_count = (
            db.query(app_module.CorrespondentSourceClassification).filter(
                app_module.CorrespondentSourceClassification.source_id.in_(inserted_ids),
                app_module.CorrespondentSourceClassification.prompt_version
                == CLASSIFIER_PROMPT_VERSION,
            ).count()
            if inserted_ids
            else 0
        )
        jobs_created = (
            db.query(app_module.CorrespondentClassificationJob).count() - jobs_before
        )
        final_result_count = destination_result_count(
            db,
            app_module,
            destination_week.id,
        )
        if source_count_after != source_matching_count:
            raise MaintenanceSafetyError("Source matching count changed during the copy")
        if classification_count != 0 or jobs_created != 0:
            raise MaintenanceSafetyError("The raw-source copy created classifier state")
        if (
            destination_week.status != "finalized"
            or final_result_count != original_result_count
        ):
            raise MaintenanceSafetyError("Destination game state changed during the copy")
        db.commit()
    except Exception:
        db.rollback()
        raise

    print(f"Source matching count unchanged: {source_count_after}")
    print(f"Destination copied count: {destination_copied_count}")
    print(f"Inserted: {len(inserted_ids)}; skipped: {skipped}")
    print(f"semantic-classifier-v2 classifications on inserted rows: {classification_count}")
    print(f"Batch jobs created: {jobs_created}")
    print(
        f"Destination Week 1 status/results: {destination_week.status}/"
        f"{final_result_count}"
    )
    print(f"Backup: {backup_path}")
    return {
        "source_matching_count": source_count_after,
        "destination_copied_count": destination_copied_count,
        "inserted": len(inserted_ids),
        "would_insert": len(inserted_ids),
        "skipped": skipped,
        "classification_count": classification_count,
        "jobs_created": jobs_created,
        "destination_result_count": final_result_count,
        "backup_path": backup_path,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    database_url = os.environ.get("DB_PATH", "")
    database_path = validated_staging_db_path(database_url)
    backup_path = None if args.dry_run else create_sqlite_backup(database_path)

    os.environ["INIT_ON_START"] = "0"
    app_module = importlib.import_module("pickem_flask_htmx_tabs")
    configured_path = app_module._database_file_path(app_module.engine)
    if configured_path != database_path:
        raise MaintenanceSafetyError("Loaded application database does not match guarded DB_PATH")
    db = app_module.SessionLocal()
    try:
        copy_sources(
            db,
            app_module,
            dry_run=args.dry_run,
            backup_path=backup_path,
        )
    finally:
        db.close()
        app_module.SessionLocal.remove()
        app_module.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
