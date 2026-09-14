#!/usr/bin/env python3
"""One-off guarded staging seed for realistic 2026-27 Week 1 picks."""

from __future__ import annotations

import argparse
import importlib
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.engine import make_url


DESTINATION_SEASON_CODE = "year-2"
EXPECTED_PLAYER_NAMES = {
    1: ("Alpha", "Steve"),
    2: ("Bravo", "Joe"),
    3: ("Charlie", "Marc"),
    4: ("Delta", "Drew"),
    5: ("Echo", "Scott"),
    6: ("Foxtrot", "Connor"),
}
EXPECTED_FIXTURES = {
    381: ("Arsenal", "Coventry City"),
    382: ("Hull City", "Man United"),
    383: ("Ipswich Town", "Sunderland"),
    384: ("Nottingham", "Leeds United"),
    385: ("Everton", "Crystal Palace"),
    386: ("Brentford", "Tottenham"),
    387: ("Man City", "Bournemouth"),
    388: ("Brighton Hove", "Aston Villa"),
    389: ("Newcastle", "Liverpool"),
    390: ("Fulham", "Chelsea"),
}
EXPECTED_MATCHUPS = {
    115: (5, 3),
    116: (1, 4),
    117: (2, 6),
}

# Each tuple is fixture ID, player ID, and whether that player should receive
# the recorded winner (True) or the opposing side (False). Drawn results always
# score zero; the concrete stored pick is still valid and deterministic.
SYNTHETIC_DRAFT = {
    115: (
        (381, 5, True), (382, 3, False),
        (383, 5, True), (384, 3, False),
        (385, 5, True), (386, 3, False),
        (387, 5, True), (388, 3, False),
        (389, 5, True), (390, 3, False),
    ),
    116: (
        (381, 1, True), (382, 4, True),
        (383, 1, True), (384, 4, True),
        (385, 1, True), (386, 4, True),
        (387, 1, True), (388, 4, True),
        (389, 1, True), (390, 4, True),
    ),
    117: (
        (381, 2, True), (382, 6, False),
        (383, 2, False), (384, 6, True),
        (385, 2, True), (386, 6, False),
        (387, 2, False), (388, 6, True),
        (389, 2, True), (390, 6, False),
    ),
}


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


def week_pick_query(db: Any, app_module: Any, week_id: int) -> Any:
    return db.query(app_module.Pick).join(app_module.Matchup).filter(
        app_module.Matchup.week_id == week_id
    )


def resolve_destination(db: Any, app_module: Any) -> tuple[Any, list[Any], list[Any], list[Any]]:
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

    fixtures = db.query(app_module.Fixture).filter_by(week_id=week.id).order_by(
        app_module.Fixture.id.asc()
    ).all()
    actual_fixtures = {fixture.id: (fixture.home, fixture.away) for fixture in fixtures}
    if len(fixtures) != 10 or actual_fixtures != EXPECTED_FIXTURES:
        raise MaintenanceSafetyError("Destination Week 1 fixtures do not match the expected 10")

    results = db.query(app_module.Result).join(app_module.Fixture).filter(
        app_module.Fixture.week_id == week.id
    ).all()
    if len(results) != 10 or {result.fixture_id for result in results} != set(EXPECTED_FIXTURES):
        raise MaintenanceSafetyError("Destination Week 1 must contain exactly 10 results")
    if any(result.outcome not in {"Home", "Away", "Draw"} for result in results):
        raise MaintenanceSafetyError("Destination Week 1 contains an invalid result outcome")

    matchups = db.query(app_module.Matchup).filter_by(week_id=week.id).order_by(
        app_module.Matchup.id.asc()
    ).all()
    actual_matchups = {
        matchup.id: (matchup.player_a_id, matchup.player_b_id)
        for matchup in matchups
    }
    if actual_matchups != EXPECTED_MATCHUPS:
        raise MaintenanceSafetyError("Destination Week 1 matchups do not match IDs/pairs 115-117")
    if any(
        matchup.first_picker_id not in {matchup.player_a_id, matchup.player_b_id}
        for matchup in matchups
    ):
        raise MaintenanceSafetyError("A destination matchup has an invalid first picker")
    if week_pick_query(db, app_module, week.id).count() != 0:
        raise MaintenanceSafetyError("Destination Week 1 must currently contain 0 picks")
    return week, fixtures, results, matchups


