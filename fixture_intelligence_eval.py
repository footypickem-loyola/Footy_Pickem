"""Read-only retrospective review; engine scoring and detectors are unchanged."""
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fixture_history import load_fixture_history
from fixture_intelligence import build_fixture_intelligence

VERSION = 'retrospective-eval-v1'
LABELS = ('EXCELLENT', 'USEFUL', 'MARGINAL', 'NOISY', 'WRONG_OR_MISLEADING')
REASONS = ('too_generic', 'sample_too_small', 'duplicate', 'stale', 'venue_relevant',
           'opponent_relevant', 'player_relevant', 'historically_memorable',
           'pickem_relevant', 'missing_context', 'factually_incomplete')
MISSES = ('data_unavailable', 'detector_missing', 'detector_threshold_too_strict',
          'ranking_too_low', 'suppression_removed_it', 'identity_mapping_issue',
          'historical_depth_insufficient', 'requires_external_editorial_context')
AUDIT_FAMILIES = ('result_runs', 'clean_sheet_runs', 'scoring_runs', 'last_five_wdl',
                  'rolling_frequencies', 'recent_goals', 'home_away_form',
                  'home_away_split', 'overall_venue_contrast', 'league_defence',
                  'season_start', 'continuation', 'exact_prior_season', 'h2h_runs',
                  'venue_h2h', 'brace_hat_trick', 'late_decisive', 'player_opponent', 'red_card')


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime):
        raise ValueError('Known ISO kickoff/as-of timestamps are required')
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def serialize(value):
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + '\n'


def families(candidate):
    kind = candidate['signal_type']
    if kind.startswith('VENUE_H2H_'):
        return ['venue_h2h']
    if kind.startswith('H2H_'):
        return ['h2h_runs']
    fixed = {'EXACT_PRIOR_SEASON_FIXTURE': 'exact_prior_season', 'BRACE': 'brace_hat_trick',
             'HAT_TRICK': 'brace_hat_trick', 'LATE_DECISIVE_GOAL': 'late_decisive',
             'PLAYER_VS_OPPONENT': 'player_opponent', 'PREVIOUS_MEETING_RED_CARD': 'red_card',
             'ROLLING_FORM': 'last_five_wdl', 'RECENT_GOALS': 'recent_goals',
             'HOME_AWAY_SPLIT': 'home_away_split', 'OVERALL_VENUE_CONTRAST': 'overall_venue_contrast',
             'FORM_CONTRAST': 'overall_venue_contrast', 'BEST_DEFENCE': 'league_defence',
             'WORST_DEFENCE': 'league_defence'}
    family = fixed.get(kind)
    if family is None:
        if candidate['family'] == 'RUN':
            family = ('clean_sheet_runs' if 'CLEAN_SHEET' in kind else
                      'scoring_runs' if 'SCORING' in kind else 'result_runs')
        else:
            family = {'FREQUENCY': 'rolling_frequencies', 'SEASON_START': 'season_start',
                      'CONTINUATION': 'continuation'}.get(candidate['family'])
    result = [family] if family else []
    if candidate['family'] in ('RUN', 'FORM', 'FREQUENCY', 'GOALS') and candidate['scope'] in ('home', 'away'):
        result.append('home_away_form')
    return result


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _predicate(value):
    if isinstance(value, dict):
        _require(bool(value), 'Empty fact predicates are too broad')
        if 'at_least' in value:
            _require(set(value) == {'at_least'} and type(value['at_least']) in (int, float), 'Invalid lower bound')
        else:
            for child in value.values():
                _predicate(child)
    elif isinstance(value, list):
        for child in value:
            _predicate(child)
    else:
        _require(value is None or type(value) in (str, int, float, bool), 'Invalid fact predicate')


