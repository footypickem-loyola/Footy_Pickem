"""Sportmonks v3 boundary. No Flask, database, scoring or secret-bearing errors."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

EVENT_TYPES = {14: 'goal', 15: 'own_goal', 16: 'penalty', 18: 'substitution',
               19: 'yellow_card', 20: 'red_card', 21: 'second_yellow', 10: 'var', 1697: 'var'}
STATES = {1: 'not_started', 2: 'live', 3: 'halftime', 4: 'break', 5: 'finished',
          6: 'live', 7: 'finished', 8: 'finished', 9: 'live', 10: 'postponed',
          11: 'suspended', 12: 'cancelled', 13: 'not_started', 14: 'awarded',
          15: 'abandoned', 16: 'delayed', 17: 'awarded', 18: 'interrupted',
          19: 'awaiting_updates', 20: 'deleted', 21: 'break', 22: 'live',
          25: 'break', 26: 'pending'}


class ProviderError(Exception):
    def __init__(self, message='Live provider unavailable', retry_after=60):
        super().__init__(message)
        self.retry_after = max(60, min(int(retry_after), 3600))


@dataclass
class LiveFixture:
    external_id: str
    league_id: int
    home: str
    away: str
    kickoff: datetime
    state: str
    home_score: int | None
    away_score: int | None
    minute: int | None
    extra_minute: int | None
    provider_updated_at: str | None
    events: list | None  # None means omitted/unknown, [] is a complete empty snapshot.


def normalize_fixture(raw):
    participants = {p['meta']['location']: p for p in raw.get('participants', [])}
    if set(participants) != {'home', 'away'}:
        raise ValueError('Missing fixture participants')
    score = {s['score']['participant']: s['score']['goals'] for s in raw.get('scores', [])
             if s.get('description') == 'CURRENT' and s.get('score', {}).get('participant') in ('home', 'away')}
    if any(type(value) is not int or value < 0 for value in score.values()):
        raise ValueError('Invalid score')
    periods = sorted(raw.get('periods') or [], key=lambda p: p.get('sort_order') or 0)
    period = periods[-1] if periods else {}
    minute = period.get('minutes')
    boundary = (period.get('counts_from') or 0) + (period.get('period_length') or 45)
    extra = max(0, minute - boundary) if minute is not None else None
    if extra:
        minute = boundary
    teams = {str(p['id']): (side, p['name']) for side, p in participants.items()}
    events = None
    if isinstance(raw.get('events'), list):
        events = []
        for event in raw['events']:
            kind = EVENT_TYPES.get(event.get('type_id'))
            if not kind:
                continue
            # No-ID identity is stable for identical snapshots. Corrections create a
            # replacement and deactivate the old key on complete reconciliation.
            identity = event.get('id')
            if identity is None:
                fields = [event.get(k) for k in ('type_id', 'participant_id', 'player_id',
                          'player_name', 'minute', 'extra_minute', 'sort_order')]
                identity = 'fallback:' + hashlib.sha256(json.dumps(fields).encode()).hexdigest()
            side, name = teams.get(str(event.get('participant_id')), (None, None))
            events.append(dict(external_event_id=str(identity), event_type=kind,
                minute=event.get('minute'), extra_minute=event.get('extra_minute'),
                team_provider_id=str(event['participant_id']) if event.get('participant_id') else None,
                team_name=name, side=side, player_provider_id=str(event['player_id']) if event.get('player_id') else None,
                player_name=event.get('player_name'), related_player_provider_id=str(event['related_player_id']) if event.get('related_player_id') else None,
                related_player_name=event.get('related_player_name'),
                detail=str(event.get('sub_type_id') or event.get('info') or ''),
                running_score=event.get('result'), sort_order=event.get('sort_order') or 0,
                provider_updated_at=event.get('updated_at'), is_active=not bool(event.get('rescinded'))))
    kickoff = datetime.fromisoformat(raw['starting_at'].replace('Z', '+00:00'))
    if kickoff.tzinfo is not None:
        kickoff = kickoff.astimezone(timezone.utc).replace(tzinfo=None)
    return LiveFixture(str(raw['id']), int(raw['league_id']), participants['home']['name'],
        participants['away']['name'], kickoff,
        STATES.get(raw.get('state_id'), 'unknown'), score.get('home'), score.get('away'),
        minute, extra, raw.get('updated_at'), events)


class SportmonksClient:
    def __init__(self, token=None, league_id=None, opener=urlopen):
        self.token = token if token is not None else os.environ.get('SPORTMONKS_API_TOKEN', '')
        self.league_id = int(league_id or os.environ.get('SPORTMONKS_LEAGUE_ID', '8'))
        self.opener = opener
        self.rate_limit = None

    def livescores(self):
        if not self.token:
            raise ProviderError('Live provider token is not configured', 300)
        query = urlencode({'include': 'participants;scores;state;periods;events',
                           'filters': f'fixtureLeagues:{self.league_id}'})
        request = Request('https://api.sportmonks.com/v3/football/livescores?' + query,
                          headers={'Authorization': self.token, 'Accept': 'application/json'})
        try:
            with self.opener(request, timeout=10) as response:
                payload = json.loads(response.read())
            if not isinstance(payload.get('data'), list) or payload.get('pagination', {}).get('has_more'):
                raise ProviderError('Incomplete live provider response')
            self.rate_limit = payload.get('rate_limit')
            return [normalize_fixture(raw) for raw in payload['data'] if raw.get('league_id') == self.league_id]
        except HTTPError as error:
            retry = error.headers.get('Retry-After', '60') if error.headers else '60'
            raise ProviderError(f'Live provider HTTP {error.code}', int(retry) if retry.isdigit() else 60) from None
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('Invalid or unavailable live provider response') from None
