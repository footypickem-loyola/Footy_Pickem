"""Local-only season cache for Pick Insight. No provider client on this path."""
from datetime import timedelta, timezone
import json
from pathlib import Path
import re

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, JSON

LEAGUE_ID = 8
SEASON_ID = 28083
# Weekly refreshes must survive ordinary gaps and international breaks.
MAX_AGE = timedelta(days=35)
MAPPING_PATH = Path(__file__).parent / 'data' / 'sportmonks_2026_27_mapping.json'


def verified_mapping():
    rows = json.loads(MAPPING_PATH.read_text(encoding='utf-8'))['teams']
    ids = [r['sportmonks_team_id'] for r in rows]
    names = [r['footy_club'] for r in rows]
    if (len(rows) != 20 or len(set(ids)) != 20 or len(set(names)) != 20
            or any(type(i) is not int or i <= 0 for i in ids)
            or any(not isinstance(n, str) or not n.strip() for n in names)):
        raise ValueError('Incomplete or colliding season mapping')
    return dict(zip(ids, names))


def validate_mapping(mapping, stored_clubs, participants):
    ids = [p.get('id') for p in participants]
    if (len(mapping) != 20 or len(set(mapping.values())) != 20
            or set(mapping.values()) != set(stored_clubs)
            or len(ids) != 20 or any(type(i) is not int for i in ids)
            or len(set(ids)) != 20 or set(ids) != set(mapping)):
        raise ValueError('Season mapping is incomplete or has collisions')


def crest_url(value, team_id):
    # Only credential-free static team images from the documented CDN.
    if isinstance(value, str) and re.fullmatch(
            rf'https://cdn\.sportmonks\.com/images/soccer/teams/\d+/{team_id}\.png', value):
        return value
    return None


def player_name(value):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 160
            and not any(ord(c) < 32 for c in value))


def register_models(Base):
    class ClubSeasonEnrichment(Base):
        __tablename__ = 'club_season_enrichment'
        season_id = Column(Integer, ForeignKey('seasons.id'), primary_key=True)
        sportmonks_team_id = Column(Integer, primary_key=True)
        sportmonks_season_id = Column(Integer, nullable=False)
        league_id = Column(Integer, nullable=False)
        footy_club = Column(String, nullable=False)
        crest_url = Column(String)
        # All tied leaders, never reconstructed event totals.
        top_scorers = Column(JSON, nullable=False, default=list)
        goals = Column(Integer)
        synced_at = Column(DateTime, nullable=False)
        __table_args__ = (UniqueConstraint('season_id', 'footy_club'),)
    return ClubSeasonEnrichment


def supported_season(season):
    return (season is not None and season.code == 'year-2'
            and season.api_competition_code == 'PL' and season.api_season_year == 2026)


def cached_enrichment(db, models, season, team, now):
    fallback = dict(crest=None, top_scorer=None)
    if not supported_season(season):
        return fallback
    try:
        mapping = verified_mapping()
    except (OSError, ValueError, KeyError, TypeError):
        return fallback
    team_id = next((key for key, value in mapping.items() if value == team), None)
    if team_id is None:
        return fallback
    row = db.get(models.ClubSeasonEnrichment, (season.id, team_id))
    if (row is None or row.footy_club != team or row.league_id != LEAGUE_ID
            or row.sportmonks_season_id != SEASON_ID or row.synced_at is None):
        return fallback
    def utc(value):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if not timedelta(0) <= utc(now) - utc(row.synced_at) <= MAX_AGE:
        return fallback
    fallback['crest'] = crest_url(row.crest_url, team_id)
    scorers = row.top_scorers
    if (type(row.goals) is int and row.goals > 0 and isinstance(scorers, list) and scorers
            and all(isinstance(p, dict) and type(p.get('player_id')) is int
                    and p['player_id'] > 0 and player_name(p.get('name')) for p in scorers)
            and len({p['player_id'] for p in scorers}) == len(scorers)):
        names = ' / '.join(p['name'] for p in sorted(scorers, key=lambda p: p['player_id']))
        fallback['top_scorer'] = f"{names} · {row.goals} {'goal' if row.goals == 1 else 'goals'}"
        if len(scorers) > 1:
            fallback['top_scorer'] += ' each'
    return fallback
