#!/usr/bin/env python3
"""Guarded staging-only setup and cleanup for Correspondent V2 Week 99 E2E testing."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.engine import make_url


EXPECTED_STAGING_DB_PATH = Path("/data/pickem_staging.db").resolve()
PRODUCTION_DB_PATH = Path("/data/pickem.db").resolve()
SEASON_CODE = "year-2"
WEEK_NUMBER = 99
ROOM_CODE = "E2E-CORR-W99"
MANIFEST_FILENAME = "correspondent_week99_e2e_manifest.json"
MANIFEST_SCHEMA_VERSION = 1
SOURCE_MARKER = "correspondent-week99-e2e"
EXPECTED_PLAYER_COUNT = 6

FIXTURE_PLAN = (
    ("Arsenal", "Chelsea", "Home", 2, 1),
    ("Liverpool", "Newcastle", "Away", 1, 2),
    ("Man City", "Bournemouth", "Home", 3, 1),
    ("Tottenham", "Brighton", "Away", 0, 1),
    ("Everton", "Fulham", "Home", 1, 0),
    ("Sunderland", "Leeds", "Home", 2, 0),
    ("Aston Villa", "Man United", "Away", 1, 2),
    ("Brentford", "Crystal Palace", "Home", 2, 1),
    ("Nottingham", "Wolves", "Away", 0, 2),
    ("Coventry City", "Hull City", "Home", 1, 0),
)

# Correct/incorrect choices for each matchup. Alternating fixtures are owned by
# each player, yielding 5 picks per player and both close and clear outcomes.
CORRECTNESS_PLAN = (
    (True, True, True, False, True, False, True, True, True, False),
    (True, True, False, False, True, True, False, True, True, False),
    (True, True, True, False, True, True, False, True, True, False),
)


class MaintenanceSafetyError(RuntimeError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def validated_staging_db_path(
    database_url: str,
    *,
    allow_test_db: bool = False,
) -> Path:
    """Accept the exact staging path; tests may explicitly opt into a temp DB."""
    if not isinstance(database_url, str) or not database_url.strip():
        raise MaintenanceSafetyError("DB_PATH must be explicitly configured")
    try:
        parsed = make_url(database_url.strip())
    except Exception as exc:
        raise MaintenanceSafetyError("DB_PATH is not a valid SQLAlchemy URL") from exc
    if not parsed.drivername.startswith("sqlite") or not parsed.database:
        raise MaintenanceSafetyError("DB_PATH must use SQLite")
    resolved = Path(parsed.database).expanduser().resolve()
    if resolved == PRODUCTION_DB_PATH:
        raise MaintenanceSafetyError("Refusing production database /data/pickem.db")
    if resolved != EXPECTED_STAGING_DB_PATH and not allow_test_db:
        raise MaintenanceSafetyError(
            "Refusing to run: DB_PATH must resolve exactly to "
            "/data/pickem_staging.db"
        )
    if not resolved.is_file():
        raise MaintenanceSafetyError(f"Database does not exist: {resolved}")
    return resolved


def create_sqlite_backup(database_path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = database_path.with_name(
        f"{database_path.stem}.week99-e2e-backup-{timestamp}{database_path.suffix}"
    )
    source = sqlite3.connect(str(database_path))
    destination = sqlite3.connect(str(backup_path))
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return backup_path


def default_manifest_path(database_path: Path) -> Path:
    return database_path.with_name(MANIFEST_FILENAME)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    return value


def _model_rows(query: Any, model: Any) -> list[list[Any]]:
    rows = query.order_by(model.id.asc()).all()
    return [
        [_json_value(getattr(row, column.name)) for column in model.__table__.columns]
        for row in rows
    ]


def tracked_models(app_module: Any) -> tuple[Any, ...]:
    return (
        app_module.Player,
        app_module.Season,
        app_module.Week,
        app_module.Fixture,
        app_module.Result,
        app_module.Matchup,
        app_module.Pick,
        app_module.CorrespondentSource,
        app_module.CorrespondentSourceClassification,
        app_module.CorrespondentClassificationJob,
        app_module.CorrespondentClassificationJobItem,
        app_module.CorrespondentXCollectionState,
        app_module.CorrespondentXPageReceipt,
        app_module.WeeklyRecap,
        app_module.RecapSourceUsage,
        app_module.WeeklyRecapSelection,
        app_module.CorrespondentEmailDelivery,
        app_module.CorrespondentNotification,
    )


def table_counts(db: Any, app_module: Any) -> dict[str, int]:
    return {
        model.__tablename__: db.query(model).count()
        for model in tracked_models(app_module)
    }


def unaffected_snapshot(
    db: Any,
    app_module: Any,
    *,
    owned_week_id: Optional[int],
) -> str:
    """Hash every tracked row not owned by the disposable Week 99 test."""
    fixture_ids = set()
    matchup_ids = set()
    source_ids = set()
    job_ids = set()
    collection_ids = set()
    recap_ids = set()
    if owned_week_id is not None:
        fixture_ids = {
            row.id for row in db.query(app_module.Fixture).filter_by(
                week_id=owned_week_id
            ).all()
        }
        matchup_ids = {
            row.id for row in db.query(app_module.Matchup).filter_by(
                week_id=owned_week_id
            ).all()
        }
        source_ids = {
            row.id for row in db.query(app_module.CorrespondentSource).filter_by(
                week_id=owned_week_id
            ).all()
        }
        job_ids = {
            row.id for row in db.query(app_module.CorrespondentClassificationJob).filter_by(
                week_id=owned_week_id
            ).all()
        }
        collection_ids = {
            row.id for row in db.query(app_module.CorrespondentXCollectionState).filter_by(
                week_id=owned_week_id
            ).all()
        }
        recap_ids = {
            row.id for row in db.query(app_module.WeeklyRecap).filter_by(
                week_id=owned_week_id
            ).all()
        }

    queries = {
        app_module.Player: db.query(app_module.Player),
        app_module.Season: db.query(app_module.Season),
        app_module.Week: db.query(app_module.Week).filter(
            app_module.Week.id != owned_week_id
        ) if owned_week_id is not None else db.query(app_module.Week),
        app_module.Fixture: db.query(app_module.Fixture).filter(
            app_module.Fixture.week_id != owned_week_id
        ) if owned_week_id is not None else db.query(app_module.Fixture),
        app_module.Result: db.query(app_module.Result).filter(
            ~app_module.Result.fixture_id.in_(fixture_ids)
        ) if fixture_ids else db.query(app_module.Result),
        app_module.Matchup: db.query(app_module.Matchup).filter(
            app_module.Matchup.week_id != owned_week_id
        ) if owned_week_id is not None else db.query(app_module.Matchup),
        app_module.Pick: db.query(app_module.Pick).filter(
            ~app_module.Pick.matchup_id.in_(matchup_ids)
        ) if matchup_ids else db.query(app_module.Pick),
        app_module.CorrespondentSource: db.query(app_module.CorrespondentSource).filter(
            app_module.CorrespondentSource.week_id != owned_week_id
        ) if owned_week_id is not None else db.query(app_module.CorrespondentSource),
        app_module.CorrespondentSourceClassification:
            db.query(app_module.CorrespondentSourceClassification).filter(
                ~app_module.CorrespondentSourceClassification.source_id.in_(source_ids)
            ) if source_ids else db.query(app_module.CorrespondentSourceClassification),
        app_module.CorrespondentClassificationJob:
            db.query(app_module.CorrespondentClassificationJob).filter(
                app_module.CorrespondentClassificationJob.week_id != owned_week_id
            ) if owned_week_id is not None else db.query(app_module.CorrespondentClassificationJob),
        app_module.CorrespondentClassificationJobItem:
            db.query(app_module.CorrespondentClassificationJobItem).filter(
                ~app_module.CorrespondentClassificationJobItem.job_id.in_(job_ids)
            ) if job_ids else db.query(app_module.CorrespondentClassificationJobItem),
        app_module.CorrespondentXCollectionState:
            db.query(app_module.CorrespondentXCollectionState).filter(
                app_module.CorrespondentXCollectionState.week_id != owned_week_id
            ) if owned_week_id is not None else db.query(app_module.CorrespondentXCollectionState),
        app_module.CorrespondentXPageReceipt:
            db.query(app_module.CorrespondentXPageReceipt).filter(
                ~app_module.CorrespondentXPageReceipt.collection_id.in_(collection_ids)
            ) if collection_ids else db.query(app_module.CorrespondentXPageReceipt),
        app_module.WeeklyRecap: db.query(app_module.WeeklyRecap).filter(
            app_module.WeeklyRecap.week_id != owned_week_id
        ) if owned_week_id is not None else db.query(app_module.WeeklyRecap),
        app_module.RecapSourceUsage: db.query(app_module.RecapSourceUsage).filter(
            ~app_module.RecapSourceUsage.recap_id.in_(recap_ids)
        ) if recap_ids else db.query(app_module.RecapSourceUsage),
        app_module.WeeklyRecapSelection:
            db.query(app_module.WeeklyRecapSelection).filter(
                app_module.WeeklyRecapSelection.week_id != owned_week_id
            ) if owned_week_id is not None else db.query(app_module.WeeklyRecapSelection),
        app_module.CorrespondentEmailDelivery:
            db.query(app_module.CorrespondentEmailDelivery).filter(
                ~app_module.CorrespondentEmailDelivery.recap_id.in_(recap_ids)
            ) if recap_ids else db.query(app_module.CorrespondentEmailDelivery),
        app_module.CorrespondentNotification:
            db.query(app_module.CorrespondentNotification).filter(
                app_module.CorrespondentNotification.week_id != owned_week_id
            ) if owned_week_id is not None else db.query(app_module.CorrespondentNotification),
    }
    payload = {
        model.__tablename__: _model_rows(queries[model], model)
        for model in tracked_models(app_module)
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def resolve_active_year2(db: Any, app_module: Any) -> Any:
    active = db.query(app_module.Season).filter_by(
        is_active=1,
        is_archived=0,
    ).all()
    if len(active) != 1 or active[0].code != SEASON_CODE:
        raise MaintenanceSafetyError(
            "The sole active, non-archived season must be year-2"
        )
    return active[0]


def require_protected_weeks(db: Any, app_module: Any, season: Any) -> dict[int, int]:
    weeks = db.query(app_module.Week).filter(
        app_module.Week.season_id == season.id,
        app_module.Week.number.in_((1, 2, 3)),
    ).all()
    by_number = {week.number: week.id for week in weeks}
    if set(by_number) != {1, 2, 3}:
        raise MaintenanceSafetyError("Active year-2 Weeks 1, 2, and 3 must all exist")
    return by_number


def require_six_existing_players(db: Any, app_module: Any, season: Any) -> list[Any]:
    players = app_module.season_players(db, season)
    if len(players) != EXPECTED_PLAYER_COUNT or len({player.id for player in players}) != 6:
        raise MaintenanceSafetyError("Active year-2 must resolve to exactly six players")
    return sorted(players, key=lambda player: player.id)


def _losing_team(fixture: Any, outcome: str) -> str:
    return fixture.away if outcome == "Home" else fixture.home


def _winning_team(fixture: Any, outcome: str) -> str:
    return fixture.home if outcome == "Home" else fixture.away


def build_seed_records(
    db: Any,
    app_module: Any,
    season: Any,
    players: list[Any],
    *,
    now: datetime,
) -> tuple[Any, dict[str, list[Any]]]:
    week = app_module.Week(
        season_id=season.id,
        number=WEEK_NUMBER,
        room_code=ROOM_CODE,
        status="finalized",
        finalized_at=now,
    )
    db.add(week)
    db.flush()

    fixtures = []
    results = []
    first_kickoff = now - timedelta(days=3)
    for index, (home, away, outcome, home_score, away_score) in enumerate(
        FIXTURE_PLAN,
        start=1,
    ):
        fixture = app_module.Fixture(
            week_id=week.id,
            match_number=index,
            home=home,
            away=away,
            external_match_id=None,
            kickoff_utc=first_kickoff + timedelta(hours=(index - 1) * 4),
            api_status="FINISHED",
        )
        db.add(fixture)
        db.flush()
        fixtures.append(fixture)
        result = app_module.Result(
            fixture_id=fixture.id,
            outcome=outcome,
            home_score=home_score,
            away_score=away_score,
            source="week99-e2e-helper",
            updated_at=now,
        )
        db.add(result)
        results.append(result)

    matchups = []
    picks = []
    player_pairs = ((players[0], players[1]), (players[2], players[3]), (players[4], players[5]))
    for matchup_index, (player_a, player_b) in enumerate(player_pairs):
        matchup = app_module.Matchup(
            week_id=week.id,
            player_a_id=player_a.id,
            player_b_id=player_b.id,
            first_picker_id=player_a.id,
        )
        db.add(matchup)
        db.flush()
        matchups.append(matchup)
        for fixture_index, fixture in enumerate(fixtures):
            picker = player_a if fixture_index % 2 == 0 else player_b
            outcome = FIXTURE_PLAN[fixture_index][2]
            correct = CORRECTNESS_PLAN[matchup_index][fixture_index]
            team = (
                _winning_team(fixture, outcome)
                if correct
                else _losing_team(fixture, outcome)
            )
            pick = app_module.Pick(
                matchup_id=matchup.id,
                player_id=picker.id,
                fixture_id=fixture.id,
                team=team,
                created_at=now - timedelta(days=4, minutes=fixture_index),
            )
            db.add(pick)
            picks.append(pick)

    x_state = app_module.CorrespondentXCollectionState(
        week_id=week.id,
        status="completed",
        window_start=first_kickoff - timedelta(hours=2),
        window_end=now + timedelta(hours=1),
        next_token=None,
        page_count=0,
        retrieved_count=0,
        persisted_count=0,
        lease_token=None,
        lease_expires_at=None,
        last_error=None,
        cap_reached_at=None,
        completed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(x_state)

    source_specs = (
        (
            "pass1",
            fixtures[2],
            "A dominant second half helped Manchester City beat Bournemouth 3-1, changing two Week 99 matchup positions.",
            {"approved_routing": {"article_candidate": True}},
        ),
        (
            "pass2",
            fixtures[1],
            "A first-half red card shaped Newcastle's 2-1 win at Liverpool and mattered to several Week 99 picks.",
            {"approved_routing": {"article_candidate": True}},
        ),
        (
            "signal",
            None,
            "RT @week99reporter: Newcastle's win changed the Pick 'Em picture.",
            {
                "approved_routing": {
                    "article_candidate": False,
                    "attention_signal_only": True,
                },
                "objective_audit": {"is_retweet": True},
            },
        ),
    )
    sources = []
    for suffix, fixture, body, metadata in source_specs:
        external_id = f"{SOURCE_MARKER}-{suffix}"
        source = app_module.CorrespondentSource(
            week_id=week.id,
            fixture_id=None if fixture is None else fixture.id,
            provider="e2e-test",
            source_type="curated_post",
            external_id=external_id,
            canonical_url=f"https://example.test/{external_id}",
            author_name="Week 99 E2E Reporter",
            body_text=body,
            published_at=now - timedelta(hours=2),
            submitted_by_player_id=None,
            submission_note=SOURCE_MARKER,
            metadata_json=json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            content_hash=hashlib.sha256(
                f"{SOURCE_MARKER}:{external_id}:{body}".encode("utf-8")
            ).hexdigest(),
            status="accepted",
            created_at=now,
        )
        db.add(source)
        db.flush()
        sources.append(source)

    seeded_classification = app_module.SourceClassification(
        source_id=sources[1].id,
        pickem_impact="P1_MATCH_SHAPING",
        editorial_functions=("MATCH_EVENT", "FACT"),
        article_use="SUPPORT",
        confidence="LOW",
        route="AUTOMATED_REVIEW",
        reason_codes=("INSUFFICIENT_CONTEXT",),
        reason="Seeded low-confidence decision requiring the real automated second pass.",
    )
    seeded_batch = app_module.ClassificationBatch(
        classifications=(seeded_classification,),
        pass_number=1,
        model="week99-e2e-seed",
        provider_response_id=None,
    )
    classification = app_module.store_source_classification(
        db,
        seeded_classification,
        seeded_batch,
    )
    db.flush()

    return week, {
        "fixtures": fixtures,
        "results": results,
        "matchups": matchups,
        "picks": picks,
        "x_states": [x_state],
        "sources": sources,
        "classifications": [classification],
    }


def _ids(records: list[Any]) -> list[int]:
    return [int(record.id) for record in records]


def verify_seeded_records(
    db: Any,
    app_module: Any,
    week: Any,
    records: dict[str, list[Any]],
) -> None:
    if week.status != "finalized" or week.finalized_at is None:
        raise MaintenanceSafetyError("Week 99 was not finalized with finalized_at")
    fixtures = records["fixtures"]
    results = records["results"]
    matchups = records["matchups"]
    picks = records["picks"]
    sources = records["sources"]
    x_state = records["x_states"][0]
    if len(fixtures) != 10 or len(results) != 10:
        raise MaintenanceSafetyError("Week 99 must have exactly 10 fixtures and results")
    if any(fixture.external_match_id is not None for fixture in fixtures):
        raise MaintenanceSafetyError("Week 99 external_match_id values must all be NULL")
    if len(matchups) != 3 or len(picks) != 30:
        raise MaintenanceSafetyError("Week 99 must have 3 matchups and 30 picks")
    if Counter(pick.matchup_id for pick in picks) != Counter(
        {matchup.id: 10 for matchup in matchups}
    ):
        raise MaintenanceSafetyError("Each Week 99 matchup must have 10 picks")
    player_ids = {matchup.player_a_id for matchup in matchups} | {
        matchup.player_b_id for matchup in matchups
    }
    if Counter(pick.player_id for pick in picks) != Counter(
        {player_id: 5 for player_id in player_ids}
    ):
        raise MaintenanceSafetyError("Each Week 99 player must have 5 picks")
    if len({(pick.matchup_id, pick.fixture_id) for pick in picks}) != 30:
        raise MaintenanceSafetyError("Each matchup must have one pick per fixture")
    fixture_by_id = {fixture.id: fixture for fixture in fixtures}
    if any(
        pick.fixture_id not in fixture_by_id
        or pick.team not in {
            fixture_by_id[pick.fixture_id].home,
            fixture_by_id[pick.fixture_id].away,
        }
        or pick.team == "Draw"
        for pick in picks
    ):
        raise MaintenanceSafetyError("Week 99 picks must be valid home/away picks only")
    if (
        x_state.status != "completed"
        or x_state.page_count != 0
        or x_state.retrieved_count != 0
        or x_state.persisted_count != 0
        or x_state.next_token is not None
        or x_state.lease_token is not None
        or x_state.lease_expires_at is not None
        or x_state.completed_at is None
    ):
        raise MaintenanceSafetyError("Week 99 X collection state is not zero-spend completed")
    if db.query(app_module.CorrespondentXPageReceipt).filter_by(
        collection_id=x_state.id
    ).count() != 0:
        raise MaintenanceSafetyError("Seed must not create X page receipts")
    candidates, signal_only = app_module.classification_candidate_sources(db, week)
    if len(candidates) != 2 or len(signal_only) != 1:
        raise MaintenanceSafetyError("Expected two article candidates and one signal-only source")
    classifications = db.query(app_module.CorrespondentSourceClassification).filter(
        app_module.CorrespondentSourceClassification.source_id.in_(
            [source.id for source in sources]
        )
    ).all()
    if len(classifications) != 1 or classifications[0].route != "AUTOMATED_REVIEW":
        raise MaintenanceSafetyError("Expected one seeded Pass-1 AUTOMATED_REVIEW decision")
    if classifications[0].pass_number != 1:
        raise MaintenanceSafetyError("Seeded review decision must be Pass 1")
    if db.query(app_module.CorrespondentClassificationJob).filter_by(
        week_id=week.id
    ).count() != 0:
        raise MaintenanceSafetyError("Seed must not create classification jobs")
    if db.query(app_module.WeeklyRecap).filter_by(week_id=week.id).count() != 0:
        raise MaintenanceSafetyError("Seed must not create a recap")
    if db.query(app_module.CorrespondentNotification).filter_by(week_id=week.id).count() != 0:
        raise MaintenanceSafetyError("Seed must not create notifications")


def _manifest_ids(week: Any, records: dict[str, list[Any]]) -> dict[str, list[int]]:
    return {
        "weeks": [int(week.id)],
        "fixtures": _ids(records["fixtures"]),
        "results": _ids(records["results"]),
        "matchups": _ids(records["matchups"]),
        "picks": _ids(records["picks"]),
        "x_collection_states": _ids(records["x_states"]),
        "sources": _ids(records["sources"]),
        "classifications": _ids(records["classifications"]),
        "jobs": [],
        "job_items": [],
        "x_page_receipts": [],
        "recaps": [],
        "recap_source_usages": [],
        "recap_selections": [],
        "email_deliveries": [],
        "notifications": [],
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MaintenanceSafetyError(f"Week 99 manifest not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise MaintenanceSafetyError(f"Unable to read Week 99 manifest: {exc}") from exc
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise MaintenanceSafetyError("Week 99 manifest schema version is not supported")
    if manifest.get("marker") != SOURCE_MARKER:
        raise MaintenanceSafetyError("Week 99 manifest marker is invalid")
    return manifest


def seed_week99(
    db: Any,
    app_module: Any,
    *,
    database_path: Path,
    manifest_path: Path,
    dry_run: bool,
    backup_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    if db.new or db.dirty or db.deleted:
        raise MaintenanceSafetyError("Database session has pending unrelated changes")
    now = now or utcnow()
    season = resolve_active_year2(db, app_module)
    protected_weeks = require_protected_weeks(db, app_module, season)
    players = require_six_existing_players(db, app_module, season)
    if db.query(app_module.Week).filter_by(number=WEEK_NUMBER).count():
        raise MaintenanceSafetyError("Week 99 already exists; use teardown explicitly")
    if db.query(app_module.Week).filter_by(room_code=ROOM_CODE).count():
        raise MaintenanceSafetyError(f"Room code {ROOM_CODE} already exists")
    if manifest_path.exists():
        old_manifest = load_manifest(manifest_path)
        if old_manifest.get("status") != "torn_down":
            raise MaintenanceSafetyError("An active Week 99 manifest already exists")
    baseline_counts = table_counts(db, app_module)
    baseline_snapshot = unaffected_snapshot(db, app_module, owned_week_id=None)

    if dry_run:
        print(json.dumps({
            "command": "seed",
            "dry_run": True,
            "season": {"id": season.id, "code": season.code},
            "week_number": WEEK_NUMBER,
            "would_create": {
                "fixtures": 10,
                "results": 10,
                "matchups": 3,
                "picks": 30,
                "sources": 3,
                "seeded_classifications": 1,
                "x_pages": 0,
            },
        }, indent=2, sort_keys=True))
        db.rollback()
        return {"dry_run": True, "week_id": None}
    if backup_path is None:
        raise MaintenanceSafetyError("A verified SQLite backup is required before seed")

    manifest_written = False
    try:
        week, records = build_seed_records(
            db,
            app_module,
            season,
            players,
            now=now,
        )
        db.flush()
        verify_seeded_records(db, app_module, week, records)
        if unaffected_snapshot(db, app_module, owned_week_id=week.id) != baseline_snapshot:
            raise MaintenanceSafetyError("Seed changed records outside disposable Week 99")
        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "marker": SOURCE_MARKER,
            "status": "pending_commit",
            "database_path": str(database_path),
            "season_id": int(season.id),
            "season_code": season.code,
            "week_number": WEEK_NUMBER,
            "room_code": ROOM_CODE,
            "seeded_at": now.isoformat(timespec="microseconds"),
            "finalized_at": week.finalized_at.isoformat(timespec="microseconds"),
            "protected_weeks": {str(key): value for key, value in protected_weeks.items()},
            "baseline_counts": baseline_counts,
            "unaffected_snapshot": baseline_snapshot,
            "seeded_ids": _manifest_ids(week, records),
            "lifecycle_ids": {},
            "backup_path": str(backup_path),
        }
        write_manifest(manifest_path, manifest)
        manifest_written = True
        db.commit()
    except Exception:
        db.rollback()
        if manifest_written and manifest_path.exists():
            manifest_path.unlink()
        raise
    manifest["status"] = "seeded"
    write_manifest(manifest_path, manifest)

    summary = inspect_week99(
        db,
        app_module,
        manifest_path=manifest_path,
        emit=False,
        now=now,
    )
    summary.update({"dry_run": False, "backup_path": str(backup_path)})
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def _query_ids(query: Any) -> list[int]:
    return [int(row.id) for row in query.order_by(query.column_descriptions[0]["entity"].id.asc()).all()]


def validate_manifest_week(
    db: Any,
    app_module: Any,
    manifest: dict[str, Any],
    *,
    seed_only: bool,
) -> Any:
    configured_path = app_module._database_file_path(db.get_bind())
    if configured_path is None or str(configured_path) != manifest.get("database_path"):
        raise MaintenanceSafetyError("Manifest does not belong to the configured database")
    season = resolve_active_year2(db, app_module)
    require_protected_weeks(db, app_module, season)
    if manifest.get("season_id") != season.id or manifest.get("season_code") != season.code:
        raise MaintenanceSafetyError("Manifest season does not match active year-2")
    week_ids = manifest.get("seeded_ids", {}).get("weeks", [])
    if len(week_ids) != 1:
        raise MaintenanceSafetyError("Manifest must own exactly one Week 99 row")
    week = db.get(app_module.Week, week_ids[0])
    if (
        week is None
        or week.season_id != season.id
        or week.number != WEEK_NUMBER
        or week.room_code != ROOM_CODE
        or week.status != "finalized"
    ):
        raise MaintenanceSafetyError("Manifest does not match the current Week 99 record")

    expected = manifest["seeded_ids"]
    direct_queries = {
        "fixtures": db.query(app_module.Fixture).filter_by(week_id=week.id),
        "results": db.query(app_module.Result).join(app_module.Fixture).filter(
            app_module.Fixture.week_id == week.id
        ),
        "matchups": db.query(app_module.Matchup).filter_by(week_id=week.id),
        "picks": db.query(app_module.Pick).join(app_module.Matchup).filter(
            app_module.Matchup.week_id == week.id
        ),
        "x_collection_states": db.query(app_module.CorrespondentXCollectionState).filter_by(
            week_id=week.id
        ),
        "sources": db.query(app_module.CorrespondentSource).filter_by(week_id=week.id),
    }
    for key, query in direct_queries.items():
        if set(_query_ids(query)) != set(expected[key]):
            raise MaintenanceSafetyError(f"Manifest mismatch for Week 99 {key}")
    seeded_classifications = db.query(app_module.CorrespondentSourceClassification).filter(
        app_module.CorrespondentSourceClassification.id.in_(expected["classifications"])
    ).all()
    if len(seeded_classifications) != len(expected["classifications"]):
        raise MaintenanceSafetyError("Manifest mismatch for seeded classifications")
    x_state = db.get(
        app_module.CorrespondentXCollectionState,
        expected["x_collection_states"][0],
    )
    if (
        x_state.status != "completed"
        or x_state.page_count != 0
        or x_state.retrieved_count != 0
        or x_state.persisted_count != 0
        or x_state.next_token is not None
        or x_state.lease_token is not None
    ):
        raise MaintenanceSafetyError("Week 99 X state no longer matches zero-spend seed")
    if unaffected_snapshot(db, app_module, owned_week_id=week.id) != manifest.get(
        "unaffected_snapshot"
    ):
        raise MaintenanceSafetyError("Records outside disposable Week 99 changed")
    if seed_only:
        lifecycle_ids = collect_lifecycle_ids(db, app_module, week)
        if set(lifecycle_ids["classifications"]) != set(expected["classifications"]):
            raise MaintenanceSafetyError("Week 99 classifications no longer match the seed")
        if any(
            lifecycle_ids[key]
            for key in lifecycle_ids
            if key != "classifications"
        ):
            raise MaintenanceSafetyError("Week 99 automation already started")
    return week


def make_week99_eligible(
    db: Any,
    app_module: Any,
    *,
    manifest_path: Path,
    dry_run: bool,
    backup_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    if db.new or db.dirty or db.deleted:
        raise MaintenanceSafetyError("Database session has pending unrelated changes")
    manifest = load_manifest(manifest_path)
    if manifest.get("status") != "seeded":
        raise MaintenanceSafetyError("Week 99 manifest is not in seeded state")
    week = validate_manifest_week(db, app_module, manifest, seed_only=True)
    before = week.finalized_at
    if before is None or before.isoformat(timespec="microseconds") != manifest.get(
        "finalized_at"
    ):
        raise MaintenanceSafetyError("Week 99 finalized_at does not match the manifest")
    after = (now or utcnow()) - timedelta(minutes=61)
    if dry_run:
        print(json.dumps({
            "command": "make-eligible",
            "dry_run": True,
            "week_id": week.id,
            "before": before.isoformat(timespec="seconds"),
            "after": after.isoformat(timespec="seconds"),
        }, indent=2, sort_keys=True))
        db.rollback()
        return {"dry_run": True, "week_id": week.id, "before": before, "after": after}
    if backup_path is None:
        raise MaintenanceSafetyError("A verified SQLite backup is required before make-eligible")

    manifest["status"] = "eligibility_pending"
    manifest["finalized_at"] = after.isoformat(timespec="microseconds")
    manifest["eligibility_backup_path"] = str(backup_path)
    write_manifest(manifest_path, manifest)
    try:
        week.finalized_at = after
        db.flush()
        if unaffected_snapshot(db, app_module, owned_week_id=week.id) != manifest[
            "unaffected_snapshot"
        ]:
            raise MaintenanceSafetyError("make-eligible changed records outside Week 99")
        changed_week = db.get(app_module.Week, week.id)
        if (
            changed_week.finalized_at != after
            or changed_week.room_code != ROOM_CODE
            or changed_week.status != "finalized"
        ):
            raise MaintenanceSafetyError("make-eligible changed an unexpected Week 99 field")
        db.commit()
    except Exception:
        db.rollback()
        manifest["status"] = "seeded"
        manifest["finalized_at"] = before.isoformat(timespec="microseconds")
        manifest.pop("eligibility_backup_path", None)
        write_manifest(manifest_path, manifest)
        raise
    manifest["status"] = "eligible"
    write_manifest(manifest_path, manifest)
    summary = {"dry_run": False, "week_id": week.id, "before": before, "after": after}
    print(json.dumps({
        **summary,
        "before": before.isoformat(timespec="seconds"),
        "after": after.isoformat(timespec="seconds"),
        "backup_path": str(backup_path),
    }, indent=2, sort_keys=True))
    return summary


def collect_lifecycle_ids(db: Any, app_module: Any, week: Any) -> dict[str, list[int]]:
    source_ids = _query_ids(
        db.query(app_module.CorrespondentSource).filter_by(week_id=week.id)
    )
    job_ids = _query_ids(
        db.query(app_module.CorrespondentClassificationJob).filter_by(week_id=week.id)
    )
    collection_ids = _query_ids(
        db.query(app_module.CorrespondentXCollectionState).filter_by(week_id=week.id)
    )
    recap_ids = _query_ids(db.query(app_module.WeeklyRecap).filter_by(week_id=week.id))
    return {
        "classifications": _query_ids(
            db.query(app_module.CorrespondentSourceClassification).filter(
                app_module.CorrespondentSourceClassification.source_id.in_(source_ids)
            )
        ) if source_ids else [],
        "jobs": job_ids,
        "job_items": _query_ids(
            db.query(app_module.CorrespondentClassificationJobItem).filter(
                app_module.CorrespondentClassificationJobItem.job_id.in_(job_ids)
            )
        ) if job_ids else [],
        "x_page_receipts": _query_ids(
            db.query(app_module.CorrespondentXPageReceipt).filter(
                app_module.CorrespondentXPageReceipt.collection_id.in_(collection_ids)
            )
        ) if collection_ids else [],
        "recaps": recap_ids,
        "recap_source_usages": _query_ids(
            db.query(app_module.RecapSourceUsage).filter(
                app_module.RecapSourceUsage.recap_id.in_(recap_ids)
            )
        ) if recap_ids else [],
        "recap_selections": _query_ids(
            db.query(app_module.WeeklyRecapSelection).filter_by(week_id=week.id)
        ),
        "email_deliveries": _query_ids(
            db.query(app_module.CorrespondentEmailDelivery).filter(
                app_module.CorrespondentEmailDelivery.recap_id.in_(recap_ids)
            )
        ) if recap_ids else [],
        "notifications": _query_ids(
            db.query(app_module.CorrespondentNotification).filter_by(week_id=week.id)
        ),
    }


def inspect_week99(
    db: Any,
    app_module: Any,
    *,
    manifest_path: Path,
    emit: bool = True,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    week = validate_manifest_week(db, app_module, manifest, seed_only=False)
    status = app_module.correspondent_automation_status(db, week, now=now)
    source_ids = manifest["seeded_ids"]["sources"]
    summary = {
        "manifest_status": manifest.get("status"),
        "season": {"id": week.season_id, "code": SEASON_CODE},
        "week": {
            "id": week.id,
            "number": week.number,
            "status": week.status,
            "finalized_at": week.finalized_at.isoformat(timespec="seconds"),
        },
        "x_collection": status["x_collection"],
        "sources": {
            "total": len(source_ids),
            "article_candidates": status["classification"]["article_candidates"],
            "signal_only": status["classification"]["signal_only_sources"],
        },
        "classifications": {
            "total": db.query(app_module.CorrespondentSourceClassification).filter(
                app_module.CorrespondentSourceClassification.source_id.in_(source_ids)
            ).count(),
            "readiness": status["classification"],
        },
        "jobs": status["jobs"],
        "recap": status["recap"],
        "delivery": status["delivery"],
        "phase": status["phase"],
        "allowed_actions": status["allowed_actions"],
    }
    if emit:
        print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def _delete_ids(db: Any, model: Any, ids: list[int]) -> None:
    if ids:
        db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)


def teardown_week99(
    db: Any,
    app_module: Any,
    *,
    manifest_path: Path,
    dry_run: bool,
    backup_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    if db.new or db.dirty or db.deleted:
        raise MaintenanceSafetyError("Database session has pending unrelated changes")
    manifest = load_manifest(manifest_path)
    if manifest.get("status") not in {
        "pending_commit",
        "seeded",
        "eligibility_pending",
        "eligible",
        "teardown_pending",
    }:
        raise MaintenanceSafetyError("Week 99 manifest is not eligible for teardown")
    week = validate_manifest_week(db, app_module, manifest, seed_only=False)
    week_id = int(week.id)
    lifecycle_ids = collect_lifecycle_ids(db, app_module, week)
    expected_sources = set(manifest["seeded_ids"]["sources"])
    actual_sources = {
        row.id for row in db.query(app_module.CorrespondentSource).filter_by(
            week_id=week.id
        ).all()
    }
    if actual_sources != expected_sources:
        raise MaintenanceSafetyError("Manifest mismatch: Week 99 has unexpected sources")
    if dry_run:
        summary = {
            "command": "teardown",
            "dry_run": True,
            "week_id": week.id,
            "seeded_ids": manifest["seeded_ids"],
            "lifecycle_ids": lifecycle_ids,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        db.rollback()
        return summary
    if backup_path is None:
        raise MaintenanceSafetyError("A verified SQLite backup is required before teardown")

    manifest["status"] = "teardown_pending"
    manifest["lifecycle_ids"] = lifecycle_ids
    manifest["teardown_backup_path"] = str(backup_path)
    write_manifest(manifest_path, manifest)
    seeded = manifest["seeded_ids"]
    try:
        _delete_ids(db, app_module.CorrespondentEmailDelivery, lifecycle_ids["email_deliveries"])
        _delete_ids(db, app_module.WeeklyRecapSelection, lifecycle_ids["recap_selections"])
        _delete_ids(db, app_module.RecapSourceUsage, lifecycle_ids["recap_source_usages"])
        _delete_ids(db, app_module.CorrespondentClassificationJobItem, lifecycle_ids["job_items"])
        _delete_ids(db, app_module.CorrespondentClassificationJob, lifecycle_ids["jobs"])
        _delete_ids(db, app_module.CorrespondentSourceClassification, lifecycle_ids["classifications"])
        _delete_ids(db, app_module.CorrespondentNotification, lifecycle_ids["notifications"])
        _delete_ids(db, app_module.CorrespondentXPageReceipt, lifecycle_ids["x_page_receipts"])
        _delete_ids(db, app_module.WeeklyRecap, lifecycle_ids["recaps"])
        _delete_ids(db, app_module.CorrespondentSource, seeded["sources"])
        _delete_ids(db, app_module.CorrespondentXCollectionState, seeded["x_collection_states"])
        _delete_ids(db, app_module.Pick, seeded["picks"])
        _delete_ids(db, app_module.Result, seeded["results"])
        _delete_ids(db, app_module.Matchup, seeded["matchups"])
        _delete_ids(db, app_module.Fixture, seeded["fixtures"])
        _delete_ids(db, app_module.Week, seeded["weeks"])
        db.flush()
        if db.query(app_module.Week).filter_by(number=WEEK_NUMBER).count() != 0:
            raise MaintenanceSafetyError("Week 99 still exists after teardown")
        season = resolve_active_year2(db, app_module)
        protected = require_protected_weeks(db, app_module, season)
        if protected != {int(key): value for key, value in manifest["protected_weeks"].items()}:
            raise MaintenanceSafetyError("Protected Weeks 1-3 changed during teardown")
        if table_counts(db, app_module) != manifest["baseline_counts"]:
            raise MaintenanceSafetyError("Pre-test tracked table counts were not restored")
        if unaffected_snapshot(db, app_module, owned_week_id=None) != manifest[
            "unaffected_snapshot"
        ]:
            raise MaintenanceSafetyError("Pre-test unaffected rows were not restored")
        db.commit()
    except Exception:
        db.rollback()
        raise

    manifest["status"] = "torn_down"
    manifest["torn_down_at"] = (now or utcnow()).isoformat(timespec="microseconds")
    write_manifest(manifest_path, manifest)
    summary = {
        "dry_run": False,
        "week_id": week_id,
        "deleted_seeded_ids": seeded,
        "deleted_lifecycle_ids": lifecycle_ids,
        "backup_path": str(backup_path),
        "manifest_path": str(manifest_path),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "make-eligible", "teardown", "inspect"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    database_url = os.environ.get("DB_PATH", "")
    database_path = validated_staging_db_path(database_url)
    manifest_path = default_manifest_path(database_path)
    os.environ["INIT_ON_START"] = "0"
    app_module = importlib.import_module("pickem_flask_htmx_tabs")
    configured_path = app_module._database_file_path(app_module.engine)
    if configured_path != database_path:
        raise MaintenanceSafetyError("Loaded application database does not match guarded DB_PATH")
    db = app_module.SessionLocal()
    backup_path = None
    try:
        if args.command in {"seed", "make-eligible", "teardown"} and not args.dry_run:
            backup_path = create_sqlite_backup(database_path)
        if args.command == "seed":
            seed_week99(
                db,
                app_module,
                database_path=database_path,
                manifest_path=manifest_path,
                dry_run=args.dry_run,
                backup_path=backup_path,
            )
        elif args.command == "make-eligible":
            make_week99_eligible(
                db,
                app_module,
                manifest_path=manifest_path,
                dry_run=args.dry_run,
                backup_path=backup_path,
            )
        elif args.command == "teardown":
            teardown_week99(
                db,
                app_module,
                manifest_path=manifest_path,
                dry_run=args.dry_run,
                backup_path=backup_path,
            )
        else:
            inspect_week99(db, app_module, manifest_path=manifest_path)
    finally:
        db.close()
        app_module.SessionLocal.remove()
        app_module.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
