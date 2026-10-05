"""Read-only PL reference retrieval. No Flask, importer, network or fuzzy matching."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re

MAPPING_PATH = Path(__file__).parent / "data" / "sportmonks_2026_27_mapping.json"
FINAL_STATES = (5, 7, 8)
RELEVANT_TYPES = (14, 15, 16, 20, 21)


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def season_start(name):
    match = re.fullmatch(r"(\d{4})[/-](\d{2}|\d{4})", name)
    if not match:
        return None
    first, last = int(match[1]), int(match[2])
    return first if last in (first + 1, (first + 1) % 100) else None


def empty_history(cutoff, status="reference_unavailable"):
    return dict(status=status, cutoff=utc(cutoff).isoformat(), exact_prior_season_fixture=None,
                most_recent_meeting=None, recent_h2h=[], venue_h2h=[],
                available_meeting_count=0, home_team_id=None, away_team_id=None,
                availability_semantics="results in this reference snapshot, not an as-known-at-the-time archive")


def resolve_club_ids(db, names):
    """Explicit verified aliases plus exact reference names; collisions fail closed."""
    aliases = {}
    for row in json.loads(MAPPING_PATH.read_text(encoding="utf-8"))["teams"]:
        for name in (row["footy_club"], row["sportmonks_name"]):
            aliases.setdefault(name, set()).add(row["sportmonks_team_id"])
    for row in db.execute("""SELECT home_team_id, home_team_name FROM football_reference_fixtures WHERE provider='sportmonks'
                            UNION SELECT away_team_id, away_team_name FROM football_reference_fixtures WHERE provider='sportmonks'"""):
        aliases.setdefault(row[1], set()).add(row[0])
    return [next(iter(aliases[name])) if len(aliases.get(name, ())) == 1 else None for name in names]


def load_fixture_history(db, *, fixture, as_of, target_season_start=None):
    """Use caller's consistent read transaction; SELECT only, no schema creation.

    Cutoff is match time, not ingestion time: backfilled matches remain usable.
    Unknown/non-final meetings form gaps, so history cannot silently bridge them.
    target_season_start overrides the default PL July-to-June season convention.
    """
    cutoff = min(utc(fixture.kickoff_utc), utc(as_of))
    history = empty_history(cutoff)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"football_reference_seasons", "football_reference_fixtures", "football_reference_events"} <= tables:
        return history
    home_id, away_id = resolve_club_ids(db, (fixture.home, fixture.away))
    history.update(home_team_id=home_id, away_team_id=away_id)
    if home_id is None or away_id is None or home_id == away_id:
        history["status"] = "unmapped_or_ambiguous_club"
        return history
    kickoff = utc(fixture.kickoff_utc)
    target_start = target_season_start if target_season_start is not None else kickoff.year - (kickoff.month < 7)
    history["target_season_start"] = target_start
    query = db.execute("""SELECT f.*, s.external_season_id, s.name AS season_name, s.sync_status,
                          s.last_successful_sync_at FROM football_reference_fixtures f
                          JOIN football_reference_seasons s ON s.id=f.reference_season_id
                          WHERE f.provider='sportmonks' AND s.provider='sportmonks'
                            AND f.league_id=8 AND s.league_id=8
                            AND ((f.home_team_id=? AND f.away_team_id=?) OR
                                 (f.home_team_id=? AND f.away_team_id=?))""", (home_id, away_id, away_id, home_id))
    names = [c[0] for c in query.description]
    meetings, gaps = [], []
    for values in query:
        row = dict(zip(names, values))
        try:
            played_at = utc(row["kickoff_utc"])
        except (ValueError, TypeError, AttributeError):
            gaps.append(dict(reference_fixture_id=row["id"], reason="unknown_kickoff", kickoff=None))
            continue
        if played_at >= cutoff:
            continue
        if (row["state_id"] not in FINAL_STATES or row["sync_status"] != "completed"
                or any(type(row[s]) is not int or row[s] < 0 for s in ("home_score", "away_score"))):
            gaps.append(dict(reference_fixture_id=row["id"], reason="incomplete_meeting", kickoff=played_at.isoformat()))
            continue
        events_query = db.execute("""SELECT * FROM football_reference_events
            WHERE reference_fixture_id=? AND provider='sportmonks' AND is_present=1
              AND is_active=1 AND (rescinded IS NULL OR rescinded=0)
            ORDER BY sort_order, external_event_id""", (row["id"],))
        columns = [c[0] for c in events_query.description]
        events = [dict(zip(columns, e)) for e in events_query]
        meetings.append(dict(reference_fixture_id=row["id"], external_fixture_id=row["external_fixture_id"],
            reference_season_id=row["reference_season_id"], external_season_id=row["external_season_id"],
            season=row["season_name"], season_start=season_start(row["season_name"]),
            kickoff=played_at.isoformat(), home_team_id=row["home_team_id"], home_team=row["home_team_name"],
            away_team_id=row["away_team_id"], away_team=row["away_team_name"],
            home_score=row["home_score"], away_score=row["away_score"],
            provider_updated_at=row["provider_updated_at"], snapshot_synced_at=row["last_successful_sync_at"],
            events=[e for e in events if e["type_id"] in RELEVANT_TYPES]))
    meetings.sort(key=lambda r: (r["kickoff"], r["external_fixture_id"]), reverse=True)
    venue = [r for r in meetings if r["home_team_id"] == home_id]
    prior = [r for r in venue if r["season_start"] is not None and r["season_start"] < target_start]
    prior.sort(key=lambda r: (r["season_start"], r["kickoff"], r["external_fixture_id"]), reverse=True)
    history.update(status="available" if meetings else "no_prior_meetings",
                   exact_prior_season_fixture=prior[0] if prior else None,
                   most_recent_meeting=meetings[0] if meetings else None,
                   recent_h2h=meetings[:5], venue_h2h=venue[:5],
                   available_meeting_count=len(meetings), available_venue_count=len(venue),
                   gaps=sorted(gaps, key=lambda r: r["reference_fixture_id"]))
    return history
