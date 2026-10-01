"""Read-only live consequence and factual context layer; no provider objects."""
from datetime import datetime, timedelta
import re

GOALS = {'goal', 'own_goal', 'penalty'}
STARTED = {'live', 'halftime', 'break', 'finished', 'suspended', 'interrupted', 'abandoned'}


def contribution(team, home, home_score, away_score):
    if home_score is None or away_score is None:
        return None
    sign = (home_score > away_score) - (home_score < away_score)
    return sign if team == home else -sign


def factual_context(db, models, week_id):
    """JSON-safe normalized event facts for future Correspondent consumers."""
    fixtures = db.query(models.Fixture).filter_by(week_id=week_id).all()
    result = []
    for fixture in fixtures:
        state = db.get(models.LiveFixtureState, fixture.id)
        official = db.query(models.Result).filter_by(fixture_id=fixture.id).first()
        events = db.query(models.MatchEvent).filter_by(fixture_id=fixture.id, is_active=True).order_by(
            models.MatchEvent.minute, models.MatchEvent.extra_minute, models.MatchEvent.sort_order, models.MatchEvent.id).all()
        result.append(dict(fixture_id=fixture.id, home=fixture.home, away=fixture.away,
            kickoff=fixture.kickoff_utc.isoformat() if fixture.kickoff_utc else None,
            official=bool(official), state='official' if official else state.state if state else 'not_started',
            home_score=official.home_score if official else state.home_score if state else None,
            away_score=official.away_score if official else state.away_score if state else None,
            events=[{key: getattr(e, key) for key in ('id', 'event_type', 'minute', 'extra_minute',
                'side', 'team_name', 'player_name', 'related_player_name', 'detail', 'running_score', 'sort_order')}
                for e in events]))
    return result


def enrich_matchweek(model, db, models, week, now=None):
    """Overlay only unfinished, complete drafts. Official rows always win."""
    now = now or datetime.utcnow()
    if week.status == 'finalized' or model['archived']:
        return model
    facts = factual_context(db, models, week.id)
    facts = {f['fixture_id']: f for f in facts}
    states = {s.fixture_id: s for s in db.query(models.LiveFixtureState).filter(
        models.LiveFixtureState.fixture_id.in_(facts))}
    # Existing partial official results alone retain the established ready view.
    underway = any(state.state in STARTED for state in states.values())
    for view in [model['primary'], *model['others']]:
        if not view or not view['draft_complete'] or not underway:
            continue
        view.update(state='live', state_label='Live · Provisional', timeline=[], progress={'live': 0, 'finished': 0, 'upcoming': 0}, stale=False, incomplete=False)
        for player in view['players']:
            player['official_for'] = sum(row['contribution'] or 0 for row in player['owned'])
            for pick in player['owned']:
                fact = facts[pick['fixture_id']]
                state = states.get(pick['fixture_id'])
                official = fact['official']
                status = fact['state']
                pick['live_status'] = 'Official FT' if official else {'live': 'LIVE', 'halftime': 'HT', 'finished': 'FT · Provisional', 'not_started': 'Upcoming'}.get(status, status.capitalize())
                pick['minute'] = state.minute if state and not official else None
                pick['extra_minute'] = state.extra_minute if state and not official else None
                pick['events'] = fact['events']
                pick['goal_correction'] = any(e['event_type'] == 'var' and e['detail'] == '1512' for e in fact['events'])
                pick['stale'] = not official and ((state is not None and (now - state.last_synced_at).total_seconds() > 90)
                    or (state is None and pick['kickoff_utc'] is not None and pick['kickoff_utc'] <= now))
                view['stale'] = view['stale'] or pick['stale']
                bucket = 'finished' if official or status == 'finished' else 'live' if status in STARTED else 'upcoming'
                view['progress'][bucket] += 1
                if not official:
                    started = status in STARTED
                    pick['contribution'] = contribution(pick['team'], pick['home'], fact['home_score'], fact['away_score']) if started else 0
                    pick['outcome'] = {1: 'correct', -1: 'incorrect', 0: 'draw', None: 'pending'}[pick['contribution']] if started else 'pending'
                    pick['score'] = f"{fact['home_score']}–{fact['away_score']}" if started and fact['home_score'] is not None and fact['away_score'] is not None else None
                    view['incomplete'] = view['incomplete'] or (started and pick['contribution'] is None)
                previous = (0, 0)
                for event in fact['events']:
                    if event['event_type'] not in GOALS:
                        continue
                    match = re.fullmatch(r'(\d+)\s*[-:]\s*(\d+)', event['running_score'] or '')
                    score = tuple(map(int, match.groups())) if match else None
                    # Only assign before/after impact to an unbroken one-goal progression.
                    impact = None
                    if not pick['goal_correction'] and score and previous and sum(score) == sum(previous) + 1 and all(a >= b for a, b in zip(score, previous)):
                        impact = (contribution(pick['team'], pick['home'], *previous), contribution(pick['team'], pick['home'], *score))
                    previous = score
                    clock = (pick['kickoff_utc'] or datetime.min) + timedelta(minutes=(event['minute'] or 0) + (event['extra_minute'] or 0))
                    view['timeline'].append(dict(event, home=pick['home'], away=pick['away'], owner=player['name'],
                        team=pick['team'], impact=impact, clock=clock, fixture_id=pick['fixture_id'], goal_correction=pick['goal_correction']))
            player['for'] = sum(p['contribution'] or 0 for p in player['owned'])
            player['remaining'] = sum(p['live_status'] not in ('Official FT', 'FT · Provisional') for p in player['owned'])
        a, b = view['players']
        for player, opponent in ((a, b), (b, a)):
            player['against'] = opponent['for']
            player['net'] = player['for'] - opponent['for']
        view['projected_payout'] = abs(a['net']) * 5
        view['projected_winner'] = a['name'] if a['net'] > 0 else b['name'] if a['net'] < 0 else None
        view['timeline'].sort(key=lambda e: (e['clock'], e['fixture_id'], e['sort_order'], e['id']))
    return model
