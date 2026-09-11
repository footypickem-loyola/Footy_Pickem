#!/usr/bin/env python3
"""Reset only active 2026-27 Week 1 semantic-classifier-v2 state."""

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
ARCHIVED_STAGING_SEASON_CODE = "v2-staging"
TARGET_PROMPT_VERSION = "semantic-classifier-v2"
EXPECTED_PLAYER_NAMES = {"Steve", "Joe", "Marc", "Drew", "Scott", "Connor"}


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


def rows_snapshot(query: Any, model: Any) -> tuple[tuple[Any, ...], ...]:
    columns = tuple(model.__table__.columns)
    return tuple(
        tuple(getattr(row, column.name) for column in columns)
        for row in query.order_by(model.id.asc()).all()
    )


def week_pick_query(db: Any, app_module: Any, week_id: int) -> Any:
    return db.query(app_module.Pick).join(app_module.Matchup).filter(
        app_module.Matchup.week_id == week_id
    )


def resolve_destination(db: Any, app_module: Any) -> tuple[Any, Any]:
    active = db.query(app_module.Season).filter_by(
        is_active=1,
        is_archived=0,
    ).all()
    if len(active) != 1 or active[0].code != DESTINATION_SEASON_CODE:
        raise MaintenanceSafetyError(
            "The sole active, non-archived season must be year-2"
        )
    season = active[0]
    if season.api_season_year not in {None, 2026}:
        raise MaintenanceSafetyError("The year-2 destination is not 2026-27")
    week = db.query(app_module.Week).filter_by(
        season_id=season.id,
        number=1,
    ).one_or_none()
    if week is None or week.status != "finalized":
        raise MaintenanceSafetyError("Destination year-2 Week 1 must be finalized")
    fixture_count = db.query(app_module.Fixture).filter_by(week_id=week.id).count()
    result_count = db.query(app_module.Result).join(app_module.Fixture).filter(
        app_module.Fixture.week_id == week.id
    ).count()
    pick_count = week_pick_query(db, app_module, week.id).count()
    if fixture_count != 10 or result_count != 10 or pick_count != 30:
        raise MaintenanceSafetyError(
            "Destination Week 1 must contain 10 fixtures, 10 results, and 30 picks"
        )
    player_names = {
        player.name for player in db.query(app_module.Player).order_by(
            app_module.Player.id.asc()
        ).all()
    }
    if player_names != EXPECTED_PLAYER_NAMES:
        raise MaintenanceSafetyError(
            "Player names must be exactly Steve, Joe, Marc, Drew, Scott, Connor"
        )
    return season, week


def target_scope(db: Any, app_module: Any, season: Any, week: Any) -> dict[str, Any]:
    classifications = db.query(app_module.CorrespondentSourceClassification).join(
        app_module.CorrespondentSource,
        app_module.CorrespondentSourceClassification.source_id
        == app_module.CorrespondentSource.id,
    ).filter(
        app_module.CorrespondentSource.week_id == week.id,
        app_module.CorrespondentSourceClassification.prompt_version
        == TARGET_PROMPT_VERSION,
    ).all()
    jobs = db.query(app_module.CorrespondentClassificationJob).filter_by(
        season_id=season.id,
        week_id=week.id,
        prompt_version=TARGET_PROMPT_VERSION,
    ).all()
    job_ids = [job.id for job in jobs]
    items = (
        db.query(app_module.CorrespondentClassificationJobItem).filter(
            app_module.CorrespondentClassificationJobItem.job_id.in_(job_ids)
        ).all()
        if job_ids
        else []
    )
    return {
        "classifications": classifications,
        "jobs": jobs,
        "items": items,
        "classification_ids": {row.id for row in classifications},
        "job_ids": set(job_ids),
        "item_ids": {row.id for row in items},
    }