def validate_gold(document):
    _require(isinstance(document, dict) and document.get('schema_version') == 1
             and isinstance(document.get('items'), list), 'Invalid gold-standard document')
    seen = set()
    for item in document['items']:
        _require(isinstance(item, dict), 'Invalid gold item')
        _require(isinstance(item.get('id'), str) and item['id'] and item['id'] not in seen, 'Duplicate/missing gold ID')
        seen.add(item['id'])
        _require((type(item.get('fixture_id')) is int and item['fixture_id'] > 0) or
                 (item.get('fixture_id') is None and isinstance(item.get('binding_note'), str) and item['binding_note'].strip()), 'Invalid gold fixture ID or unresolved binding')
        for key in ('claim', 'category', 'source', 'miss_rationale'):
            _require(isinstance(item.get(key), str) and item[key].strip(), 'Gold claims need category, source and miss rationale')
        _require(item['category'] in AUDIT_FAMILIES or item['category'] == 'unsupported_story', 'Unknown gold category')
        _require(item.get('miss_reason_if_absent') in MISSES, 'Unknown miss classification')
        if 'requires_official_history_for' in item:
            _require(isinstance(item['requires_official_history_for'], list) and
                     all(isinstance(t, str) and t for t in item['requires_official_history_for']), 'Invalid official history requirement')
        groups = item.get('equivalents')
        _require(isinstance(groups, list), 'Gold equivalents must be a list of alternative fact bundles')
        for group in groups:
            _require(isinstance(group, list) and group, 'Empty equivalent bundle')
            for selector in group:
                _require(isinstance(selector, dict) and not set(selector) -
                         {'signal_types', 'subject_team', 'scope', 'evidence', 'sample', 'external_fixture_ids'}, 'Unknown selector field')
                types = selector.get('signal_types')
                _require(isinstance(types, list) and types and all(isinstance(t, str) and t for t in types), 'Selector needs signal types')
                _require(any(selector.get(k) for k in ('evidence', 'sample', 'external_fixture_ids')), 'Selector needs factual constraints, not just category')
                for key in ('subject_team', 'scope'):
                    if key in selector:
                        _require(isinstance(selector[key], str) and bool(selector[key]), 'Invalid selector identity/scope')
                for key in ('evidence', 'sample'):
                    if key in selector:
                        _require(isinstance(selector[key], dict) and bool(selector[key]), 'Invalid selector facts')
                        _predicate(selector[key])
                if 'external_fixture_ids' in selector:
                    ids = selector['external_fixture_ids']
                    _require(isinstance(ids, list) and ids and all(type(i) is int and i > 0 for i in ids), 'Invalid evidence IDs')
    return document


def validate_reviews(document):
    _require(isinstance(document, dict) and document.get('schema_version') == 1 and
             isinstance(document.get('items'), list), 'Invalid review document')
    seen = set()
    for item in document['items']:
        _require(isinstance(item, dict) and type(item.get('fixture_id')) is int and
                 isinstance(item.get('candidate_id'), str), 'Invalid review identity')
        key = (item['fixture_id'], item['candidate_id'])
        _require(key not in seen, 'Duplicate candidate review')
        seen.add(key)
        _require(item.get('label') in LABELS, 'Invalid quality label')
        _require(isinstance(item.get('reason_tags'), list) and all(t in REASONS for t in item['reason_tags']), 'Invalid review reason tags')
        _require(isinstance(item.get('note'), str) and item['note'].strip(), 'Review needs a rationale')
    return document


def subset(expected, actual):
    """Structured football facts, never lexical similarity of claim text."""
    if isinstance(expected, dict):
        if set(expected) == {'at_least'}:
            return type(actual) in (int, float) and actual >= expected['at_least']
        return isinstance(actual, dict) and all(k in actual and subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, (list, tuple)) and len(expected) == len(actual) and all(subset(a, b) for a, b in zip(expected, actual))
    return type(expected) is type(actual) and expected == actual


def equivalent_fact(candidate, selector):
    if candidate['signal_type'] not in selector['signal_types']:
        return False
    if any(candidate.get(k) != selector[k] for k in ('subject_team', 'scope') if k in selector):
        return False
    if any(not subset(selector[k], candidate[k]) for k in ('evidence', 'sample') if k in selector):
        return False
    evidence_ids = {r['external_fixture_id'] for r in candidate['provenance'].get('fixtures', [])}
    return set(selector.get('external_fixture_ids', [])) <= evidence_ids


