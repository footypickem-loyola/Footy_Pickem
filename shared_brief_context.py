"""Read-only canonical PL context; no player, pick, matchup or league identity."""
from contextlib import closing
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS

from pick_insight_enrichment import verified_mapping
from pre_match_brief import (utc, require, _keys, _id, serialize, build_fixture_facts,
    validate_fixture_facts, CorrespondentError, BriefNotReady)

CONTEXT_VERSION = 'shared-team-context-v1'
PROMPT_VERSION = 'shared-team-brief-v1'
PROVIDER = 'football-data'
COMPETITION = 'PL'


def identity(season_year, fixture_id, team_id):
    require(season_year == 2026 and _id(fixture_id) and team_id in verified_mapping(),
            'Unsupported canonical fixture/team mapping')
    return dict(provider=PROVIDER, competition=COMPETITION, season_year=season_year,
                external_fixture_id=fixture_id, team_id=team_id)


def validate_team_context(context):
    _keys(context, ('context_version', 'prompt_version', 'identity', 'matchweek', 'as_of',
                    'week_first_kickoff', 'candidate_limit', 'fixture'), 'shared context')
    require(context['context_version'] == CONTEXT_VERSION and context['prompt_version'] == PROMPT_VERSION,
            'Unsupported shared context version')
    key = context['identity']
    require(key == identity(key['season_year'], key['external_fixture_id'], key['team_id']), 'Invalid canonical identity')
    require(type(context['matchweek']) is int and 1 <= context['matchweek'] <= 38, 'Invalid matchweek')
    f = context['fixture']
    _keys(f, ('fixture_id', 'home', 'away', 'picked_team', 'opponent', 'kickoff', 'candidates', 'fallback_facts'), 'shared fixture')
    require(f['fixture_id'] == key['external_fixture_id'] and f['home'] != f['away'], 'Canonical fixture mismatch')
    clubs = verified_mapping()
    require(f['home'] in clubs.values() and f['away'] in clubs.values()
            and f['picked_team'] == clubs[key['team_id']] and f['picked_team'] in (f['home'], f['away']), 'Team mismatch')
    require(f['opponent'] == (f['away'] if f['picked_team'] == f['home'] else f['home']), 'Opponent mismatch')
    cutoff = utc(context['as_of'])
    require(cutoff < utc(context['week_first_kickoff']) <= utc(f['kickoff']), 'Shared context must precede whole matchweek')
    require(context['candidate_limit'] == 5, 'Shared candidate cap must be five')
    validate_fixture_facts(f, cutoff=cutoff, candidate_limit=5)
    serialize(context)
    return context