def validate_players(db: Any, app_module: Any) -> dict[int, Any]:
    players = {
        player.id: player
        for player in db.query(app_module.Player).filter(
            app_module.Player.id.in_(EXPECTED_PLAYER_NAMES)
        ).all()
    }
    current = {player_id: player.name for player_id, player in players.items()}
    expected = {
        player_id: names[0]
        for player_id, names in EXPECTED_PLAYER_NAMES.items()
    }
    if current != expected:
        raise MaintenanceSafetyError(
            f"Player IDs 1-6 do not have the expected pre-seed names: {current}"
        )
    target_names = {names[1] for names in EXPECTED_PLAYER_NAMES.values()}
    conflicts = db.query(app_module.Player).filter(
        app_module.Player.name.in_(target_names),
        ~app_module.Player.id.in_(EXPECTED_PLAYER_NAMES),
    ).count()
    if conflicts:
        raise MaintenanceSafetyError("A target player name belongs to another player ID")
    return players


def selected_team(fixture: Any, result: Any, should_be_correct: bool) -> str:
    if result.outcome == "Draw":
        return "Draw" if should_be_correct else fixture.home
    winner = fixture.home if result.outcome == "Home" else fixture.away
    loser = fixture.away if result.outcome == "Home" else fixture.home
    return winner if should_be_correct else loser


def build_pick_plan(
    app_module: Any,
    fixtures: list[Any],
    results: list[Any],
    matchups: list[Any],
) -> list[Any]:
    fixture_by_id = {fixture.id: fixture for fixture in fixtures}
    result_by_fixture = {result.fixture_id: result for result in results}
    plan_by_matchup_player: dict[tuple[int, int], list[tuple[int, str]]] = {}
    for matchup_id, entries in SYNTHETIC_DRAFT.items():
        if {fixture_id for fixture_id, _player_id, _correct in entries} != set(EXPECTED_FIXTURES):
            raise MaintenanceSafetyError(f"Synthetic matchup {matchup_id} does not cover every fixture")
        player_counts = Counter(player_id for _fixture_id, player_id, _correct in entries)
        expected_players = set(EXPECTED_MATCHUPS[matchup_id])
        if set(player_counts) != expected_players or set(player_counts.values()) != {5}:
            raise MaintenanceSafetyError(f"Synthetic matchup {matchup_id} is not split 5/5")
        for fixture_id, player_id, should_be_correct in entries:
            fixture = fixture_by_id[fixture_id]
            team = selected_team(
                fixture,
                result_by_fixture[fixture_id],
                should_be_correct,
            )
            plan_by_matchup_player.setdefault((matchup_id, player_id), []).append(
                (fixture_id, team)
            )

    created_at = datetime(2026, 8, 20, 12, 0, 0)
    planned = []
    for matchup in matchups:
        first = matchup.first_picker_id
        second = (
            matchup.player_b_id
            if first == matchup.player_a_id
            else matchup.player_a_id
        )
        queues = {
            player_id: list(plan_by_matchup_player[(matchup.id, player_id)])
            for player_id in (matchup.player_a_id, matchup.player_b_id)
        }
        for index, player_id in enumerate(
            (first, second, second, first, first, second, second, first, first, second)
        ):
            fixture_id, team = queues[player_id].pop(0)
            planned.append(app_module.Pick(
                matchup_id=matchup.id,
                player_id=player_id,
                fixture_id=fixture_id,
                team=team,
                created_at=created_at + timedelta(seconds=index + (matchup.id * 20)),
            ))
    return planned