def compare_gold(item, packet, availability=None):
    ranked = {c['id']: i for i, c in enumerate(packet['ranked_candidates'], 1)}
    decisions = {d['candidate_id']: d for d in packet['ranking_decisions']}
    bundles = []
    for group in item['equivalents']:
        selected = []
        for selector in group:
            matches = [c for c in packet['candidates'] if equivalent_fact(c, selector)]
            if not matches:
                break
            selected.append(min(matches, key=lambda c: (ranked.get(c['id'], 10**9), -c['editorial_score'], c['id'])))
        if len(selected) == len(group):
            ids = sorted({c['id'] for c in selected})
            bundles.append(ids)
    ids = min(bundles, key=lambda group: (max(ranked.get(i, 10**9) for i in group), len(group), group)) if bundles else []
    surfaced = bool(ids) and all(i in ranked for i in ids)
    rank = max(ranked[i] for i in ids) if surfaced else None
    reason, basis = None, None
    unavailable_teams = [team for team in item.get('requires_official_history_for', [])
                         if (availability or {}).get(team, {}).get('blocked_result_ids')]
    unavailable = not ids and (bool(unavailable_teams) or item['miss_reason_if_absent'] == 'data_unavailable')
    if not surfaced:
        if ids:
            suppressed = [decisions[i]['reason'] for i in ids if i not in ranked]
            reason = 'detector_threshold_too_strict' if all(r == 'below_editorial_threshold' for r in suppressed) else 'suppression_removed_it'
            basis = 'ranking_decisions'
        elif unavailable:
            reason, basis = 'data_unavailable', ('official_result_availability_at_cutoff' if unavailable_teams else 'curated_missing_dataset')
        else:
            reason, basis = item['miss_reason_if_absent'], 'curated_hypothesis'
    status = ('SURFACED' if surfaced else 'DATA UNAVAILABLE AT REPLAY' if unavailable else
              'HISTORICAL DEPTH INSUFFICIENT' if reason == 'historical_depth_insufficient' else
              'EXTERNAL DATA REQUIRED' if reason == 'requires_external_editorial_context' else 'ENGINE MISS')
    return dict(gold_id=item['id'], fixture_id=item['fixture_id'], claim=item['claim'], category=item['category'],
                source=item['source'], raw_equivalent_found=bool(ids), surfaced=surfaced,
                matching_candidate_id=ids[0] if ids else None, matching_candidate_ids=ids,
                rank=rank, top_3=surfaced and rank <= 3, top_5=surfaced and rank <= 5,
                assessment=status, eligible_for_primary_coverage=not unavailable, unavailable_teams=unavailable_teams,
                miss_reason=reason, miss_reason_basis=basis,
                top_5_miss_reason=None if surfaced and rank <= 5 else 'ranking_too_low' if surfaced else reason,
                miss_rationale=item['miss_rationale'] if not ids else None)


def duplication_flags(candidates):
    """Conservative shared-story flags for review, not a new suppression policy."""
    seen, flags = [], []
    for candidate in candidates:
        metric = candidate['evidence'].get('metric')
        kind = candidate['signal_type']
        if metric and (kind.endswith('_RUN') or kind.endswith('_SEASON_START')):
            metric = {'WINNING': 'positive_result', 'UNBEATEN': 'positive_result',
                      'LOSING': 'negative_result', 'WINLESS': 'negative_result'}.get(metric, metric)
            key = (candidate['provenance']['source'], candidate['subject_team'], metric)
            rows = set(candidate['sample']['fixture_ids'])
        else:
            continue
        prior = next((item for item in seen if item[0] == key and
                      (rows <= item[1] or item[1] <= rows)), None)
        if prior:
            flags.append(dict(candidate_id=candidate['id'], overlaps_with=prior[2], reason='same_team_related_metric_and_nested_evidence_window'))
        seen.append((key, rows, candidate['id']))
    return flags


