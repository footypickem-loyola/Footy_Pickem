"""Writer-only fact eligibility and conservative, unranked context fallbacks.

PR24 detectors, candidate identities and editorial scores are not modified.
"""
from collections import Counter
from hashlib import sha256
import json

from fixture_history import utc, resolve_club_ids, FINAL_STATES, season_start as parse_season
from historical_fixture_intelligence import complete_scorer_evidence, scorer_groups

GOAL_STORIES = {'LATE_DECISIVE_GOAL', 'BRACE', 'HAT_TRICK', 'PLAYER_VS_OPPONENT'}


def writing_metadata(candidate):
    """Recover the named actor from the candidate's authoritative event evidence."""
    evidence = candidate['evidence']
    kind = candidate['signal_type']
    scorers = []
    if kind in GOAL_STORIES:
        player_id = evidence.get('player_id')
        events = [e for e in candidate['provenance'].get('events', [])
                  if e.get('player_provider_id') == player_id and e.get('player_name')]
        if kind == 'LATE_DECISIVE_GOAL':
            events = [e for e in events if e.get('minute') == evidence['minute'] and
                      (e.get('extra_minute') or 0) == (evidence.get('extra_minute') or 0) and
                      e.get('running_score', '').replace(' ', '') == '-'.join(map(str, evidence['score_after']))]
        if not player_id or not events or len({e['player_name'] for e in events}) != 1:
            return None  # An anonymous goal story is not eligible for writing.
        scorers = [dict(player_id=player_id, name=events[0]['player_name'],
                        event_ids=sorted({e['external_event_id'] for e in events}),
                        own_goal=bool(evidence.get('own_goal')), goals=evidence.get('goals'),
                        minute=evidence.get('minute'), extra_minute=evidence.get('extra_minute'))]
    sample = candidate['sample']
    comparison = None
    if kind in {'BEST_DEFENCE', 'WORST_DEFENCE', 'BEST_ATTACK', 'WORST_ATTACK'}:
        comparison = dict(kind='league_ranking_at_cutoff',
                          as_of=candidate['recomputability']['cutoff'])
    if kind.startswith(('H2H_', 'VENUE_H2H_')):
        comparison = dict(kind='bounded_h2h_sequence', count=evidence['count'],
            venue='home' if kind.startswith('VENUE_') else 'both', start=sample['start'], end=sample['end'],
            lower_bound=evidence.get('lower_bound', False))
    return dict(role='editorial', scorers=scorers, comparison_scope=comparison,
        specific_goal_story=kind in GOAL_STORIES,
        scoring_meetings=evidence.get('scoring_meetings'),
        instruction='Use the supplied dates/season; not automatically the latest overall meeting.')


def _events(db, row):
    rows = db.execute('''SELECT * FROM football_reference_events WHERE reference_fixture_id=?
        AND provider='sportmonks' AND is_active=1 AND is_present=1
        AND (rescinded IS NULL OR rescinded=0) ORDER BY sort_order,external_event_id''', (row['reference_fixture_id'],))
    names = [c[0] for c in rows.description]
    return [dict(zip(names, e)) for e in rows]


def _reference_rows(db, team_id, cutoff, season_start=None):
    # Only pre-cutoff result rows and their events are inspected. Unknown kickoffs
    # and incomplete results are returned as gaps for scorer-total completeness.
    query = db.execute('''SELECT f.id AS reference_fixture_id,f.external_fixture_id,
        f.kickoff_utc AS kickoff,f.home_team_id,f.away_team_id,f.home_team_name AS home_team,
        f.away_team_name AS away_team,f.home_score,f.away_score,f.state_id,s.sync_status,s.name AS season
        FROM football_reference_fixtures f JOIN football_reference_seasons s ON s.id=f.reference_season_id
        WHERE f.provider='sportmonks' AND s.provider='sportmonks' AND f.league_id=8 AND s.league_id=8
        AND (f.home_team_id=? OR f.away_team_id=?)
        AND (f.kickoff_utc IS NULL OR julianday(f.kickoff_utc) IS NULL OR julianday(f.kickoff_utc)<julianday(?))
        ORDER BY julianday(f.kickoff_utc) DESC,f.external_fixture_id DESC''', (team_id, team_id, cutoff.isoformat()))
    names = [c[0] for c in query.description]
    rows = [dict(zip(names, r)) for r in query]
    if season_start is not None:
        rows = [r for r in rows if parse_season(r['season']) == season_start]
    return rows


