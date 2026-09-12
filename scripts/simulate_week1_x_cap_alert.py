#!/usr/bin/env python3
"""Simulate the active year-2 Week 1 X cap alert in staging only."""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func
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


def _rows_snapshot(db: Any, models: tuple[Any, ...]) -> dict[str, tuple[Any, ...]]:
    return {
        model.__tablename__: tuple(
            tuple(getattr(row, column.name) for column in model.__table__.columns)
            for row in db.query(model).order_by(model.id.asc()).all()
        )
        for model in models
    }


def resolve_target(db: Any, app_module: Any, *, now: datetime) -> tuple[Any, Any]:
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
    completed_results, fixture_count = app_module.count_results_for_week(db, week)
    if week.status != "finalized" or fixture_count != 10 or completed_results != 10:
        raise MaintenanceSafetyError(
            "Active year-2 Week 1 must be finalized with 10 fixtures and 10 results"
        )
    if week.finalized_at is None:
        raise MaintenanceSafetyError("Active year-2 Week 1 finalized_at is required")
    if now < week.finalized_at + app_module.CORRESPONDENT_FINALIZATION_BUFFER:
        raise MaintenanceSafetyError("Active year-2 Week 1 is not automation-eligible")
    return season, week


def simulate_week1_x_cap_alert(
    db: Any,
    app_module: Any,
    *,
    now: datetime,
) -> dict[str, Any]:
    if db.new or db.dirty or db.deleted:
        raise MaintenanceSafetyError("Database session has pending unrelated changes")
    season, week = resolve_target(db, app_module, now=now)
    state = db.query(app_module.CorrespondentXCollectionState).filter_by(
        week_id=week.id
    ).one_or_none()
    notification = db.query(app_module.CorrespondentNotification).filter_by(
        week_id=week.id,
        event_type="x_weekly_cap_reached",
    ).one_or_none()
    if notification is not None:
        if notification.status == "sent":
            raise MaintenanceSafetyError("Week 1 X cap alert was already sent")
        raise MaintenanceSafetyError(
            "Week 1 already has X cap notification state; refusing an ambiguous duplicate"
        )
    before_state = app_module._x_collection_json(state)
    first_kickoff = db.query(func.min(app_module.Fixture.kickoff_utc)).filter_by(
        week_id=week.id
    ).scalar()
    if first_kickoff is None:
        raise MaintenanceSafetyError("Week 1 fixtures must have kickoff timestamps")
    expected_window_start = first_kickoff - timedelta(hours=2)
    expected_window_end = (
        week.finalized_at + app_module.CORRESPONDENT_FINALIZATION_BUFFER
    )
    if state is not None and not (
        state.status == "ready"
        and state.window_start == expected_window_start
        and state.window_end == expected_window_end
        and state.page_count == 0
        and state.retrieved_count == 0
        and state.persisted_count == 0
        and state.next_token is None
        and state.lease_token is None
        and state.lease_expires_at is None
        and state.last_error is None
        and state.cap_reached_at is None
        and state.completed_at is None
    ):
        raise MaintenanceSafetyError(
            "Week 1 X collection state is not pristine; refusing an ambiguous test"
        )

    protected_models = (
        app_module.Player,
        app_module.Season,
        app_module.Week,
        app_module.Fixture,
        app_module.Matchup,
        app_module.Pick,
        app_module.Result,
        app_module.CorrespondentSource,
        app_module.CorrespondentSourceClassification,
        app_module.CorrespondentClassificationJob,
        app_module.CorrespondentClassificationJobItem,
        app_module.WeeklyRecap,
        app_module.RecapSourceUsage,
        app_module.CorrespondentEmailDelivery,
        app_module.CorrespondentXPageReceipt,
    )
    protected_before = _rows_snapshot(db, protected_models)

    try:
        if state is None:
            state = app_module.CorrespondentXCollectionState(
                week_id=week.id,
                window_start=expected_window_start,
                window_end=expected_window_end,
                created_at=now,
            )
            db.add(state)
        state.status = "capped"
        state.next_token = None
        state.page_count = (
            app_module.CORRESPONDENT_X_WEEKLY_CAP
            // app_module.CORRESPONDENT_X_PAGE_SIZE
        )
        state.retrieved_count = app_module.CORRESPONDENT_X_WEEKLY_CAP
        state.persisted_count = app_module.CORRESPONDENT_X_WEEKLY_CAP
        state.lease_token = None
        state.lease_expires_at = None
        state.last_error = None
        state.cap_reached_at = now
        state.completed_at = now
        state.updated_at = now
        db.flush()
        notification = app_module._ensure_x_cap_notification(
            db,
            week,
            state,
            now=now,
        )
        db.flush()
        status = app_module.correspondent_automation_status(db, week, now=now)
        if "claim_x_cap_alert" not in status["allowed_actions"]:
            raise MaintenanceSafetyError(
                "Capped state did not expose the Gmail cap-alert action"
            )
        if _rows_snapshot(db, protected_models) != protected_before:
            raise MaintenanceSafetyError("An unrelated table changed during simulation")
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "season": {"id": season.id, "code": season.code},
        "week": {"id": week.id, "number": week.number},
        "before_x_collection": before_state,
        "before_notification": None,
        "after_x_collection": app_module._x_collection_json(state),
        "after_notification": app_module._notification_json(notification),
        "allowed_actions": status["allowed_actions"],
    }


def main() -> int:
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
        summary = simulate_week1_x_cap_alert(
            db,
            app_module,
            now=app_module.utcnow(),
        )
    finally:
        db.close()
        app_module.SessionLocal.remove()
        app_module.engine.dispose()

    print(f"DB path: {database_path}")
    print(f"Season: {summary['season']}")
    print(f"Week: {summary['week']}")
    print("Before X state:", json.dumps(summary["before_x_collection"], sort_keys=True))
    print("After X state:", json.dumps(summary["after_x_collection"], sort_keys=True))
    print("Before cap alert:", json.dumps(summary["before_notification"]))
    print("After cap alert:", json.dumps(summary["after_notification"], sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MaintenanceSafetyError as exc:
        print(f"ABORTED: {exc}", file=sys.stderr)
        raise SystemExit(2)