def metrics(fixtures, comparisons, reviews):
    raw = [c for f in fixtures for c in f['packet']['candidates']]
    ranked = [c for f in fixtures for c in f['packet']['ranked_candidates']]
    labels = Counter(r['label'] for r in reviews)
    eligible_comparisons = [c for c in comparisons if c.get('eligible_for_primary_coverage', True)]
    denominator = len(eligible_comparisons)
    rate = lambda n, d: round(n / d, 4) if d else None
    counts = {name: sum(bool(c[name]) for c in eligible_comparisons) for name in ('raw_equivalent_found', 'surfaced', 'top_3', 'top_5')}
    supported = [c for c in comparisons if c['assessment'] in ('SURFACED', 'ENGINE MISS')]
    audit = {family: dict(raw=0, ranked=0, top_5=0, gold_hits=0, gold_misses=0, gold_not_evaluable=0, labels={label: 0 for label in LABELS}) for family in AUDIT_FAMILIES}
    for values, bucket in ((raw, 'raw'), (ranked, 'ranked'), ([c for f in fixtures for c in f['packet']['ranked_candidates'][:5]], 'top_5')):
        for candidate in values:
            for family in families(candidate):
                audit[family][bucket] += 1
    for comparison in comparisons:
        if comparison['category'] in audit:
            bucket = ('gold_not_evaluable' if not comparison.get('eligible_for_primary_coverage', True) else
                      'gold_hits' if comparison['surfaced'] else 'gold_misses')
            audit[comparison['category']][bucket] += 1
    lookup = {c['id']: c for c in raw}
    for review in reviews:
        for family in families(lookup[review['candidate_id']]):
            audit[family]['labels'][review['label']] += 1
    top_slots = sum(min(5, len(f['packet']['ranked_candidates'])) for f in fixtures)
    duplicate_slots = sum(len(f['review_summary']['duplication_flags']) for f in fixtures)
    return dict(fixture_count=len(fixtures), raw_candidate_count=len(raw), ranked_candidate_count=len(ranked),
                gold_items=denominator, bound_gold_items=len(comparisons),
                gold_excluded_data_unavailable_at_replay=len(comparisons)-denominator, gold_counts=counts,
                supported_data_only_items=len(supported),
                supported_data_only_coverage_rate=rate(sum(c['surfaced'] for c in supported), len(supported)),
                gold_coverage_rate=rate(counts['surfaced'], denominator), top_3_coverage_rate=rate(counts['top_3'], denominator),
                top_5_coverage_rate=rate(counts['top_5'], denominator),
                review_count=len(reviews), unreviewed_candidate_count=len(raw)-len(reviews),
                label_counts={label: labels[label] for label in LABELS},
                label_rates_among_reviewed={label: rate(labels[label], len(reviews)) for label in LABELS},
                duplication_rate=rate(duplicate_slots, top_slots), duplication_flagged_slots=duplicate_slots, top_5_slots=top_slots,
                fixtures_with_zero_strong_signals=sum(not any(c['editorial_score'] >= 80 for c in f['packet']['ranked_candidates']) for f in fixtures),
                fixtures_with_zero_usable_signals=sum(not f['packet']['ranked_candidates'] for f in fixtures),
                miss_counts=dict(sorted(Counter(c['miss_reason'] for c in eligible_comparisons if c['miss_reason']).items())),
                assessment_counts=dict(sorted(Counter(c['assessment'] for c in comparisons).items())),
                detector_families=audit,
                definitions=dict(coverage='equivalent story retained anywhere in ranked candidates; top-N requires every fact in a bundle; DATA UNAVAILABLE AT REPLAY excluded from primary denominator',
                                 strong='editorial score >=80, an engine-score proxy, not a human quality label',
                                 duplication='flagged later top-5 slots / all top-5 slots; conservative shared-evidence proxy',
                                 labels='manual review subset only; not an unbiased sample',
                                 families='overlapping audit categories; venue form is also counted in its base family'))