def _complete(row):
    try:
        utc(row['kickoff'])
    except (ValueError, TypeError, AttributeError):
        return False
    return (row['kickoff'] is not None and row['state_id'] in FINAL_STATES and row['sync_status'] == 'completed'
            and all(type(row[k]) is int and row[k] >= 0 for k in ('home_score', 'away_score')))


def _scorers(row):
    # Missing/ambiguous event ledgers never produce exact scorer totals.
    if not complete_scorer_evidence(row):
        return []
    return [dict(player_id=player, name=events[0]['player_name'], team_id=team, goals=len(events),
                 event_ids=sorted(e['external_event_id'] for e in events), own_goal=False,
                 minute=None, extra_minute=None)
            for (team, player), events in sorted(scorer_groups(row).items())
            if len({e['player_name'] for e in events}) == 1]


def _anchor(target_id, row, kind, cutoff, comparison=None):
    clean = {k: v for k, v in row.items() if k != 'events'}
    source = row.get('source', 'Sportmonks reference')
    fact = dict(signal_type=kind, claim=f"{row['home_team']} {row['home_score']}-{row['away_score']} {row['away_team']} on {row['kickoff'][:10]}.",
        evidence=dict(home=row['home_team'], away=row['away_team'], home_score=row['home_score'],
                      away_score=row['away_score'], date=row['kickoff'], season=row.get('season')),
        provenance=dict(source=source, fixtures=[clean]),
        writing=dict(role='fallback', scorers=_scorers(row) if row.get('events') else [],
            comparison_scope=comparison, specific_goal_story=False, scoring_meetings=None,
            instruction='Result context, not a promoted editorial candidate. Name any scorer used; never invent missing scorers.'),
        cutoff=cutoff.isoformat())
    fingerprint = sha256(json.dumps(fact, sort_keys=True).encode()).hexdigest()[:16]
    return dict(id=f'{target_id}:fallback:{kind}:{fingerprint}', **fact)


def recent_form_fact(fixture, team, schedule, results, candidates, cutoff, db=None):
    """Bounded official-result sample, never a season-long or gap-bridging run.

    This writer fallback deliberately does not create/rank editorial candidates.
    Scores are accessed only after kickoff and result-update eligibility checks.
    """
    by_id = {r.fixture_id: r for r in results}
    rows = []
    for f in sorted(schedule, key=lambda f: f.id):
        if (f.id == fixture.id or team not in (f.home, f.away) or f.kickoff_utc is None
                or utc(f.kickoff_utc) >= cutoff):
            continue
        r = by_id.get(f.id)
        if r is None or r.updated_at is None or utc(r.updated_at) >= cutoff:
            continue
        if not all(type(s) is int and s >= 0 for s in (r.home_score, r.away_score)):
            continue
        expected = 'Home' if r.home_score > r.away_score else 'Away' if r.away_score > r.home_score else 'Draw'
        if r.outcome != expected:
            continue
        rows.append(dict(official_fixture_id=f.id, home_team=f.home, away_team=f.away,
            kickoff=utc(f.kickoff_utc).isoformat(), home_score=r.home_score, away_score=r.away_score,
            updated_at=utc(r.updated_at).isoformat(), source='Footy official results'))
    rows = sorted(rows, key=lambda r: (r['kickoff'], r['official_fixture_id']))[-5:]
    if not rows:
        return None
    # Suppress substantial overlap with retained facts for this club, including
    # current-season candidates whose provenance uses `rows`, not `fixtures`.
    covered = set()
    for c in candidates:
        if c['subject_team'] == team:
            covered.update(utc(r['kickoff']) for r in c['provenance'].get('rows', c['provenance'].get('fixtures', [])))
    if sum(utc(r['kickoff']) in covered for r in rows) * 2 >= len(rows):
        return None
    # Enrich only an already eligible official result. A reference event ledger
    # must match the fixture identities, date and score; no anonymous goals.
    if db is not None:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if {'football_reference_fixtures', 'football_reference_seasons', 'football_reference_events'} <= tables:
            latest = rows[-1]
            home_id, away_id = resolve_club_ids(db, (latest['home_team'], latest['away_team']))
            for ref in _reference_rows(db, home_id, cutoff) if home_id else []:
                if (_complete(ref) and ref['home_team_id'] == home_id and ref['away_team_id'] == away_id
                        and utc(ref['kickoff']) == utc(latest['kickoff'])
                        and (ref['home_score'], ref['away_score']) == (latest['home_score'], latest['away_score'])):
                    ref['events'] = _events(db, ref)
                    if _scorers(ref):
                        latest.update(reference_fixture_id=ref['reference_fixture_id'], home_team_id=home_id,
                            away_team_id=away_id, events=ref['events'])
                    break
    if len(rows) == 1:
        return _anchor(fixture.id, rows[0], 'PICKED_TEAM_RESULT', cutoff)
    outcomes, gf, ga = [], 0, 0
    for r in rows:
        scored, conceded = ((r['home_score'], r['away_score']) if team == r['home_team']
                            else (r['away_score'], r['home_score']))
        gf += scored
        ga += conceded
        outcomes.append('W' if scored > conceded else 'L' if scored < conceded else 'D')
    counts = Counter(outcomes)
    evidence = dict(team=team, sample_size=len(rows), results=outcomes, wins=counts['W'], draws=counts['D'],
        losses=counts['L'], goals_for=gf, goals_against=ga, start=rows[0]['kickoff'], end=rows[-1]['kickoff'],
        scope='up_to_five_eligible_official_league_results', complete_season=False,
        scorer_match_date=rows[-1]['kickoff'])
    fact = dict(signal_type='RECENT_FORM_CONTEXT',
        claim=f"{team}: {counts['W']} wins, {counts['D']} draws and {counts['L']} defeats across {len(rows)} Premier League matches "
              f"from {rows[0]['kickoff'][:10]} to {rows[-1]['kickoff'][:10]}, scoring {gf} and conceding {ga}.",
        evidence=evidence, provenance=dict(source='Footy official results',
            fixtures=[{k:v for k,v in r.items() if k != 'events'} for r in rows]), cutoff=cutoff.isoformat(),
        writing=dict(role='fallback', scorers=_scorers(rows[-1]) if rows[-1].get('events') else [], specific_goal_story=False, scoring_meetings=None,
            comparison_scope=dict(kind='bounded_verified_results', start=rows[0]['kickoff'], end=rows[-1]['kickoff'], count=len(rows)),
            instruction='Use exact sample size and dates. This may omit unavailable results; do not infer a consecutive run, full-season record, all-time record or absolute recency. Named scorer totals apply only to scorer_match_date, not the whole sample; identify their club. No unsupported goal minute or decisive-goal claim.'))
    fingerprint = sha256(json.dumps(fact, sort_keys=True).encode()).hexdigest()[:16]
    return dict(id=f'{fixture.id}:fallback:RECENT_FORM_CONTEXT:{fingerprint}', **fact)


