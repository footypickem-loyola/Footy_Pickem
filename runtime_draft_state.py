"""Shared read/write eligibility rules; the draft engine still owns snake order."""
from datetime import datetime, timezone
from sqlalchemy import text


def fixture_lock_reason(row, now=None):
    value=row['kickoff_utc']
    try:
        kickoff=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
        if kickoff is None:return 'Fixture kickoff is unverified'
        kickoff=kickoff.replace(tzinfo=timezone.utc) if kickoff.tzinfo is None else kickoff.astimezone(timezone.utc)
    except (ValueError,TypeError,AttributeError):return 'Fixture kickoff is unverified'
    if kickoff <= (now or datetime.now(timezone.utc)) or row['result'] is not None:
        return 'Fixture is locked'
    return None


def draft_lock_reason(db, context, matchup):
    status=db.execute(text('SELECT status FROM leagues WHERE id=:id'),dict(id=context['league']['id'])).scalar_one()
    season=db.execute(text('SELECT is_active,is_archived FROM seasons WHERE id=:id'),dict(id=context['season']['id'])).one()
    if status!='active':return 'League is not active'
    if not season.is_active or season.is_archived:return 'Season is read only'
    if context['week']['status']!='drafting':return 'Week is read only'
    if len(matchup['picks'])>=10:return 'Draft is complete'
    pair=[p['id'] for p in matchup['players']]
    count=db.execute(text("""SELECT COUNT(*) FROM league_season_memberships r
        JOIN players p ON p.id=r.membership_id WHERE r.season_id=:season
        AND r.membership_id IN (:a,:b) AND r.left_at IS NULL AND p.left_at IS NULL
        AND p.status='active' AND p.league_id=:league"""),
        dict(season=context['season']['id'],a=pair[0],b=pair[1],league=context['league']['id'])).scalar_one()
    if count!=2:return 'Matchup membership is no longer active'
    return None


def add_draft_state(db, context, domain):
    now=datetime.now(timezone.utc)
    for matchup in context['matchups']:
        count=len(matchup['picks']);pair=[p['id'] for p in matchup['players']]
        first=matchup['first_picker_id'];second=next(p for p in pair if p!=first)
        turn=domain.draft_turn_at(first,second,count) if domain is not None and count<10 else None
        used={p['fixture_id'] for p in matchup['picks']}
        available=[f for f in context['fixtures'] if f['id'] not in used and fixture_lock_reason(f,now) is None]
        reason=draft_lock_reason(db,context,matchup)
        if domain is None:reason='Draft runtime is unavailable'
        if reason is None and not available:reason='No eligible fixtures remain'
        matchup['draft']=dict(expected_count=count,turn_player_id=turn,locked=reason is not None,
            lock_reason=reason,can_pick=reason is None and turn==context['viewer']['membership_id'],
            available_fixtures=available)
    return context