def evaluate(database, *, season, weeks=None, fixture_ids=None, start=None, end=None, as_of=None, gold=None, reviews=None, supplemental_gold=None):
    gold = validate_gold(gold or dict(schema_version=1, items=[]))
    reviews = validate_reviews(reviews or dict(schema_version=1, items=[]))
    supplemental_gold = validate_gold(supplemental_gold or dict(schema_version=1, items=[]))
    _require(type(season) is int and season > 0, 'Season must be an official database season ID')
    _require(sum(bool(x) for x in (weeks, fixture_ids, start or end)) == 1, 'Select weeks, fixture IDs, or a date range')
    if start or end:
        _require(start is not None and end is not None and utc(start) < utc(end), 'Date range requires start < end (exclusive)')
    path = Path(database).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        db.row_factory = sqlite3.Row
        def records(sql, params=(), parse_dates=True):
            result = []
            for row in db.execute(sql, params):
                value = dict(row)
                for key in ('kickoff_utc', 'updated_at'):
                    if parse_dates and key in value and value[key] is not None:
                        value[key] = utc(value[key])
                result.append(SimpleNamespace(**value))
            return result
        all_weeks = records('SELECT id,season_id,number FROM weeks WHERE season_id=? ORDER BY number,id', (season,))
        schedule = records('SELECT f.id,f.week_id,f.home,f.away,f.kickoff_utc FROM fixtures f JOIN weeks w ON w.id=f.week_id WHERE w.season_id=? ORDER BY f.id', (season,))
        _require(bool(schedule), 'No fixtures in selected season')
        _require(len({f.id for f in schedule}) == len(schedule), 'Duplicate fixture IDs')
        _require(all(f.home and f.away and f.home != f.away for f in schedule), 'Malformed fixture identities')
        week_map = {w.id: w for w in all_weeks}
        if weeks:
            _require(set(weeks) <= {w.number for w in all_weeks}, 'Unknown matchweek')
            targets = [f for f in schedule if week_map[f.week_id].number in weeks]
        elif fixture_ids:
            _require(set(fixture_ids) <= {f.id for f in schedule}, 'Unknown fixture in selected season')
            targets = [f for f in schedule if f.id in fixture_ids]
        else:
            _require(all(f.kickoff_utc is not None for f in schedule), 'Date selection cannot silently omit unknown kickoffs')
            targets = [f for f in schedule if utc(start) <= f.kickoff_utc < utc(end)]
        _require(bool(targets), 'No selected fixtures')
        results = records('SELECT r.fixture_id,r.outcome,r.home_score,r.away_score,r.source,r.updated_at FROM results r JOIN fixtures f ON f.id=r.fixture_id JOIN weeks w ON w.id=f.week_id WHERE w.season_id=? ORDER BY r.fixture_id', (season,), parse_dates=False)
        slates, output = [], []
        for number in sorted({week_map[f.week_id].number for f in targets}):
            whole = [f for f in schedule if week_map[f.week_id].number == number]
            _require(len(whole) == 10 and len({team for f in whole for team in (f.home, f.away)}) == 20,
                     'A matchweek must have exactly 10 fixtures and 20 distinct teams')
            _require(all(f.kickoff_utc is not None for f in whole), 'All matchweek kickoffs must be known')
            first = min(f.kickoff_utc for f in whole)
            cutoff = utc(as_of) if as_of is not None else first-timedelta(hours=24)
            _require(cutoff < first, 'As-of must precede the FIRST kickoff of the whole matchweek')
            selected = sorted((f for f in targets if week_map[f.week_id].number == number), key=lambda f: (f.kickoff_utc, f.id))
            slates.append(dict(week=number, first_kickoff=first.isoformat(), cutoff=cutoff.isoformat(),
                               fixture_ids=[f.id for f in selected], full_matchweek=len(selected) == 10))
            # Defense in depth: no target-slate result reaches the pure engine.
            excluded = {f.id for f in whole}
            eligible = {f.id for f in schedule if f.kickoff_utc is not None and f.kickoff_utc < cutoff and f.id not in excluded}
            safe_results = [SimpleNamespace(**{**vars(r), 'updated_at': utc(r.updated_at) if r.updated_at is not None else None})
                            for r in results if r.fixture_id in eligible]
            for fixture in selected:
                history = load_fixture_history(db, fixture=fixture, as_of=cutoff)
                packet = build_fixture_intelligence(fixture=fixture, fixtures=schedule, results=safe_results,
                                                    weeks=all_weeks, season_id=season, as_of=cutoff, reference_history=history)
                ranked = packet['ranked_candidates']
                top = [dict(candidate_id=c['id'], claim=c['claim'], signal_type=c['signal_type'], score=c['editorial_score']) for c in ranked[:5]]
                blocked = {e['fixture_id'] for e in packet['diagnostics']['exclusions']
                           if e['reason'] in ('result_not_available_before_cutoff', 'missing_official_result', 'unknown_kickoff')}
                availability = {team: dict(blocked_result_ids=sorted(f.id for f in schedule if
                                    f.id in blocked and team in (f.home, f.away))) for team in (fixture.home, fixture.away)}
                output.append(dict(week=number, fixture=packet['fixture'], cutoff=packet['cutoff'], packet=packet,
                                   official_history_availability=availability,
                                   current_season_review_status='NOT EVALUABLE FROM AVAILABLE SNAPSHOT' if any(v['blocked_result_ids'] for v in availability.values()) else 'EVALUABLE',
                                   raw_candidate_count=len(packet['candidates']),
                                   suppressed_candidates=[d for d in packet['ranking_decisions'] if not d['selected']],
                                   review_summary=dict(top_signals=top, usable_signal_count=len(ranked),
                                                       duplication_flags=duplication_flags(ranked[:5]))))
        by_id = {f['fixture']['id']: f for f in output}
        selected_reviews = [r for r in reviews['items'] if r['fixture_id'] in by_id]
        for review in selected_reviews:
            _require(review['candidate_id'] in {c['id'] for c in by_id[review['fixture_id']]['packet']['candidates']}, 'Review refers to an unknown candidate; regenerate for this snapshot/cutoff')
        comparisons = [compare_gold(g, by_id[g['fixture_id']]['packet'], by_id[g['fixture_id']]['official_history_availability']) for g in gold['items'] if g['fixture_id'] in by_id]
        supplemental = [compare_gold(g, by_id[g['fixture_id']]['packet'], by_id[g['fixture_id']]['official_history_availability']) for g in supplemental_gold['items'] if g['fixture_id'] in by_id]
        return dict(schema_version=1, evaluation_version=VERSION, season_id=season,
                    cutoff_rule='24h before the first kickoff of each complete matchweek' if as_of is None else 'explicit as-of strictly before the first kickoff of each complete matchweek',
                    limitations=['Official result.updated_at is enforced unchanged; later corrections/backfills are unavailable, not rewound.',
                                 'Reference facts describe pre-cutoff matches in the supplied corrected snapshot, not an as-known-at-time archive.',
                                 'No new detector, AI writer, external context, scraping, or subjective engine labels.',
                                 'Coverage is semantic structured-fact matching against a small curated set, not text matching or statistical model accuracy.'],
                    slates=slates, fixtures=output, comparisons=comparisons, reviews=selected_reviews,
                    gold_metadata={k: v for k, v in gold.items() if k != 'items'},
                    review_metadata={k: v for k, v in reviews.items() if k != 'items'},
                    gold_items_outside_selection=len(gold['items'])-len(comparisons),
                    unscored_gold_items=[dict(g, evaluation_status='UNRESOLVED TARGET' if g['fixture_id'] is None else 'FIXTURE OUTSIDE SELECTED SLATES') for g in gold['items'] if g['fixture_id'] not in by_id],
                    supplemental_benchmark=dict(metadata={k: v for k, v in supplemental_gold.items() if k != 'items'},
                                                comparisons=supplemental, metrics=metrics(output, supplemental, selected_reviews)),
                    metrics=metrics(output, comparisons, selected_reviews))