def fallback_facts(db, *, fixture, picked_team, history, candidates, schedule, results, cutoff, recent_form=False):
    """Fill up to two usable facts, in A/B/C/D order, without changing ranking.

    C requires a complete pre-cutoff current-season reference goal ledger for the
    club. Missing games, anonymous goals or own-goal ambiguity withhold the total.
    """
    needed = max(0, 2-len(candidates))
    if not needed:
        return []
    output, seen = [], set()
    for c in candidates:
        for r in c['provenance'].get('fixtures', []):
            seen.add((utc(r['kickoff']), r['home_team'], r['away_team']))
        if recent_form:
            fixtures_by_id = {f.id: f for f in schedule}
            for r in c['provenance'].get('rows', []):
                f = fixtures_by_id.get(r['fixture_id'])
                if f is not None:
                    seen.add((utc(r['kickoff']), f.home, f.away))

    def add(row, kind, comparison=None):
        key = (utc(row['kickoff']), row['home_team'], row['away_team'])
        if key not in seen:
            seen.add(key)
            output.append(_anchor(fixture.id, row, kind, cutoff, comparison))

    if recent_form:
        form = recent_form_fact(fixture, picked_team, schedule, results, candidates, cutoff, db)
        if form:
            output.append(form)
            seen.update((utc(r['kickoff']), r['home_team'], r['away_team']) for r in form['provenance']['fixtures'])
        if len(output) >= needed:
            return output

    latest = history.get('most_recent_meeting')
    if latest:
        add(latest, 'H2H_RESULT', dict(kind='most_recent_available_h2h', venue='both', before=cutoff.isoformat(),
                                      limitation='Available reference history, not an all-time or availability archive.'))
    if len(output) >= needed:
        return output
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    reference = {'football_reference_fixtures', 'football_reference_seasons', 'football_reference_events'} <= tables
    opponent = fixture.away if picked_team == fixture.home else fixture.home
    ids = resolve_club_ids(db, (picked_team, opponent)) if reference else (None, None)

    def recent(team, team_id):
        possible = []
        if team_id is not None:
            for row in _reference_rows(db, team_id, cutoff):
                if _complete(row):
                    row['events'] = _events(db, row)
                    possible.append(row)
                    break
        by_id = {r.fixture_id: r for r in results}
        for f in schedule:
            r = by_id.get(f.id)
            if (team not in (f.home, f.away) or f.kickoff_utc is None or f.kickoff_utc >= cutoff or r is None
                    or r.updated_at is None or r.updated_at >= cutoff):
                continue
            if not all(type(s) is int and s >= 0 for s in (r.home_score, r.away_score)):
                continue
            outcome = 'Home' if r.home_score > r.away_score else 'Away' if r.away_score > r.home_score else 'Draw'
            if r.outcome != outcome:
                continue
            possible.append(dict(reference_fixture_id=None, external_fixture_id=None, official_fixture_id=f.id,
                home_team=f.home, away_team=f.away, kickoff=f.kickoff_utc.isoformat(), home_score=r.home_score,
                away_score=r.away_score, source='Footy official results', updated_at=r.updated_at.isoformat(), events=[]))
        return max(possible, key=lambda r: utc(r['kickoff'])) if possible else None

    # B: newest eligible picked-team result; dates avoid unsupported absolute recency.
    row = recent(picked_team, ids[0])
    if row:
        add(row, 'PICKED_TEAM_RESULT', dict(kind='most_recent_available_team_result', team=picked_team, before=cutoff.isoformat()))
    if len(output) >= needed:
        return output[:needed]
    # C: club leaders only when every pre-cutoff fixture and normal goal is known.
    season_start = fixture.kickoff_utc.year - (fixture.kickoff_utc.month < 7)
    for team, team_id in zip((picked_team, opponent), ids):
        if team_id is None or len(output) >= needed:
            continue
        rows = _reference_rows(db, team_id, cutoff, season_start)
        if not rows or not all(_complete(r) for r in rows):
            continue
        # A complete event ledger for a partial set of fixtures does not prove
        # club season leaders. Cross-check known current-season schedule coverage.
        covered_dates = {utc(r['kickoff']) for r in rows}
        if any(team in (f.home, f.away) and (f.kickoff_utc is None or
               (utc(f.kickoff_utc) < cutoff and
                utc(f.kickoff_utc).year - (utc(f.kickoff_utc).month < 7) == season_start and
                utc(f.kickoff_utc) not in covered_dates)) for f in schedule):
            continue
        for r in rows:
            r['events'] = _events(db, r)
        if not all(complete_scorer_evidence(r) and all(len({e['player_name'] for e in events}) == 1
                   for events in scorer_groups(r).values()) for r in rows):
            continue
        tally, names, event_ids = Counter(), {}, {}
        for r in rows:
            for s in _scorers(r):
                if s['team_id'] == team_id:
                    tally[s['player_id']] += s['goals']
                    names.setdefault(s['player_id'], set()).add(s['name'])
                    event_ids.setdefault(s['player_id'], []).extend(s['event_ids'])
        if not tally or any(len(n) != 1 for n in names.values()):
            continue
        leaders = [dict(player_id=p, name=next(iter(names[p])), goals=g, event_ids=sorted(event_ids[p]),
                        own_goal=False, minute=None, extra_minute=None) for p,g in sorted(tally.items()) if g == max(tally.values())]
        evidence = dict(team=team, season_start=season_start, goals=max(tally.values()), leaders=leaders,
                        completed_matches=len(rows), own_goals_excluded=True)
        fingerprint = sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()[:16]
        output.append(dict(id=f'{fixture.id}:fallback:SEASON_SCORERS:{fingerprint}', signal_type='SEASON_SCORERS',
            claim=f"{team}: current-season league leading scorer(s), {max(tally.values())} goals before {cutoff.isoformat()}.",
            evidence=evidence, cutoff=cutoff.isoformat(), provenance=dict(source='Sportmonks reference',
                fixtures=[{k:v for k,v in r.items() if k != 'events'} for r in rows]),
            writing=dict(role='fallback', scorers=leaders, comparison_scope=dict(kind='current_season_club_scorers', before=cutoff.isoformat()),
                specific_goal_story=True, scoring_meetings=None, instruction='Name all joint leaders; no appearance rate or roster assertion.')))
    # D: another simple recent result for the opponent if no stronger fallback exists.
    if len(output) < needed:
        row = recent(opponent, ids[1])
        if row:
            add(row, 'OPPONENT_RESULT')
    return output[:needed]
