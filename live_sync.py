"""Transactional persistence and deterministic fixture identity, independent of HTTP."""
from datetime import datetime, timedelta
import logging

PROVIDER = 'sportmonks'
ALIASES = {'manchester city': 'man city', 'manchester united': 'man united',
           'tottenham hotspur': 'tottenham', 'wolverhampton wanderers': 'wolves',
           'nottingham forest': 'nottingham', 'brighton & hove albion': 'brighton',
           'brighton hove': 'brighton', 'newcastle united': 'newcastle', 'west ham united': 'west ham',
           'leeds united': 'leeds', 'afc bournemouth': 'bournemouth'}


def team_key(name):
    name = ' '.join(name.casefold().split())
    return ALIASES.get(name, name)


def resolve_fixture(db, models, item, league_id):
    if item.league_id != league_id:
        return None
    link = db.query(models.FixtureProviderLink).filter_by(provider=PROVIDER, external_fixture_id=item.external_id).first()
    if link:
        fixture = db.get(models.Fixture, link.fixture_id)
        if fixture and team_key(fixture.home) == team_key(item.home) and team_key(fixture.away) == team_key(item.away):
            return fixture
        logging.warning('Live fixture identity changed: provider fixture %s', item.external_id)
        return None
    candidates = db.query(models.Fixture).join(models.Week).join(models.Season).filter(
        models.Season.api_competition_code == 'PL', models.Season.is_archived == 0,
        models.Fixture.kickoff_utc == item.kickoff).all()
    candidates = [f for f in candidates if team_key(f.home) == team_key(item.home)
                  and team_key(f.away) == team_key(item.away)]
    if len(candidates) != 1:
        logging.warning('Live fixture mapping unresolved: provider fixture %s (%d matches)', item.external_id, len(candidates))
        return None
    fixture = candidates[0]
    if db.query(models.FixtureProviderLink).filter_by(fixture_id=fixture.id, provider=PROVIDER).first():
        logging.warning('Live fixture mapping conflict: fixture %s', fixture.id)
        return None
    db.add(models.FixtureProviderLink(fixture_id=fixture.id, provider=PROVIDER, external_fixture_id=item.external_id))
    db.flush()
    return fixture


def ingest(db, models, items, league_id=8, now=None):
    """Caller owns one transaction for the entire poll; never writes Result or Week."""
    now = now or datetime.utcnow()
    count = 0
    for item in items:
        fixture = resolve_fixture(db, models, item, league_id)
        if fixture is None:
            continue
        week = db.get(models.Week, fixture.week_id)
        if week.status == 'finalized' or week.season.is_archived:
            continue
        state = db.get(models.LiveFixtureState, fixture.id)
        if state is not None and item.state in ('unknown', 'pending', 'awaiting_updates'):
            # Provider connectivity states are not evidence of a return to pre-match.
            # Retain the last usable snapshot and let its freshness age naturally.
            continue
        if state is None:
            state = models.LiveFixtureState(fixture_id=fixture.id, provider=PROVIDER)
            db.add(state)
        for key in ('state', 'home_score', 'away_score', 'minute', 'extra_minute', 'provider_updated_at'):
            # Missing scores aren't evidence that a previously known score vanished.
            if key.endswith('_score') and (item.home_score is None or item.away_score is None):
                continue
            setattr(state, key, getattr(item, key))
        state.is_live = item.state in ('live', 'halftime', 'break')
        state.last_synced_at = now
        if item.events is not None:
            stored = {e.external_event_id: e for e in db.query(models.MatchEvent).filter_by(fixture_id=fixture.id, provider=PROVIDER)}
            seen = set()
            for values in item.events:
                key = values['external_event_id']
                seen.add(key)
                event = stored.get(key)
                if event is None:
                    event = models.MatchEvent(fixture_id=fixture.id, provider=PROVIDER, revisions=[], **values)
                    db.add(event)
                    stored[key] = event
                elif any(getattr(event, k) != v for k, v in values.items()):
                    event.revisions = list(event.revisions or []) + [dict(
                        at=now.isoformat(), **{k: getattr(event, k) for k in values})]
                    for k, v in values.items():
                        setattr(event, k, v)
                    event.updated_at = now
            for key, event in stored.items():
                if key not in seen and event.is_active:
                    event.revisions = list(event.revisions or []) + [dict(at=now.isoformat(), is_active=True, reason='withdrawn')]
                    event.is_active = False
                    event.updated_at = now
        count += 1
    db.flush()
    return count


def polling_window(db, models, now):
    """Only current, unfinished PL weeks; stale live rows cannot poll forever."""
    return db.query(models.Fixture).join(models.Week).join(models.Season).filter(
        models.Season.is_active == 1, models.Season.is_archived == 0,
        models.Season.api_competition_code == 'PL', models.Week.status != 'finalized',
        models.Fixture.kickoff_utc >= now - timedelta(hours=4),
        models.Fixture.kickoff_utc <= now + timedelta(minutes=20)).all()