def synthetic_pick_points(fixture: Any, outcome: Optional[str], team: str) -> int:
    if outcome is None:
        return 0
    if outcome == "Draw":
        winning_pick = "Draw"
    elif outcome == "Home":
        winning_pick = fixture.home
    else:
        winning_pick = fixture.away
    return 1 if team == winning_pick else -1


def plan_metrics(planned: list[Any], fixtures: list[Any], results: list[Any]) -> dict[str, Any]:
    fixture_by_id = {fixture.id: fixture for fixture in fixtures}
    result_by_fixture = {result.fixture_id: result for result in results}
    points = Counter()
    correct = incorrect = 0
    for pick in planned:
        fixture = fixture_by_id[pick.fixture_id]
        outcome = result_by_fixture[pick.fixture_id].outcome
        delta = synthetic_pick_points(fixture, outcome, pick.team)
        points[pick.player_id] += delta
        if delta == 1:
            correct += 1
        elif delta == -1:
            incorrect += 1
    margins = {
        matchup_id: abs(points[player_a] - points[player_b])
        for matchup_id, (player_a, player_b) in EXPECTED_MATCHUPS.items()
    }
    if not correct or not incorrect:
        raise MaintenanceSafetyError("Synthetic picks must include correct and incorrect picks")
    if min(margins.values()) > 2 or max(margins.values()) < 4:
        raise MaintenanceSafetyError(
            f"Synthetic picks do not produce both close and clear matchups: {margins}"
        )
    return {"points": dict(points), "margins": margins, "correct": correct, "incorrect": incorrect}


def protected_state(db: Any, app_module: Any, week: Any) -> dict[str, Any]:
    fixtures = db.query(app_module.Fixture).filter_by(week_id=week.id).order_by(
        app_module.Fixture.id.asc()
    ).all()
    return {
        "week_status": week.status,
        "fixtures": [
            (row.id, row.week_id, row.match_number, row.home, row.away,
             row.external_match_id, row.kickoff_utc, row.api_status)
            for row in fixtures
        ],
        "results": [
            (row.id, row.fixture_id, row.outcome, row.home_score, row.away_score,
             row.source, row.updated_at)
            for row in db.query(app_module.Result).filter(
                app_module.Result.fixture_id.in_([fixture.id for fixture in fixtures])
            ).order_by(app_module.Result.id.asc()).all()
        ],
        "matchups": [
            (row.id, row.week_id, row.player_a_id, row.player_b_id, row.first_picker_id)
            for row in db.query(app_module.Matchup).filter_by(week_id=week.id).order_by(
                app_module.Matchup.id.asc()
            ).all()
        ],
        "sources": db.query(app_module.CorrespondentSource).count(),
        "classifications": db.query(app_module.CorrespondentSourceClassification).count(),
        "jobs": db.query(app_module.CorrespondentClassificationJob).count(),
        "job_items": db.query(app_module.CorrespondentClassificationJobItem).count(),
        "recaps": db.query(app_module.WeeklyRecap).count(),
        "recap_usage": db.query(app_module.RecapSourceUsage).count(),
        "recap_selections": db.query(app_module.WeeklyRecapSelection).count(),
    }