def archived_staging_state(db: Any, app_module: Any) -> dict[str, Any]:
    season = db.query(app_module.Season).filter_by(
        code=ARCHIVED_STAGING_SEASON_CODE
    ).one_or_none()
    if season is None:
        return {"season": None, "classifications": (), "jobs": (), "items": ()}
    if bool(season.is_active) or not bool(season.is_archived):
        raise MaintenanceSafetyError("v2-staging must remain archived and inactive")
    week_ids = [
        row.id for row in db.query(app_module.Week).filter_by(season_id=season.id).all()
    ]
    source_ids = [
        row.id for row in db.query(app_module.CorrespondentSource).filter(
            app_module.CorrespondentSource.week_id.in_(week_ids)
        ).all()
    ] if week_ids else []
    classification_query = db.query(app_module.CorrespondentSourceClassification)
    if source_ids:
        classification_query = classification_query.filter(
            app_module.CorrespondentSourceClassification.source_id.in_(source_ids)
        )
    else:
        classification_query = classification_query.filter(False)
    jobs_query = db.query(app_module.CorrespondentClassificationJob).filter_by(
        season_id=season.id
    )
    job_ids = [job.id for job in jobs_query.all()]
    items_query = db.query(app_module.CorrespondentClassificationJobItem)
    if job_ids:
        items_query = items_query.filter(
            app_module.CorrespondentClassificationJobItem.job_id.in_(job_ids)
        )
    else:
        items_query = items_query.filter(False)
    return {
        "season": rows_snapshot(
            db.query(app_module.Season).filter_by(id=season.id), app_module.Season
        ),
        "classifications": rows_snapshot(
            classification_query, app_module.CorrespondentSourceClassification
        ),
        "jobs": rows_snapshot(jobs_query, app_module.CorrespondentClassificationJob),
        "items": rows_snapshot(items_query, app_module.CorrespondentClassificationJobItem),
    }


def protected_state(
    db: Any,
    app_module: Any,
    target: dict[str, Any],
) -> dict[str, Any]:
    protected_models = (
        app_module.Season,
        app_module.Week,
        app_module.Player,
        app_module.Fixture,
        app_module.Result,
        app_module.Matchup,
        app_module.Pick,
        app_module.CorrespondentSource,
        app_module.WeeklyRecap,
        app_module.RecapSourceUsage,
        app_module.WeeklyRecapSelection,
    )
    snapshot = {
        model.__tablename__: rows_snapshot(db.query(model), model)
        for model in protected_models
    }
    for key, model, excluded_ids in (
        (
            "non_target_classifications",
            app_module.CorrespondentSourceClassification,
            target["classification_ids"],
        ),
        ("non_target_jobs", app_module.CorrespondentClassificationJob, target["job_ids"]),
        (
            "non_target_job_items",
            app_module.CorrespondentClassificationJobItem,
            target["item_ids"],
        ),
    ):
        query = db.query(model)
        if excluded_ids:
            query = query.filter(~model.id.in_(excluded_ids))
        snapshot[key] = rows_snapshot(query, model)
    return snapshot


def counts_for_report(
    db: Any,
    app_module: Any,
    week: Any,
    target: dict[str, Any],
) -> dict[str, int]:
    return {
        "raw_sources": db.query(app_module.CorrespondentSource).filter_by(
            week_id=week.id
        ).count(),
        "target_classifications": len(target["classification_ids"]),
        "target_jobs": len(target["job_ids"]),
        "target_job_items": len(target["item_ids"]),
        "recaps": db.query(app_module.WeeklyRecap).filter_by(week_id=week.id).count(),
    }


