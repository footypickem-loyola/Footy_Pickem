"""Normalized live snapshot wire format and HTTP transport; no app or DB access."""
from dataclasses import asdict, fields
from datetime import datetime, timezone
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from sportmonks_live import LiveFixture, STATES, EVENT_TYPES

MAX_SNAPSHOT_BYTES = 1024 * 1024
MAX_FIXTURES = 100
MAX_EVENTS = 500
PROVIDER = 'sportmonks'
LEAGUE_ID = 8  # This bridge accepts Premier League snapshots only.
EVENT_FIELDS = {
    'external_event_id', 'event_type', 'minute', 'extra_minute', 'team_provider_id',
    'team_name', 'side', 'player_provider_id', 'player_name', 'related_player_provider_id',
    'related_player_name', 'detail', 'running_score', 'sort_order', 'provider_updated_at', 'is_active',
}
FIXTURE_FIELDS = {field.name for field in fields(LiveFixture)}


class SnapshotError(ValueError):
    """Invalid normalized snapshot; errors never contain supplied values."""


def _string(value, *, nullable=False, empty=False, limit=256):
    if value is None and nullable:
        return
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise SnapshotError('Invalid snapshot string')
    if any(ord(c) < 32 for c in value):
        raise SnapshotError('Invalid snapshot string')


def _integer(value, *, nullable=False, maximum=1000):
    if value is None and nullable:
        return
    if type(value) is not int or not 0 <= value <= maximum:
        raise SnapshotError('Invalid snapshot integer')


def _timestamp(value, *, nullable=False):
    if value is None and nullable:
        return None
    _string(value, limit=64)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if 'T' not in value and ' ' not in value:
            raise ValueError()
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except (ValueError, OverflowError):
        raise SnapshotError('Invalid snapshot timestamp') from None


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != expected:
        raise SnapshotError('Unexpected or missing snapshot fields')


def validate_snapshot(snapshot):
    """Validate the entire snapshot before callers can open a DB transaction."""
    _keys(snapshot, {'provider', 'league_id', 'fixtures'})
    if snapshot['provider'] != PROVIDER or type(snapshot['league_id']) is not int or snapshot['league_id'] != LEAGUE_ID:
        raise SnapshotError('Unsupported live provider or league')
    fixtures = snapshot['fixtures']
    if not isinstance(fixtures, list) or len(fixtures) > MAX_FIXTURES:
        raise SnapshotError('Invalid fixture collection')
    result, fixture_ids = [], set()
    for fixture in fixtures:
        _keys(fixture, FIXTURE_FIELDS)
        for key in ('external_id', 'home', 'away', 'state'):
            _string(fixture[key])
        if type(fixture['league_id']) is not int or fixture['league_id'] != LEAGUE_ID:
            raise SnapshotError('Unsupported fixture league')
        if fixture['state'] not in set(STATES.values()) | {'unknown'}:
            raise SnapshotError('Unsupported fixture state')
        if fixture['external_id'] in fixture_ids:
            raise SnapshotError('Duplicate fixture identity')
        fixture_ids.add(fixture['external_id'])
        for key in ('home_score', 'away_score', 'minute', 'extra_minute'):
            _integer(fixture[key], nullable=True)
        kickoff = _timestamp(fixture['kickoff'])
        _timestamp(fixture['provider_updated_at'], nullable=True)
        events = fixture['events']
        if events is not None:
            if not isinstance(events, list) or len(events) > MAX_EVENTS:
                raise SnapshotError('Invalid event collection')
            event_ids = set()
            for event in events:
                _keys(event, EVENT_FIELDS)
                _string(event['external_event_id'])
                _string(event['event_type'])
                if event['event_type'] not in set(EVENT_TYPES.values()):
                    raise SnapshotError('Unsupported event type')
                if event['external_event_id'] in event_ids:
                    raise SnapshotError('Duplicate event identity')
                event_ids.add(event['external_event_id'])
                for key in ('team_provider_id', 'team_name', 'player_provider_id', 'player_name',
                            'related_player_provider_id', 'related_player_name'):
                    _string(event[key], nullable=True)
                _string(event['detail'], nullable=True, empty=True, limit=2048)
                _string(event['running_score'], nullable=True, limit=32)
                if event['running_score'] is not None and not re.fullmatch(r'\d{1,3}\s*[-:]\s*\d{1,3}', event['running_score']):
                    raise SnapshotError('Invalid running score')
                if event['side'] not in (None, 'home', 'away') or type(event['is_active']) is not bool:
                    raise SnapshotError('Invalid event side or active flag')
                for key in ('minute', 'extra_minute'):
                    _integer(event[key], nullable=True)
                _integer(event['sort_order'], maximum=100000)
                _timestamp(event['provider_updated_at'], nullable=True)
        result.append(LiveFixture(**dict(fixture, kickoff=kickoff)))
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError('Duplicate snapshot field')
        result[key] = value
    return result


def decode_snapshot(body):
    if len(body) > MAX_SNAPSHOT_BYTES:
        raise SnapshotError('Snapshot too large')
    try:
        snapshot = json.loads(body, object_pairs_hook=_unique_object)
        return validate_snapshot(snapshot)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise SnapshotError('Invalid normalized live snapshot') from None


def encode_snapshot(fixtures, league_id):
    rows = []
    for fixture in fixtures:
        row = asdict(fixture)
        row['kickoff'] = fixture.kickoff.isoformat()
        rows.append(row)
    snapshot = {'provider': PROVIDER, 'league_id': league_id, 'fixtures': rows}
    validate_snapshot(snapshot)
    body = json.dumps(snapshot, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(body) > MAX_SNAPSHOT_BYTES:
        raise SnapshotError('Snapshot too large')
    return body


class BridgeError(Exception):
    def __init__(self, message='Live ingest unavailable', retry_after=60):
        super().__init__(message)
        self.retry_after = max(60, min(int(retry_after), 3600))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the shared secret to a redirected host.


class LiveIngestClient:
    def __init__(self, url=None, secret=None, opener=None):
        self.url = url if url is not None else os.environ.get('LIVE_INGEST_URL', '')
        self.secret = secret if secret is not None else os.environ.get('LIVE_INGEST_SECRET', '')
        try:
            parsed = urlsplit(self.url)
            valid = parsed.scheme in ('http', 'https') and parsed.hostname and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
        except ValueError:
            valid = False
        if not valid or not self.secret.strip():
            raise BridgeError('LIVE_INGEST_URL and LIVE_INGEST_SECRET must be configured')
        self.opener = opener or build_opener(_NoRedirect()).open

    def send(self, fixtures, league_id):
        try:
            body = encode_snapshot(fixtures, league_id)
            request = Request(self.url, data=body, method='POST', headers={
                'X-Live-Ingest-Secret': self.secret, 'Content-Type': 'application/json', 'Accept': 'application/json',
            })
            with self.opener(request, timeout=10) as response:
                if response.status != 200:
                    raise BridgeError('Live ingest rejected snapshot')
                ack = json.loads(response.read(4097))
            if not isinstance(ack, dict) or ack.get('ok') is not True or type(ack.get('stored')) is not int or not 0 <= ack['stored'] <= len(fixtures):
                raise BridgeError('Invalid live ingest acknowledgement')
            return ack['stored']
        except HTTPError as error:
            retry = error.headers.get('Retry-After', '60') if error.headers else '60'
            raise BridgeError(f'Live ingest HTTP {error.code}', int(retry) if retry.isdigit() and len(retry) <= 6 else 60) from None
        except BridgeError:
            raise
        except Exception:
            raise BridgeError('Invalid or unavailable live ingest response') from None