def verify_seeded_state(
    db: Any,
    app_module: Any,
    week: Any,
    before: dict[str, Any],
    preserved_first_pickers: dict[int, int],
) -> dict[str, Any]:
    if protected_state(db, app_module, week) != before:
        raise MaintenanceSafetyError("Protected correspondent or game state changed")
    picks = week_pick_query(db, app_module, week.id).all()
    if len(picks) != 30:
        raise MaintenanceSafetyError("Expected exactly 30 Week 1 picks")
    if Counter(pick.matchup_id for pick in picks) != Counter({115: 10, 116: 10, 117: 10}):
        raise MaintenanceSafetyError("Expected exactly 10 picks per matchup")
    if Counter(pick.player_id for pick in picks) != Counter({player_id: 5 for player_id in EXPECTED_PLAYER_NAMES}):
        raise MaintenanceSafetyError("Expected exactly 5 picks per player")
    pair_counts = Counter((pick.matchup_id, pick.fixture_id) for pick in picks)
    if len(pair_counts) != 30 or set(pair_counts.values()) != {1}:
        raise MaintenanceSafetyError("Expected one pick per fixture per matchup")
    if {pick.fixture_id for pick in picks} != set(EXPECTED_FIXTURES):
        raise MaintenanceSafetyError("A pick references a fixture outside Week 1")
    fixture_by_id = {fixture.id: fixture for fixture in db.query(app_module.Fixture).filter_by(
        week_id=week.id
    ).all()}
    if any(
        pick.team not in {fixture_by_id[pick.fixture_id].home,
                          fixture_by_id[pick.fixture_id].away, "Draw"}
        for pick in picks
    ):
        raise MaintenanceSafetyError("A pick has an invalid team value")
    names = {
        player.id: player.name
        for player in db.query(app_module.Player).filter(
            app_module.Player.id.in_(EXPECTED_PLAYER_NAMES)
        ).all()
    }
    if names != {player_id: values[1] for player_id, values in EXPECTED_PLAYER_NAMES.items()}:
        raise MaintenanceSafetyError("Player renames were not applied exactly")
    current_first_pickers = {
        matchup.id: matchup.first_picker_id
        for matchup in db.query(app_module.Matchup).filter_by(week_id=week.id).all()
    }
    if current_first_pickers != preserved_first_pickers:
        raise MaintenanceSafetyError("Matchup first-picker IDs changed")
    results = db.query(app_module.Result).join(app_module.Fixture).filter(
        app_module.Fixture.week_id == week.id
    ).all()
    metrics = plan_metrics(picks, list(fixture_by_id.values()), results)
    return {"pick_count": len(picks), **metrics}


def print_plan(planned: list[Any], fixtures: list[Any]) -> None:
    fixture_by_id = {fixture.id: fixture for fixture in fixtures}
    print("Synthetic Week 1 picks:")
    for pick in sorted(planned, key=lambda row: (row.matchup_id, row.fixture_id)):
        fixture = fixture_by_id[pick.fixture_id]
        player_name = EXPECTED_PLAYER_NAMES[pick.player_id][1]
        print(
            f"  matchup={pick.matchup_id} fixture={pick.fixture_id} "
            f"{fixture.home} vs {fixture.away}: player={pick.player_id} "
            f"{player_name} pick={pick.team}"
        )


def seed_week1(
    db: Any,
    app_module: Any,
    *,
    dry_run: bool,
    backup_path: Optional[Path] = None,
) -> dict[str, Any]:
    week, fixtures, results, matchups = resolve_destination(db, app_module)
    players = validate_players(db, app_module)
    preserved_first_pickers = {
        matchup.id: matchup.first_picker_id for matchup in matchups
    }
    before = protected_state(db, app_module, week)
    planned = build_pick_plan(app_module, fixtures, results, matchups)
    metrics = plan_metrics(planned, fixtures, results)
    print_plan(planned, fixtures)
    print(f"Planned points: {metrics['points']}; matchup margins: {metrics['margins']}")
    if dry_run:
        db.rollback()
        print("Dry run complete: no backup created, names changed, or picks written.")
        return {"dry_run": True, "would_insert": 30, **metrics, "backup_path": None}
    if backup_path is None:
        raise MaintenanceSafetyError("A verified staging backup is required before writes")

    try:
        for player_id, player in players.items():
            player.name = EXPECTED_PLAYER_NAMES[player_id][1]
        db.add_all(planned)
        db.flush()
        verified = verify_seeded_state(
            db,
            app_module,
            week,
            before,
            preserved_first_pickers,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    print(f"Verified Week 1 picks: {verified['pick_count']}")
    print(f"Verified points: {verified['points']}; margins: {verified['margins']}")
    print("Protected correspondent, recap, fixture, result, and matchup state unchanged.")
    print(f"Backup: {backup_path}")
    return {"dry_run": False, "inserted": 30, **verified, "backup_path": backup_path}


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
        seed_week1(
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
