"""Read-only presentation shaping for the Pick 'Em schedule."""

from collections import Counter, defaultdict
from datetime import timezone
from zoneinfo import ZoneInfo


def schedule_dates(fixtures):
    """Known fixture date range in the same timezone as Matchweek."""
    dates = sorted({(f.kickoff_utc.replace(tzinfo=timezone.utc)
                     if f.kickoff_utc.tzinfo is None else f.kickoff_utc)
                    .astimezone(ZoneInfo("America/New_York")).date()
                    for f in fixtures if f.kickoff_utc is not None})
    if not dates:
        return "Dates TBD"
    first, last = dates[0], dates[-1]
    def label(day):
        return f"{day:%b} {day.day}, {day.year}"
    return label(first) if first == last else f"{label(first)} – {label(last)}"


def build_fixtures(*, weeks, matchups, picks, fixtures, results, viewer_id):
    picks_per_matchup = Counter(pick.matchup_id for pick in picks)
    fixtures_by_week = defaultdict(list)
    for fixture in fixtures:
        fixtures_by_week[fixture.week_id].append(fixture)
    completed = {result.fixture_id for result in results}
    matchups_by_week = defaultdict(list)
    for matchup in matchups:
        matchups_by_week[matchup.week_id].append(matchup)
    league = []
    schedule = []
    for week in sorted(weeks, key=lambda item: item.number):
        if week.status == "finalized":
            continue
        week_fixtures = fixtures_by_week[week.id]
        dates = schedule_dates(week_fixtures)
        has_results = any(f.id in completed for f in week_fixtures)
        games = []
        for matchup in matchups_by_week[week.id]:
            count = picks_per_matchup[matchup.id]
            status = (("Results pending" if has_results else "Matchup set") if count == 10
                      else "Draft in progress" if count else "Draft not started")
            players = [matchup.player_a, matchup.player_b]
            own = viewer_id in [player.id for player in players]
            game = {"id": matchup.id, "players": [player.name for player in players],
                    "is_yours": own, "picks": count, "status": status}
            games.append(game)
            if own:
                schedule.append({"week": week.number, "dates": dates, "status": status, "picks": count,
                                 "opponent": next(p.name for p in players if p.id != viewer_id)})
        league.append({"number": week.number, "matchups": games,
                       "dates": dates,
                       "status": "Results pending" if has_results else "Upcoming"})
    return {"weeks": league, "schedule": schedule,
            "default_week": league[0]["number"] if league else None}
