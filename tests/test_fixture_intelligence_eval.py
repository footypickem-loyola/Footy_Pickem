from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fixture_intelligence_eval import (compare_gold, duplication_flags, equivalent_fact, evaluate,
                                       metrics, serialize, validate_gold, validate_reviews)
from scripts.evaluate_fixture_intelligence import main


def gold_item(fixture_id=600):
    return dict(id='four-wins', fixture_id=fixture_id, claim='Four victories from five league games.',
                category='rolling_frequencies', source='synthetic regression, not external preview',
                miss_reason_if_absent='detector_missing', miss_rationale='Test fact is supported by the five previous matches.',
                equivalents=[[dict(signal_types=['WINNING_LAST_5'], subject_team='T0', scope='overall',
                                   evidence=dict(metric='WINNING', count=4, denominator=5))],
                             [dict(signal_types=['ROLLING_FORM'], subject_team='T0', scope='overall',
                                   evidence=dict(wins=4, losses=1, draws=0), sample=dict(size=5))]])


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'eval.db'
        self.start = datetime(2026, 8, 1, 15, tzinfo=timezone.utc)
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('CREATE TABLE weeks(id INTEGER,season_id INTEGER,number INTEGER);'
                             'CREATE TABLE fixtures(id INTEGER,week_id INTEGER,home TEXT,away TEXT,kickoff_utc TEXT);'
                             'CREATE TABLE results(fixture_id INTEGER,outcome TEXT,home_score INTEGER,away_score INTEGER,source TEXT,updated_at TEXT);')
            for week in range(1, 7):
                db.execute('INSERT INTO weeks VALUES(?,?,?)', (week, 2, week))
                for i in range(10):
                    kickoff = self.start + timedelta(days=7*(week-1), hours=i)
                    fid = week*100+i
                    db.execute('INSERT INTO fixtures VALUES(?,?,?,?,?)', (fid, week, f'T{i*2}', f'T{i*2+1}', kickoff.isoformat()))
                    hs, aws = (0, 1) if week == 3 else (1, 0)
                    db.execute('INSERT INTO results VALUES(?,?,?,?,?,?)', (fid, 'Home' if hs else 'Away', hs, aws, 'test', (kickoff+timedelta(hours=2)).isoformat()))
            db.commit()

    def run_eval(self, **kwargs):
        return evaluate(self.path, season=2, **(kwargs or {'weeks': [6]}))

    def change(self, sql):
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript(sql)

    def test_deterministic_stable_order_and_read_only(self):
        before = self.path.read_bytes()
        first = serialize(self.run_eval())
        self.assertEqual(first, serialize(self.run_eval()))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([f['fixture']['id'] for f in json.loads(first)['fixtures']], list(range(600, 610)))
        self.change('CREATE TABLE saved AS SELECT * FROM results; DELETE FROM results; INSERT INTO results SELECT * FROM saved ORDER BY fixture_id DESC; DROP TABLE saved;')
        self.assertEqual(first, serialize(self.run_eval()))

    def test_query_only_and_no_flask_import(self):
        # Probe the connection through the actual history callback, before engines run.
        from unittest.mock import patch
        from fixture_intelligence_eval import load_fixture_history
        def check(db, **kwargs):
            self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                db.execute('DELETE FROM results')
            return load_fixture_history(db, **kwargs)
        with patch('fixture_intelligence_eval.load_fixture_history', side_effect=check):
            self.run_eval()
        import subprocess, sys
        result = subprocess.run([sys.executable, '-c', "import fixture_intelligence_eval, sys; assert 'pickem_flask_htmx_tabs' not in sys.modules"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_whole_week_selection_and_partial_selectors(self):
        report = self.run_eval()
        self.assertEqual(report['slates'][0]['cutoff'], (self.start+timedelta(days=34)).isoformat())
        partial = self.run_eval(fixture_ids=[609])
        self.assertEqual(partial['slates'][0]['cutoff'], report['slates'][0]['cutoff'])
        self.assertFalse(partial['slates'][0]['full_matchweek'])
        dates = self.run_eval(start=self.start+timedelta(days=35), end=self.start+timedelta(days=36))
        self.assertEqual(report['fixtures'], dates['fixtures'])

    def test_future_and_target_week_results_cannot_leak(self):
        before = serialize(self.run_eval())
        # Even fraudulently early availability timestamps cannot admit target-week results.
        self.change("UPDATE results SET home_score=99,away_score=0,outcome='Home',updated_at='2020-01-01' WHERE fixture_id>=600;")
        self.assertEqual(before, serialize(self.run_eval()))
        self.change("INSERT INTO weeks VALUES(7,2,7); INSERT INTO fixtures VALUES(700,7,'T0','T1','2027-01-01'); INSERT INTO results VALUES(700,'Away',0,99,'test','2020-01-01');")
        self.assertEqual(before, serialize(self.run_eval()))
        self.change("UPDATE results SET updated_at='malformed future metadata' WHERE fixture_id>=600;")
        self.assertEqual(before, serialize(self.run_eval()))
        for f in self.run_eval()['fixtures']:
            for c in f['packet']['candidates']:
                self.assertTrue(c['sample']['end'] < f['cutoff'])
                self.assertTrue(all(i < 600 for i in c['sample']['fixture_ids']))

    def test_future_reference_result_and_event_cannot_leak(self):
        from football_reference_schema import create_schema
        from football_reference import upsert
        with closing(sqlite3.connect(self.path)) as db:
            create_schema(db)
            sid = upsert(db, 'football_reference_seasons', dict(provider='sportmonks', external_season_id=28083, league_id=8, name='2026/2027', sync_status='completed', fixture_count=1, last_successful_sync_at='2026-10-01'), ('provider', 'external_season_id'))
            fid = upsert(db, 'football_reference_fixtures', dict(provider='sportmonks', external_fixture_id=999, reference_season_id=sid, league_id=8, kickoff_utc='2027-01-01T15:00:00+00:00', home_team_id=500, home_team_name='T0', away_team_id=501, away_team_name='T1', home_score=1, away_score=0, state_id=5, state='FT', created_at='2026-10-01', updated_at='2026-10-01'), ('provider', 'external_fixture_id'))
            db.commit()
        before = serialize(self.run_eval())
        self.change("UPDATE football_reference_fixtures SET home_score=40; INSERT INTO football_reference_events(provider,external_event_id,reference_fixture_id,type_id,event_type,is_active,is_present,created_at,updated_at) VALUES('sportmonks',1,"+str(fid)+",14,'goal',1,1,'2026-10-01','2026-10-01');")
        self.assertEqual(before, serialize(self.run_eval()))

    def test_later_official_update_is_not_backdated(self):
        self.change("UPDATE results SET updated_at='2026-12-01';")
        report = self.run_eval()
        self.assertEqual(report['metrics']['raw_candidate_count'], 0)
        self.assertEqual(report['metrics']['fixtures_with_zero_usable_signals'], 10)
        self.assertTrue(all(e['reason'] == 'result_not_available_before_cutoff' for e in report['fixtures'][0]['packet']['diagnostics']['exclusions']))
        self.assertIsNone(report['metrics']['gold_coverage_rate'])

    def test_strict_cutoff_and_malformed_selection(self):
        for kwargs in ({'weeks': [6], 'as_of': self.start+timedelta(days=35)},
                       {'fixture_ids': [609], 'as_of': self.start+timedelta(days=35, hours=1)},
                       {'weeks': [99]}, {'fixture_ids': [999]}, {'weeks': [6], 'fixture_ids': [600]},
                       {'start': self.start}, {'start': self.start, 'end': self.start}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_eval(**kwargs)
        self.change('DELETE FROM fixtures WHERE id=609;')
        with self.assertRaises(ValueError):
            self.run_eval()

    def test_malformed_fixture(self):
        self.change("UPDATE fixtures SET kickoff_utc=NULL WHERE id=600;")
        with self.assertRaises(ValueError):
            self.run_eval()
        self.change("UPDATE fixtures SET kickoff_utc='bad' WHERE id=600;")
        with self.assertRaises(ValueError):
            self.run_eval()

    def test_semantic_matching_ignores_wording_but_distinguishes_runs(self):
        item = gold_item()
        packet = self.run_eval()['fixtures'][0]['packet']
        comparison = compare_gold(item, packet)
        self.assertTrue(comparison['surfaced'])
        changed = deepcopy(packet)
        for candidate in changed['candidates']:
            candidate['claim'] = 'Completely different wording'
        self.assertEqual(comparison, compare_gold(item, changed))
        wrong = deepcopy(item)
        wrong['equivalents'] = [[dict(signal_types=['WINNING_RUN'], subject_team='T0', scope='overall', evidence=dict(count=4))]]
        self.assertFalse(compare_gold(wrong, packet)['raw_equivalent_found'])
        wrong['equivalents'][0][0]['signal_types'] = ['WINNING_LAST_5']
        wrong['equivalents'][0][0]['subject_team'] = 'T1'
        self.assertFalse(compare_gold(wrong, packet)['raw_equivalent_found'])
        self.assertFalse(equivalent_fact(packet['candidates'][0], dict(signal_types=[packet['candidates'][0]['signal_type']], evidence={'count': True})))

    def test_miss_classification_and_metrics(self):
        packet = self.run_eval()['fixtures'][0]['packet']
        item = gold_item()
        result = compare_gold(item, packet)
        candidate_ids = result['matching_candidate_ids']
        altered = deepcopy(packet)
        altered['ranked_candidates'] = [c for c in altered['ranked_candidates'] if c['id'] not in candidate_ids]
        # Remove all equivalent alternatives so suppression classification is unambiguous.
        item['equivalents'] = [item['equivalents'][0]]
        matches = [c for c in altered['candidates'] if equivalent_fact(c, item['equivalents'][0][0])]
        altered['ranked_candidates'] = [c for c in altered['ranked_candidates'] if c not in matches]
        for d in altered['ranking_decisions']:
            if d['candidate_id'] in [c['id'] for c in matches]:
                d['selected'], d['reason'] = False, 'below_editorial_threshold'
        self.assertEqual(compare_gold(item, altered)['miss_reason'], 'detector_threshold_too_strict')
        for d in altered['ranking_decisions']:
            d['reason'] = 'redundant_with_stronger_fact'
        self.assertEqual(compare_gold(item, altered)['miss_reason'], 'suppression_removed_it')
        altered['candidates'] = []
        for reason in ('data_unavailable', 'detector_missing', 'historical_depth_insufficient', 'identity_mapping_issue', 'requires_external_editorial_context'):
            item['miss_reason_if_absent'] = reason
            self.assertEqual(compare_gold(item, altered)['miss_reason'], reason)
        missing = deepcopy(gold_item())
        missing.update(id='missing', equivalents=[])
        report = self.run_eval(weeks=[6], gold=dict(schema_version=1, items=[gold_item(), missing]))
        self.assertEqual(report['metrics']['gold_coverage_rate'], 0.5)
        self.assertEqual(report['metrics']['gold_counts']['surfaced'], 1)
        self.assertEqual(report['metrics']['miss_counts'], {'detector_missing': 1})

    def test_rank_below_five_and_bundle_coverage(self):
        packet = self.run_eval()['fixtures'][0]['packet']
        item = gold_item()
        target = next(c for c in packet['candidates'] if equivalent_fact(c, item['equivalents'][0][0]))
        item['equivalents'] = [item['equivalents'][0]]
        packet['ranked_candidates'] = [dict(target, id='filler'+str(i)) for i in range(5)] + [target]
        result = compare_gold(item, packet)
        self.assertTrue(result['surfaced'])
        self.assertFalse(result['top_5'])
        self.assertEqual(result['top_5_miss_reason'], 'ranking_too_low')
        item['equivalents'][0].append(dict(signal_types=['NON_EXISTENT'], evidence=dict(count=4)))
        self.assertFalse(compare_gold(item, packet)['raw_equivalent_found'])

    def test_review_schema_labels_and_unknown_candidate(self):
        report = self.run_eval()
        candidate = report['fixtures'][0]['packet']['ranked_candidates'][0]
        review = dict(fixture_id=600, candidate_id=candidate['id'], label='USEFUL', reason_tags=['venue_relevant'], note='Test rationale')
        data = dict(schema_version=1, items=[review])
        result = self.run_eval(weeks=[6], reviews=data)
        self.assertEqual(result['metrics']['label_rates_among_reviewed']['USEFUL'], 1)
        review['candidate_id'] = 'stale-id'
        with self.assertRaises(ValueError):
            self.run_eval(weeks=[6], reviews=data)
        review['label'] = 'MADE_UP'
        with self.assertRaises(ValueError):
            validate_reviews(data)

    def test_gold_schema_rejects_bad_and_overbroad_inputs(self):
        valid = dict(schema_version=1, items=[gold_item()])
        self.assertEqual(validate_gold(valid), valid)
        for change in (dict(fixture_id='600'), dict(miss_reason_if_absent='unknown'), dict(claim=''),
                       dict(equivalents=[[]]), dict(equivalents=[[dict(signal_types=['RUN'])]]),
                       dict(equivalents=[[dict(signal_types=['RUN'], evidence={'count': {'at_least': 'four'}})]])):
            value = deepcopy(valid)
            value['items'][0].update(change)
            with self.assertRaises(ValueError):
                validate_gold(value)
        valid['items'].append(deepcopy(valid['items'][0]))
        with self.assertRaises(ValueError):
            validate_gold(valid)

    def test_cli_refuses_overwriting_database(self):
        before = self.path.read_bytes()
        with self.assertRaises(SystemExit):
            main(['--db', str(self.path), '--season', '2', '--week', '6', '--output', str(self.path)])
        self.assertEqual(before, self.path.read_bytes())

    def test_overlap_flags_are_review_metadata(self):
        packet = self.run_eval()['fixtures'][0]['packet']
        c = next(c for c in packet['candidates'] if c['signal_type'] == 'WINNING_RUN')
        other = deepcopy(c)
        other['id'] = 'other'
        flags = duplication_flags([c, other])
        self.assertEqual(flags[0]['overlaps_with'], c['id'])
        self.assertEqual(packet, self.run_eval()['fixtures'][0]['packet'])

    def test_suppressed_anchor_can_match_retained_event_story(self):
        anchor = dict(id='anchor', signal_type='EXACT_PRIOR_SEASON_FIXTURE', subject_team='A', scope='home',
                      evidence=dict(home_score=1, away_score=3), sample={}, editorial_score=85,
                      provenance=dict(fixtures=[dict(external_fixture_id=99)]))
        event = dict(anchor, id='event', signal_type='LATE_DECISIVE_GOAL',
                     evidence=dict(final_score=[1, 3], kind='winner'), editorial_score=94)
        packet = dict(candidates=[anchor, event], ranked_candidates=[event], ranking_decisions=[
            dict(candidate_id='anchor', reason='historical_anchor_covered_by_event'),
            dict(candidate_id='event', reason='selected')])
        item = gold_item()
        item['equivalents'] = [[dict(signal_types=['EXACT_PRIOR_SEASON_FIXTURE'], evidence=dict(home_score=1, away_score=3), external_fixture_ids=[99])],
                               [dict(signal_types=['LATE_DECISIVE_GOAL'], evidence=dict(final_score=[1, 3]), external_fixture_ids=[99])]]
        result = compare_gold(item, packet)
        self.assertTrue(result['top_3'])
        self.assertEqual(result['matching_candidate_id'], 'event')
        self.assertIsNone(result['miss_reason'])

    def test_committed_gold_and_review_schemas(self):
        root = Path(__file__).resolve().parents[1]
        validate_gold(json.loads((root/'tests/fixtures/fixture-intelligence-gold-standard.json').read_text(encoding='utf-8')))
        validate_gold(json.loads((root/'tests/fixtures/fixture-intelligence-snapshot-seed.json').read_text(encoding='utf-8')))
        validate_reviews(json.loads((root/'docs/pr23_candidate_reviews.json').read_text(encoding='utf-8')))

    def test_unavailable_replay_excluded_from_primary_coverage(self):
        item = gold_item()
        item['requires_official_history_for'] = ['T0']
        self.change("UPDATE results SET updated_at='2026-10-05';")
        report = self.run_eval(weeks=[6], gold=dict(schema_version=1, items=[item]))
        self.assertEqual(report['comparisons'][0]['assessment'], 'DATA UNAVAILABLE AT REPLAY')
        self.assertFalse(report['comparisons'][0]['eligible_for_primary_coverage'])
        self.assertEqual(report['metrics']['gold_items'], 0)
        self.assertIsNone(report['metrics']['gold_coverage_rate'])
        self.assertEqual(report['metrics']['gold_excluded_data_unavailable_at_replay'], 1)
        self.assertEqual(report['metrics']['miss_counts'], {})
        self.assertEqual(report['metrics']['detector_families']['rolling_frequencies']['gold_misses'], 0)
        self.assertEqual(report['fixtures'][0]['current_season_review_status'], 'NOT EVALUABLE FROM AVAILABLE SNAPSHOT')

    def test_unresolved_and_supplemental_targets_never_pool_primary_metrics(self):
        unresolved = dict(gold_item(), id='unresolved', fixture_id=None, binding_note='No opponent/date supplied')
        report = self.run_eval(weeks=[6], gold=dict(schema_version=1, items=[unresolved]),
                               supplemental_gold=dict(schema_version=1, items=[gold_item()]))
        self.assertEqual(report['metrics']['gold_items'], 0)
        self.assertEqual(report['unscored_gold_items'][0]['evaluation_status'], 'UNRESOLVED TARGET')
        self.assertEqual(report['supplemental_benchmark']['metrics']['gold_coverage_rate'], 1)

    def test_depth_external_and_engine_assessments_are_distinct(self):
        item = dict(gold_item(), equivalents=[])
        packet = self.run_eval()['fixtures'][0]['packet']
        for reason, expected in [('historical_depth_insufficient', 'HISTORICAL DEPTH INSUFFICIENT'),
                                  ('data_unavailable', 'DATA UNAVAILABLE AT REPLAY'),
                                  ('requires_external_editorial_context', 'EXTERNAL DATA REQUIRED'),
                                  ('detector_missing', 'ENGINE MISS')]:
            item['miss_reason_if_absent'] = reason
            self.assertEqual(compare_gold(item, packet)['assessment'], expected)
