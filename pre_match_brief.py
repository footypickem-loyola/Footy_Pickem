"""Deterministic, read-only context for a committed five-pick pre-match slate.

No Flask import, database initialization, preference queues, network or clock.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from correspondent.writer import CorrespondentError
from fixture_history import load_fixture_history
from fixture_intelligence import build_fixture_intelligence, VERSION as FIXTURE_VERSION
from historical_fixture_intelligence import VERSION as HISTORICAL_VERSION
from pre_match_facts import writing_metadata, fallback_facts

CONTEXT_VERSION = 'pre-match-context-v2'
PROMPT_VERSION = 'pre-match-brief-v2'
CANDIDATE_KEYS = {'id', 'signal_type', 'family', 'subject_team', 'claim', 'editorial_score',
                  'scope', 'evidence', 'sample', 'provenance', 'confidence', 'recomputability', 'writing'}


def all_facts(pick):
    return pick['candidates'] + pick['fallback_facts']


def validate_writing(fact):
    writing = fact.get('writing')
    _keys(writing, ('role', 'scorers', 'comparison_scope', 'specific_goal_story', 'scoring_meetings', 'instruction'), 'writing metadata')
    require(writing['role'] in ('editorial', 'fallback') and type(writing['specific_goal_story']) is bool, 'Invalid fact role')
    require(writing['comparison_scope'] is None or isinstance(writing['comparison_scope'], dict), 'Invalid comparison scope')
    require(isinstance(writing['scorers'], list), 'Invalid scorer list')
    for scorer in writing['scorers']:
        require(isinstance(scorer, dict) and _id(scorer.get('player_id')) and _text(scorer.get('name'))
                and isinstance(scorer.get('event_ids'), list) and scorer['event_ids'], 'Missing authoritative scorer identity')
    require(not writing['specific_goal_story'] or writing['scorers'], 'Anonymous goal story is ineligible')


class BriefNotReady(CorrespondentError):
    """No complete committed pre-kickoff slate is available at this cutoff."""


def require(condition, message):
    if not condition:
        raise CorrespondentError(message)


def utc(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(isinstance(value, datetime), 'Known ISO timestamps are required')
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError) as exc:
        raise CorrespondentError('Invalid ISO timestamp') from exc


def serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _id(value):
    return type(value) is int and value > 0


def _keys(value, keys, label):
    require(isinstance(value, dict) and set(value) == set(keys), f'Malformed {label} fields')


def validate_fixture_facts(pick, *, cutoff, candidate_limit, seen=None):
    """Shared PR25 fact contract, independent of slate/player metadata."""
    candidate_ids = seen if seen is not None else set()
    candidates = pick['candidates']
    require(isinstance(candidates, list) and len(candidates) <= candidate_limit, 'Invalid candidate list/cap')
    for c in candidates:
        _keys(c, CANDIDATE_KEYS, 'candidate')
        validate_writing(c)
        require(c['writing']['role'] == 'editorial', 'Candidate role mismatch')
        require(all(_text(c[k]) for k in ('id', 'signal_type', 'family', 'subject_team', 'claim', 'scope')), 'Missing candidate fields')
        require(c['id'].startswith(f"{pick['fixture_id']}:") and c['id'] not in candidate_ids, 'Duplicate/cross-fixture candidate')
        candidate_ids.add(c['id'])
        require(c['subject_team'] in (pick['home'], pick['away']), 'Candidate subject outside fixture')
        require(type(c['editorial_score']) is int and c['editorial_score'] >= 60, 'Candidate below retention threshold')
        require(all(isinstance(c[k], dict) for k in ('evidence', 'sample', 'provenance', 'confidence', 'recomputability')),
                'Invalid structured candidate evidence')
        sample = c['sample']
        require(type(sample.get('size')) is int and sample['size'] > 0 and
                isinstance(sample.get('fixture_ids'), list) and sample['fixture_ids'] and
                all(_id(i) for i in sample['fixture_ids']) and _text(sample.get('window')), 'Invalid sample')
        require(utc(sample.get('start')) <= utc(sample.get('end')) < cutoff, 'Candidate evidence outside cutoff')
        require(utc(c['recomputability'].get('cutoff')) == cutoff, 'Candidate cutoff mismatch')
        require(c['recomputability'].get('engine_version') in (FIXTURE_VERSION, HISTORICAL_VERSION), 'Invalid candidate version')
        provenance = c['provenance']
        require(_text(provenance.get('source')), 'Missing candidate provenance')
        rows = provenance.get('fixtures', provenance.get('rows'))
        require(isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows), 'Missing evidence rows')
        require(all(utc(r.get('kickoff')) < cutoff for r in rows), 'Future provenance fixture')
    fallback = pick['fallback_facts']
    require(isinstance(fallback, list) and len(fallback) <= 2, 'Invalid fallback list')
    for fact in fallback:
        _keys(fact, ('id', 'signal_type', 'claim', 'evidence', 'provenance', 'writing', 'cutoff'), 'fallback fact')
        require(_text(fact['id']) and fact['id'].startswith(f"{pick['fixture_id']}:fallback:")
                and fact['id'] not in candidate_ids, 'Duplicate/cross-fixture fallback fact')
        candidate_ids.add(fact['id'])
        require(_text(fact['signal_type']) and _text(fact['claim']) and isinstance(fact['evidence'], dict), 'Invalid fallback evidence')
        require(utc(fact['cutoff']) == cutoff and isinstance(fact['provenance'], dict), 'Fallback cutoff mismatch')
        rows = fact['provenance'].get('fixtures')
        require(isinstance(rows, list) and rows and all(isinstance(r, dict) and utc(r.get('kickoff')) < cutoff for r in rows), 'Future/missing fallback evidence')
        validate_writing(fact)
        require(fact['writing']['role'] == 'fallback', 'Fallback role mismatch')
    require(bool(all_facts(pick)), 'No supported football context for fixture')


def validate_context(context):
    """Reject malformed caller input before credentials or any provider call.

    This checks the context contract, not whether a caller forged football facts.
    Production callers must obtain this input from the authoritative builder.
    """
    _keys(context, ('context_version', 'prompt_version', 'season', 'week', 'player', 'as_of',
        'earliest_kickoff', 'week_first_kickoff', 'candidate_limit', 'versions', 'slate_status',
        'availability', 'picks'), 'context')
    require(context['context_version'] == CONTEXT_VERSION and context['prompt_version'] == PROMPT_VERSION,
            'Unsupported context/prompt version')
    require(context['slate_status'] == 'committed', 'Slate is not committed')
    for key, fields in (('season', ('id', 'code', 'name')), ('week', ('id', 'number')), ('player', ('id', 'name'))):
        _keys(context[key], fields, key)
        require(_id(context[key]['id']), f'Invalid {key} ID')
    require(_id(context['week']['number']), 'Invalid week number')
    require(all(_text(context[k][f]) for k, f in (('season', 'code'), ('season', 'name'), ('player', 'name'))),
            'Missing season/player identity')
    _keys(context['versions'], ('fixture_intelligence', 'historical_intelligence'), 'versions')
    require(context['versions'] == dict(fixture_intelligence=FIXTURE_VERSION, historical_intelligence=HISTORICAL_VERSION),
            'Unsupported intelligence versions; rebuild the context')
    require(isinstance(context['availability'], list) and all(_text(v) for v in context['availability']), 'Invalid availability notes')
    require(type(context['candidate_limit']) is int and 1 <= context['candidate_limit'] <= 5, 'Candidate cap must be 1–5')
    picks = context['picks']
    require(isinstance(picks, list) and len(picks) == 5, 'Exactly five committed picks required')
    cutoff = utc(context['as_of'])
    require(cutoff < utc(context['week_first_kickoff']) <= utc(context['earliest_kickoff']), 'As-of must precede the whole matchweek')
    fixture_ids, pick_ids, candidate_ids, orders, ordering = set(), set(), set(), set(), []
    for pick in picks:
        _keys(pick, ('pick_id', 'committed_at', 'pick_order', 'fixture_id', 'home', 'away', 'kickoff',
            'picked_team', 'opponent', 'picked_team_venue', 'packet_version', 'candidates', 'fallback_facts'), 'pick')
        require(_id(pick['fixture_id']) and pick['fixture_id'] not in fixture_ids, 'Duplicate/invalid fixture')
        require(_id(pick['pick_id']) and pick['pick_id'] not in pick_ids, 'Duplicate/invalid pick')
        fixture_ids.add(pick['fixture_id'])
        pick_ids.add(pick['pick_id'])
        require(type(pick['pick_order']) is int and pick['pick_order'] in range(1, 6), 'Invalid pick order')
        orders.add(pick['pick_order'])
        require(utc(pick['committed_at']) <= cutoff < utc(pick['kickoff']), 'Pick not committed before cutoff/kickoff')
        require(all(_text(pick[k]) for k in ('home', 'away', 'picked_team', 'opponent')), 'Missing fixture identity')
        require(pick['home'] != pick['away'] and pick['picked_team'] in (pick['home'], pick['away']), 'Invalid picked team')
        home = pick['picked_team'] == pick['home']
        require(pick['opponent'] == pick['away' if home else 'home'] and pick['picked_team_venue'] == ('home' if home else 'away'),
                'Invalid opponent/venue orientation')
        require(pick['packet_version'] == FIXTURE_VERSION, 'Invalid fixture packet version')
        ordering.append((utc(pick['kickoff']), pick['fixture_id']))
        validate_fixture_facts(pick, cutoff=cutoff, candidate_limit=context['candidate_limit'], seen=candidate_ids)
    require(orders == set(range(1, 6)), 'Duplicate pick order')
    require(ordering == sorted(ordering), 'Fixtures must be ordered by kickoff then ID')
    require(utc(context['earliest_kickoff']) == ordering[0][0], 'Earliest kickoff mismatch')
    try:
        serialize(context)
    except (TypeError, ValueError) as exc:
        raise CorrespondentError('Context must contain finite JSON data') from exc
    return context


def build_fixture_facts(db, *, f, team, schedule, results, weeks, season, cutoff, candidate_limit=5, recent_form=False):
    """Project unchanged ranked intelligence and writer-only fallback facts."""
    history = load_fixture_history(db, fixture=f, as_of=cutoff)
    packet = build_fixture_intelligence(fixture=f, fixtures=schedule, results=results, weeks=weeks,
        season_id=season, as_of=cutoff, reference_history=history)
    candidates = []
    for c in packet['ranked_candidates'][:candidate_limit]:
        writing = writing_metadata(c)
        if writing is None:
            continue
        candidate = {key: c[key] for key in CANDIDATE_KEYS - {'writing'}}
        candidate['writing'] = writing
        # Keep factual rows and event IDs, not provider annotation blobs.
        candidate['provenance'] = {k: v for k, v in c['provenance'].items()
                                   if k in ('source', 'fixtures', 'rows', 'reference_event_ids', 'external_event_ids')}
        candidates.append(candidate)
    fallback = fallback_facts(db, fixture=f, picked_team=team, history=history, candidates=candidates,
                              schedule=schedule, results=results, cutoff=cutoff, recent_form=recent_form)
    if not candidates and not fallback:
        raise BriefNotReady(f'No supported football context for fixture {f.id}; cannot generate an empty entry')
    return dict(candidates=candidates, fallback_facts=fallback)


def build_pre_match_context(database, *, season, week, player_id, as_of, candidate_limit=5):
    """Read one consistent SQLite snapshot; persisted picks alone mean committed.

    Bulk confirmation locks a preference queue, not five allocated fixtures.
    Never inspect that queue or another player's picks to construct this brief.
    """
    require(all(_id(v) for v in (season, week, player_id)), 'Positive season/week/player IDs required')
    require(type(candidate_limit) is int and 1 <= candidate_limit <= 5, 'Candidate cap must be 1–5')
    cutoff = utc(as_of)
    try:
        path = Path(database).resolve(strict=True)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            db.row_factory = sqlite3.Row

            def rows(sql, params=()):
                return [dict(r) for r in db.execute(sql, params)]

            def one(sql, params, label):
                found = rows(sql, params)
                require(len(found) == 1, f'Unknown or ambiguous {label}')
                return found[0]

            season_row = one('SELECT id,code,name FROM seasons WHERE id=?', (season,), 'season')
            week_row = one('SELECT id,number FROM weeks WHERE season_id=? AND number=?', (season, week), 'week')
            player = one('SELECT id,name FROM players WHERE id=?', (player_id,), 'player')
            matchup = one('SELECT id FROM matchups WHERE week_id=? AND (player_a_id=? OR player_b_id=?)',
                          (week_row['id'], player_id, player_id), 'player matchup')
            picks = rows('SELECT id,fixture_id,team,created_at FROM picks WHERE matchup_id=? AND player_id=? ORDER BY id',
                         (matchup['id'], player_id))
            if len(picks) != 5 or any(utc(p['created_at']) > cutoff for p in picks):
                raise BriefNotReady('Exactly five persisted picks committed by as-of are required; preference queues do not count')
            weeks = [SimpleNamespace(**r) for r in rows('SELECT id,season_id FROM weeks WHERE season_id=? ORDER BY id', (season,))]
            schedule = [SimpleNamespace(**{**r, 'kickoff_utc': utc(r['kickoff_utc']) if r['kickoff_utc'] else None})
                        for r in rows('SELECT f.id,f.week_id,f.home,f.away,f.kickoff_utc FROM fixtures f JOIN weeks w ON w.id=f.week_id WHERE w.season_id=? ORDER BY f.id', (season,))]
            require(len({f.id for f in schedule}) == len(schedule), 'Duplicate schedule fixture')
            whole_week = [f for f in schedule if f.week_id == week_row['id']]
            require(whole_week and all(f.kickoff_utc is not None for f in whole_week), 'Unknown week kickoffs')
            first = min(f.kickoff_utc for f in whole_week)
            if cutoff >= first:
                raise BriefNotReady('As-of must precede the first kickoff of the whole matchweek')
            by_id = {f.id: f for f in whole_week}
            require(len({p['fixture_id'] for p in picks}) == 5 and all(p['fixture_id'] in by_id for p in picks),
                    'Duplicate pick or fixture outside selected week')
            pick_order = {p['id']: i for i, p in enumerate(sorted(picks, key=lambda p: (utc(p['created_at']), p['id'])), 1)}
            eligible = {f.id for f in schedule if f.week_id != week_row['id'] and f.kickoff_utc and f.kickoff_utc < cutoff}
            results = [SimpleNamespace(**{**r, 'updated_at': utc(r['updated_at']) if r['updated_at'] else None})
                       for r in rows('SELECT r.fixture_id,r.outcome,r.home_score,r.away_score,r.source,r.updated_at FROM results r JOIN fixtures f ON f.id=r.fixture_id JOIN weeks w ON w.id=f.week_id WHERE w.season_id=? ORDER BY r.fixture_id', (season,))
                       if r['fixture_id'] in eligible]
            entries = []
            for p in sorted(picks, key=lambda p: (by_id[p['fixture_id']].kickoff_utc, p['fixture_id'])):
                f = by_id[p['fixture_id']]
                require(p['team'] in (f.home, f.away), 'Picked team does not belong to fixture')
                facts = build_fixture_facts(db, f=f, team=p['team'], schedule=schedule, results=results, weeks=weeks,
                    season=season, cutoff=cutoff, candidate_limit=candidate_limit)
                entries.append(dict(pick_id=p['id'], committed_at=utc(p['created_at']).isoformat(), pick_order=pick_order[p['id']],
                    fixture_id=f.id, home=f.home, away=f.away, kickoff=f.kickoff_utc.isoformat(), picked_team=p['team'],
                    opponent=f.away if p['team'] == f.home else f.home, picked_team_venue='home' if p['team'] == f.home else 'away',
                    packet_version=FIXTURE_VERSION, **facts))
            return validate_context(dict(context_version=CONTEXT_VERSION, prompt_version=PROMPT_VERSION,
                season=season_row, week=week_row, player=player, as_of=cutoff.isoformat(), earliest_kickoff=entries[0]['kickoff'],
                week_first_kickoff=first.isoformat(), candidate_limit=candidate_limit,
                versions=dict(fixture_intelligence=FIXTURE_VERSION, historical_intelligence=HISTORICAL_VERSION),
                slate_status='committed', picks=entries, availability=[
                    'Persisted picks in this snapshot, created by as-of; no pick revision archive.',
                    'Official Result.updated_at must be strictly before as-of; unavailable form is not reconstructed.',
                    'Historical facts describe pre-cutoff matches in this reference snapshot, not an as-known-at-time archive.',
                    'Stored meeting samples do not establish all-time records, appearances or current player membership.']))
    except (OSError, sqlite3.Error) as exc:
        raise CorrespondentError(f'Unable to read local brief snapshot: {exc}') from exc
