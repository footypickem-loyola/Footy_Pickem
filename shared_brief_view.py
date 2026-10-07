"""Training Ground read adapter. No workflow, generation, initialization or writes."""
import os
from pick_insight_enrichment import verified_mapping
from shared_brief_context import identity
from shared_brief_store import read_saved


def selected_briefs(db, models, season, week, picks):
    if season is None or week is None or len(picks) != 5:
        return []
    rows = []
    try:
        club_ids = {name: key for key, name in verified_mapping().items()}
    except (OSError, ValueError, KeyError):
        club_ids = {}
    fixtures = {f.id: f for f in db.query(models.Fixture).filter(
        models.Fixture.week_id == week.id, models.Fixture.id.in_([p['fixture_id'] for p in picks])).all()}
    for pick in picks:
        f = fixtures.get(pick['fixture_id'])
        body = None
        if (f is not None and f.external_match_id and f.kickoff_utc and season.api_competition_code == 'PL'
                and season.api_season_year == 2026 and pick['team'] in (f.home, f.away) and pick['team'] in club_ids):
            body = read_saved(os.environ.get('SHARED_BRIEF_STORE'),
                identity(season.api_season_year, f.external_match_id, club_ids[pick['team']]),
                os.environ.get('SHARED_BRIEF_CONTENT_VERSION'), kickoff=f.kickoff_utc,
                home=f.home, away=f.away, team=pick['team'])
        rows.append(dict(home=pick['home'], away=pick['away'], team=pick['team'], body=body))
    return rows
