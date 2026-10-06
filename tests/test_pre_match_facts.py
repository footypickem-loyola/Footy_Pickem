"""Writer-only fallbacks: authoritative identities and strict pre-cutoff reads."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import test_historical_fixture_intelligence as historical_tests
from pre_match_facts import fallback_facts, writing_metadata, _events
from correspondent.pre_match_writer import validate_sentence, load_system_prompt
from correspondent.writer import CorrespondentError
from test_pre_match_brief import example


class FallbackTests(unittest.TestCase):
    setUp = historical_tests.HistoricalTests.setUp
    fixture = historical_tests.HistoricalTests.fixture
    goal = historical_tests.HistoricalTests.goal
    history = historical_tests.HistoricalTests.history
    packet = historical_tests.HistoricalTests.packet

    def build(self, candidates=None):
        return fallback_facts(self.db, fixture=self.target, picked_team='Arsenal',
            history=self.history(), candidates=candidates or [], schedule=[], results=[], cutoff=self.as_of)

    def test_zero_retained_h2h_result_with_authoritative_scorer(self):
        self.fixture(1, '2026-08-01', score=(1, 0), season=3)
        self.goal(1, 1, 30, '1-0')
        fact = self.build()[0]
        self.assertEqual(fact['signal_type'], 'H2H_RESULT')
        self.assertEqual(fact['writing']['role'], 'fallback')
        self.assertEqual(fact['writing']['scorers'][0]['name'], 'Player 100')
        self.assertEqual(fact['writing']['scorers'][0]['event_ids'], [100001])
        self.assertNotIn('editorial_score', fact)

    def test_cutoff_excludes_equal_and_future_rows_and_event_reads(self):
        self.fixture(1, '2026-08-01', score=(1, 0), home=19, away=9, season=3)
        self.goal(1, 1, 30, '1-0')
        before = self.build()
        self.fixture(2, '2026-08-20', score=(99, 0), season=3)
        self.fixture(3, '2026-09-01', score=(99, 0), season=3)
        seen = []
        def checked(db, row):
            seen.append(row['reference_fixture_id'])
            self.assertEqual(row['reference_fixture_id'], 1)
            return _events(db, row)
        with patch('pre_match_facts._events', side_effect=checked):
            self.assertEqual(before, self.build())
        self.assertTrue(seen)

    def test_anonymous_goal_keeps_result_not_specific_goal_story(self):
        self.fixture(1, '2026-08-01', score=(1, 0), season=3)
        self.goal(1, 1, 90, '1-0', extra=4)
        self.db.execute('UPDATE football_reference_events SET player_name=NULL')
        fact = self.build()[0]
        self.assertEqual(fact['writing']['scorers'], [])
        with self.assertRaises(CorrespondentError):
            validate_sentence('The winner arrived at 90+4.', [fact], [fact], dict(home='Arsenal', away='Man United'))
        validate_sentence('Arsenal won 1-0 on 1 August 2026.', [fact], [fact], dict(home='Arsenal', away='Man United'))

    def test_named_goal_metadata_does_not_mutate_engine_packet(self):
        self.fixture(1, '2025-09-01', score=(1, 0))
        self.goal(1, 1, 90, '1-0', extra=4)
        packet = self.packet()
        original = deepcopy(packet)
        fact = next(c for c in packet['ranked_candidates'] if c['signal_type'] == 'LATE_DECISIVE_GOAL')
        self.assertEqual(writing_metadata(fact)['scorers'][0]['name'], 'Player 100')
        self.build(packet['ranked_candidates'])
        self.assertEqual(original, packet)
        self.assertEqual(original, self.packet())
        for event in fact['provenance']['events']:
            event['player_name'] = None
        self.assertIsNone(writing_metadata(fact))

    def test_season_leaders_require_complete_named_ledger_and_supported_season_name(self):
        self.db.execute("UPDATE football_reference_seasons SET name='2026/27' WHERE id=3")
        self.fixture(1, '2026-08-01', score=(1, 0), season=3)
        self.goal(1, 1, 30, '1-0')
        leader = next(f for f in self.build() if f['signal_type'] == 'SEASON_SCORERS')
        self.assertEqual(leader['evidence']['goals'], 1)
        self.fixture(2, '2026-08-10', score=(1, 0), away=9, season=3)
        # No event for that score: the result remains usable, season totals do not.
        facts = fallback_facts(self.db, fixture=self.target, picked_team='Arsenal', history={},
            candidates=[], schedule=[], results=[], cutoff=self.as_of)
        self.assertNotIn('SEASON_SCORERS', [f['signal_type'] for f in facts])


class SentenceGroundingTests(unittest.TestCase):
    def setUp(self):
        self.pick = next(p for p in example()['picks'] if p['fixture_id'] == 780)
        self.facts = self.pick['candidates']
        self.late = next(f for f in self.facts if f['signal_type'] == 'LATE_DECISIVE_GOAL')

    def check(self, text, facts=None):
        validate_sentence(text, facts or [self.late], self.facts, self.pick)

    def test_scorer_required_and_wrong_or_uncited_scorer_rejected(self):
        self.check('Emiliano Buendía scored the winner at 90+5.')
        self.check('Aston Villa’s Emiliano Buendía scored the winner at 90+5.')
        for text in ('The winner arrived at 90+5.', 'Invented Person scored the winner at 90+5.',
                     'Emiliano Buendía scored the winner at 90+4.',
                     'Emiliano Buendía scored the winner in the 87th minute.',
                     'Emiliano Buendía scored the equaliser at 90+5.',
                     'Emiliano Buendía scored the winner; Leandro Trossard scored too.'):
            with self.subTest(text=text), self.assertRaises(CorrespondentError):
                self.check(text)

    def test_recency_requires_explicit_scope_not_merely_dated_event(self):
        for prefix in ('In their latest meeting, ', 'In their most recent meeting, ', 'In their previous meeting, ', 'In their previous clash, '):
            with self.assertRaisesRegex(CorrespondentError, 'Recency'):
                self.check(prefix + 'Emiliano Buendía scored the winner at 90+5.')
        self.check('In December 2025, Emiliano Buendía scored the winner at 90+5.')
        self.assertIn('unless `writing.comparison_scope` explicitly establishes', load_system_prompt())

    def test_stoppage_time_at_notation_preserves_named_event_citations(self):
        context = example(5)
        for fid, player, club, extra, kind in ((801, 'Fábio Carvalho', 'Brentford', 3, 'equaliser'),
                                             (810, 'Benjamin Sesko', 'Manchester United', 4, 'winner')):
            pick = next(p for p in context['picks'] if p['fixture_id'] == fid)
            fact = next(f for f in pick['candidates'] if f['signal_type'] == 'LATE_DECISIVE_GOAL'
                        and f['evidence']['extra_minute'] == extra)
            validate_sentence(f'{player} scored the {kind} for {club} at 90+{extra}.',
                              [fact], pick['candidates'], pick)
        self.assertIn('Write stoppage-time goals as "at 90+3"', load_system_prompt())

    def test_ordinal_stoppage_time_remains_rejected_by_unchanged_validator(self):
        context = example(5)
        for fid, extra, phrase in ((801, 3, 'in the 90+3rd minute'), (810, 4, 'at the 90+4th-minute')):
            pick = next(p for p in context['picks'] if p['fixture_id'] == fid)
            fact = next(f for f in pick['candidates'] if f['signal_type'] == 'LATE_DECISIVE_GOAL'
                        and f['evidence']['extra_minute'] == extra)
            name = fact['writing']['scorers'][0]['name']
            with self.assertRaisesRegex(CorrespondentError, 'Goal minute'):
                validate_sentence(f'{name} scored for {fact["subject_team"]} {phrase}.',
                                  [fact], pick['candidates'], pick)

    def test_mixed_club_and_opponent_scorers_have_explicit_supported_attribution(self):
        pick = next(p for p in example()['picks'] if p['fixture_id'] == 776)
        fact = pick['fallback_facts'][0]
        row = fact['provenance']['fixtures'][0]
        scorers = {s['name']: s for s in fact['writing']['scorers']}
        self.assertEqual(row['home_team'], 'Sunderland')
        self.assertEqual(row['away_team'], 'Chelsea')
        self.assertEqual(scorers['Trai Hume']['team_id'], row['home_team_id'])
        self.assertEqual(scorers['Cole Palmer']['team_id'], row['away_team_id'])
        self.assertNotEqual(scorers['Cole Palmer']['team_id'], row['home_team_id'])
        validate_sentence('Trai Hume scored for Sunderland; Cole Palmer scored for Chelsea in Sunderland’s 2-1 win on 24 May 2026.',
                          [fact], pick['fallback_facts'], pick)
        brace = next(f for f in pick['candidates'] if f['signal_type'] == 'BRACE')
        self.assertEqual(brace['subject_team'], 'Fulham')
        validate_sentence('Raúl Jiménez scored twice for Fulham in their 3-1 win at Sunderland on 22 February 2026.',
                          [brace], pick['candidates'], pick)
        # Club attribution is a prompt obligation reviewed semantically in live
        # output; these checks do not pretend the lexical validator proves it.
        prompt = load_system_prompt()
        self.assertIn('explicitly name the player AND the', prompt)
        self.assertIn('club they scored for', prompt)
        self.assertIn('attribute each separately', prompt)

    def test_internal_words_fail_and_own_goal_is_explicit(self):
        with self.assertRaises(CorrespondentError):
            self.check('The stored evidence shows Emiliano Buendía scored the winner at 90+5.')
        fact = deepcopy(self.late)
        fact['writing']['scorers'][0]['own_goal'] = True
        with self.assertRaisesRegex(CorrespondentError, 'Own goal'):
            self.check('Emiliano Buendía scored the winner at 90+5.', [fact])


if __name__ == '__main__':
    unittest.main()
