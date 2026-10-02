"""Central, explicitly invoked season synchronization; never imported by views."""
from datetime import datetime, timezone
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

from pick_insight_enrichment import (LEAGUE_ID, SEASON_ID, crest_url, player_name,
                                    supported_season, validate_mapping, verified_mapping)


class SeasonSyncError(ValueError):
    """Safe errors containing no credentials, provider bodies, or request URLs."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SeasonClient:
    def __init__(self, token=None, opener=None):
        self.token = token if token is not None else os.environ.get('SPORTMONKS_API_TOKEN', '')
        self.opener = opener or build_opener(NoRedirect()).open

    def pages(self, resource, **params):
        if not self.token:
            raise SeasonSyncError('Sportmonks token is not configured')
        result, seen = [], set()
        for page in range(1, 101):
            query = urlencode(dict(params, page=page, per_page=50))
            request = Request(f'https://api.sportmonks.com/v3/football/{resource}/seasons/{SEASON_ID}?{query}',
                              headers={'Authorization': self.token, 'Accept': 'application/json'})
            try:
                with self.opener(request, timeout=15) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError()
                payload = json.loads(raw)
                rows, pagination = payload['data'], payload['pagination']
                if (not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows)
                        or type(pagination.get('has_more')) is not bool
                        or type(pagination.get('current_page')) is not int
                        or pagination['current_page'] != page):
                    raise ValueError()
                fingerprint = json.dumps(rows, sort_keys=True)
                if fingerprint in seen or (pagination['has_more'] and not rows):
                    raise ValueError()
                seen.add(fingerprint)
                result.extend(rows)
                if not pagination['has_more']:
                    return result
            except HTTPError as error:
                raise SeasonSyncError(f'Sportmonks season HTTP {error.code}') from None
            except Exception:
                raise SeasonSyncError('Incomplete or unavailable Sportmonks season response') from None
        raise SeasonSyncError('Sportmonks pagination limit exceeded')

    def participants(self):
        return self.pages('teams')

    def scorers(self):
        return self.pages('topscorers', include='player;participant;type',
                          filters='seasonTopscorerTypes:208')


def select_scorers(rows, mapping):
    candidates = {team_id: {} for team_id in mapping}
    invalid = set()
    for row in rows:
        team_id = row.get('participant_id')
        if type(team_id) is not int or team_id not in mapping:
            continue  # Unknown IDs never match by name, including included names.
        if row.get('season_id') != SEASON_ID or row.get('type_id') != 208:
            continue
        player = row.get('player') or {}
        participant = row.get('participant') or {}
        player_id, total = row.get('player_id'), row.get('total')
        if not isinstance(player, dict) or not isinstance(participant, dict):
            invalid.add(team_id)
            continue
        name = next((player.get(k).strip() for k in ('display_name', 'common_name', 'name')
                     if player_name(player.get(k))), None)
        if (type(player_id) is not int or player_id <= 0 or player.get('id') != player_id
                or participant.get('id') != team_id or type(total) is not int or total < 0 or not name):
            invalid.add(team_id)
            continue
        previous = candidates[team_id].get(player_id)
        value = (total, name)
        if previous is not None and previous != value:
            invalid.add(team_id)  # Ambiguous/stage duplicates must not be summed.
        candidates[team_id][player_id] = value
    result = {}
    for team_id, players in candidates.items():
        goals = max((v[0] for v in players.values()), default=0)
        result[team_id] = (None, []) if team_id in invalid or not goals else (
            goals, [dict(player_id=pid, name=value[1]) for pid, value in sorted(players.items())
                    if value[0] == goals])
    return result


def sync_season(db, models, season, client, now=None):
    if not supported_season(season) or not season.is_active or season.is_archived:
        raise SeasonSyncError('Only the active 2026/2027 PL season is supported')
    mapping = verified_mapping()
    fixtures = db.query(models.Fixture).join(models.Week).filter(models.Week.season_id == season.id).all()
    clubs = {name for f in fixtures for name in (f.home, f.away)}
    participants = client.participants()
    validate_mapping(mapping, clubs, participants)
    # Fetch every page and validate before replacing anything. A failure retains
    # the prior timestamp, so outages cannot make an old snapshot appear fresh.
    scorers = select_scorers(client.scorers(), mapping)
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo:
        timestamp = timestamp.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        for participant in participants:
            team_id = participant['id']
            row = db.get(models.ClubSeasonEnrichment, (season.id, team_id))
            if row is None:
                row = models.ClubSeasonEnrichment(season_id=season.id, sportmonks_team_id=team_id)
                db.add(row)
            row.sportmonks_season_id, row.league_id = SEASON_ID, LEAGUE_ID
            row.footy_club = mapping[team_id]
            row.crest_url = crest_url(participant.get('image_path'), team_id)
            row.goals, row.top_scorers = scorers[team_id]
            row.synced_at = timestamp
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {'clubs': len(participants), 'clubs_with_scorers': sum(bool(v[1]) for v in scorers.values())}