def verify_reset_state(
    db: Any,
    app_module: Any,
    season: Any,
    week: Any,
    original_target: dict[str, Any],
    before_counts: dict[str, int],
    before_protected: dict[str, Any],
    before_archived: dict[str, Any],
) -> dict[str, int]:
    remaining = target_scope(db, app_module, season, week)
    after_counts = counts_for_report(db, app_module, week, remaining)
    after_counts["target_job_items"] = (
        db.query(app_module.CorrespondentClassificationJobItem).filter(
            app_module.CorrespondentClassificationJobItem.id.in_(
                original_target["item_ids"]
            )
        ).count()
        if original_target["item_ids"]
        else 0
    )
    if after_counts["raw_sources"] != before_counts["raw_sources"]:
        raise MaintenanceSafetyError("Raw Week 1 source count changed")
    if any(
        after_counts[key] != 0
        for key in ("target_classifications", "target_jobs", "target_job_items")
    ):
        raise MaintenanceSafetyError("Target classifier state was not fully removed")
    if week.status != "finalized":
        raise MaintenanceSafetyError("Week 1 is no longer finalized")
    result_count = db.query(app_module.Result).join(app_module.Fixture).filter(
        app_module.Fixture.week_id == week.id
    ).count()
    if result_count != 10 or week_pick_query(db, app_module, week.id).count() != 30:
        raise MaintenanceSafetyError("Week 1 results or picks changed")
    player_names = {player.name for player in db.query(app_module.Player).all()}
    if player_names != EXPECTED_PLAYER_NAMES:
        raise MaintenanceSafetyError("Player names changed")
    if after_counts["recaps"] != before_counts["recaps"]:
        raise MaintenanceSafetyError("Week 1 recap count changed")
    if protected_state(db, app_module, remaining) != before_protected:
        raise MaintenanceSafetyError("Protected game, source, recap, or classifier state changed")
    if archived_staging_state(db, app_module) != before_archived:
        raise MaintenanceSafetyError("Archived v2-staging classifier state changed")
    return after_counts


def reset_week1_classifier_state(
    db: Any,
    app_module: Any,
    *,
    dry_run: bool,
    backup_path: Optional[Path] = None,
) -> dict[str, Any]:
    season, week = resolve_destination(db, app_module)
    target = target_scope(db, app_module, season, week)
    before_counts = counts_for_report(db, app_module, week, target)
    before_archived = archived_staging_state(db, app_module)
    before_protected = protected_state(db, app_module, target)
    print("Reset scope before deletion:")
    print(f"  raw Week 1 sources: {before_counts['raw_sources']}")
    print(f"  target classifications: {before_counts['target_classifications']}")
    print(f"  target jobs: {before_counts['target_jobs']}")
    print(f"  target job items: {before_counts['target_job_items']}")
    print(f"  Week 1 recaps: {before_counts['recaps']}")
    if dry_run:
        db.rollback()
        print("Dry run complete: no backup created and no classifier state deleted.")
        return {"dry_run": True, **before_counts, "backup_path": None}
    if backup_path is None:
        raise MaintenanceSafetyError("A verified staging backup is required before writes")

    try:
        if target["classification_ids"]:
            db.query(app_module.CorrespondentSourceClassification).filter(
                app_module.CorrespondentSourceClassification.id.in_(
                    target["classification_ids"]
                )
            ).delete(synchronize_session=False)
        if target["item_ids"]:
            db.query(app_module.CorrespondentClassificationJobItem).filter(
                app_module.CorrespondentClassificationJobItem.id.in_(target["item_ids"])
            ).delete(synchronize_session=False)
        if target["job_ids"]:
            db.query(app_module.CorrespondentClassificationJob).filter(
                app_module.CorrespondentClassificationJob.id.in_(target["job_ids"])
            ).delete(synchronize_session=False)
        db.flush()
        after_counts = verify_reset_state(
            db,
            app_module,
            season,
            week,
            target,
            before_counts,
            before_protected,
            before_archived,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    print("Reset verified:")
    print(f"  raw Week 1 sources unchanged: {after_counts['raw_sources']}")
    print("  target classifications/jobs/items: 0/0/0")
    print("  Week 1 remains finalized with 10 results and 30 picks")
    print("  player names and recap count unchanged")
    archived_counts = {
        key: len(before_archived[key])
        for key in ("classifications", "jobs", "items")
    }
    print(f"  archived v2-staging state unchanged: {archived_counts}")
    print(f"Backup: {backup_path}")
    return {
        "dry_run": False,
        "deleted_classifications": before_counts["target_classifications"],
        "deleted_jobs": before_counts["target_jobs"],
        "deleted_job_items": before_counts["target_job_items"],
        "raw_sources": after_counts["raw_sources"],
        "recaps": after_counts["recaps"],
        "archived": archived_counts,
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
        reset_week1_classifier_state(
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