def check_current_fixture(database, slot):
    """Recheck official identity/kickoff around provider I/O; never infer a remap."""
    key = slot['identity']
    with closing(sqlite3.connect(Path(database).resolve(strict=True).as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('''SELECT f.home,f.away,f.kickoff_utc FROM fixtures f JOIN weeks w ON w.id=f.week_id
            JOIN seasons s ON s.id=w.season_id WHERE s.api_competition_code=? AND s.api_season_year=?
            AND f.external_match_id=?''', (key['competition'], key['season_year'], key['external_fixture_id'])).fetchall()
        require(rows and all((r[0], r[1], utc(r[2])) == (slot['home'], slot['away'], utc(slot['kickoff'])) for r in rows),
                'Official schedule changed during generation')


def build_round(database, *, season_year, matchweek, as_of):
    """Return exactly twenty canonical slots; fact-unavailable slots remain blocked.

    Duplicate league copies of official fixtures/results must agree. Game primary
    keys only join source rows; every engine fixture/week/season ID is canonical.
    """
    require(season_year == 2026 and type(matchweek) is int and 1 <= matchweek <= 38,
            'Only verified 2026/27 PL mapping is available')
    cutoff = utc(as_of)
    try:
        with closing(sqlite3.connect(Path(database).resolve(strict=True).as_uri()+'?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            rows = db.execute('''SELECT f.id AS local_id,f.external_match_id,f.home,f.away,f.kickoff_utc,
                 w.number FROM fixtures f JOIN weeks w ON w.id=f.week_id JOIN seasons s ON s.id=w.season_id
                 WHERE s.api_competition_code=? AND s.api_season_year=? ORDER BY f.external_match_id''',
                 (COMPETITION, season_year)).fetchall()
            require(bool(rows), 'No official PL season schedule')
            clubs = verified_mapping()
            ids = {name: key for key, name in clubs.items()}
            schedule, local_ids = {}, {}
            for r in rows:
                require(_id(r['external_match_id']) and r['home'] in ids and r['away'] in ids,
                        'Missing/unknown official fixture or club identity')
                f = NS(id=r['external_match_id'], week_id=r['number'], home=r['home'], away=r['away'],
                       kickoff_utc=utc(r['kickoff_utc']) if r['kickoff_utc'] else None)
                require(f.id not in schedule or vars(schedule[f.id]) == vars(f), 'Conflicting canonical fixture copies')
                schedule[f.id] = f
                local_ids[r['local_id']] = f.id
            targets = sorted((f for f in schedule.values() if f.week_id == matchweek), key=lambda f: f.id)
            require(len(targets) == 10 and len({t for f in targets for t in (f.home, f.away)}) == 20,
                    'A matchweek requires ten fixtures and twenty distinct clubs')
            require(all(f.kickoff_utc for f in targets), 'Unknown target kickoff')
            first = min(f.kickoff_utc for f in targets)
            require(cutoff < first, 'Generation cutoff must precede whole matchweek')
            results = {}
            # SQL excludes future result rows before they are inspected.
            for r in db.execute('''SELECT r.* FROM results r JOIN fixtures f ON f.id=r.fixture_id
                JOIN weeks w ON w.id=f.week_id JOIN seasons s ON s.id=w.season_id
                WHERE s.api_competition_code=? AND s.api_season_year=? AND w.number<>?
                AND julianday(f.kickoff_utc)<julianday(?) AND julianday(r.updated_at)<julianday(?)''',
                (COMPETITION, season_year, matchweek, cutoff.isoformat(), cutoff.isoformat())):
                fid = local_ids[r['fixture_id']]
                result = NS(fixture_id=fid, outcome=r['outcome'], home_score=r['home_score'], away_score=r['away_score'],
                            source=r['source'], updated_at=utc(r['updated_at']))
                old = results.get(fid)
                require(old is None or (old.outcome, old.home_score, old.away_score) ==
                        (result.outcome, result.home_score, result.away_score), 'Conflicting official result copies')
                # Latest eligible update wins deterministically; no local-copy identity enters context.
                if old is None or (result.updated_at, str(result.source)) > (old.updated_at, str(old.source)):
                    results[fid] = result
            weeks = [NS(id=n, season_id=season_year) for n in sorted({f.week_id for f in schedule.values()})]
            output = []
            for f in targets:
                for team in (f.home, f.away):
                    key = identity(season_year, f.id, ids[team])
                    context, error = None, None
                    try:
                        facts = build_fixture_facts(db, f=f, team=team, schedule=list(schedule.values()),
                            results=list(results.values()), weeks=weeks, season=season_year, cutoff=cutoff, recent_form=True)
                        context = validate_team_context(dict(context_version=CONTEXT_VERSION, prompt_version=PROMPT_VERSION,
                            identity=key, matchweek=matchweek, as_of=cutoff.isoformat(), week_first_kickoff=first.isoformat(),
                            candidate_limit=5, fixture=dict(fixture_id=f.id, home=f.home, away=f.away, picked_team=team,
                            opponent=f.away if team == f.home else f.home, kickoff=f.kickoff_utc.isoformat(), **facts)))
                    except BriefNotReady:
                        error = 'football_context_unavailable'
                    output.append(dict(identity=key, kickoff=f.kickoff_utc.isoformat(), home=f.home, away=f.away,
                                       team=team, context=context, error=error))
            return output
    except (OSError, sqlite3.Error) as exc:
        raise CorrespondentError('Unable to read canonical PL source') from exc
