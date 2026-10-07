"""Bounded promoted-club context; strict official cutoff, no generation."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import test_pre_match_facts as existing
from pre_match_facts import recent_form_fact, fallback_facts
from correspondent.pre_match_writer import validate_sentence
from correspondent.writer import CorrespondentError


class RecentFormTests(unittest.TestCase):
    def setUp(self):
        self.cutoff = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.target = NS(id=99, home='Coventry City', away='Hull City', kickoff_utc=self.cutoff)
        self.fixtures, self.results = [], []

    def match(self, fid, score=(2, 1), home=True):
        dt = datetime(2026, 8, fid, tzinfo=timezone.utc)
        f = NS(id=fid, home='Coventry City' if home else 'Arsenal',
               away='Arsenal' if home else 'Coventry City', kickoff_utc=dt)
        r = NS(fixture_id=fid, home_score=score[0], away_score=score[1],
               outcome='Home' if score[0] > score[1] else 'Away' if score[1] > score[0] else 'Draw',
               updated_at=dt+timedelta(hours=2))
        self.fixtures.append(f); self.results.append(r)
        return r

    def fact(self, candidates=()):
        return recent_form_fact(self.target, 'Coventry City', self.fixtures, self.results, candidates, self.cutoff)

    def test_one_promoted_club_match_is_score_not_five_match_form(self):
        self.match(1)
        fact = self.fact()
        self.assertEqual(fact['signal_type'], 'PICKED_TEAM_RESULT')
        self.assertIn('Coventry City 2-1 Arsenal', fact['claim'])
        self.assertEqual(len(fact['provenance']['fixtures']), 1)
        self.assertEqual(fact['writing']['scorers'], [])
        validate_sentence(fact['claim'], [fact], [fact], dict(home='Coventry City', away='Hull City'))
        with self.assertRaises(CorrespondentError):
            validate_sentence('The winner arrived at 90+3.', [fact], [fact], dict(home='Coventry City', away='Hull City'))

    def test_several_matches_exact_wdl_gf_ga_and_chronological_results(self):
        self.match(1, (2,1)); self.match(2, (1,1), False); self.match(3, (3,0), False)
        e = self.fact()['evidence']
        self.assertEqual((e['sample_size'],e['wins'],e['draws'],e['losses'],e['goals_for'],e['goals_against']), (3,1,1,1,3,5))
        self.assertEqual(e['results'], ['W','D','L'])
        self.assertFalse(e['complete_season'])

    def test_sample_caps_at_five_and_is_deterministic(self):
        for i in range(1,8): self.match(i)
        before = self.fact()
        self.assertEqual(before['evidence']['sample_size'],5)
        self.assertEqual([r['official_fixture_id'] for r in before['provenance']['fixtures']],[3,4,5,6,7])
        self.fixtures.reverse(); self.results.reverse()
        self.assertEqual(before,self.fact())

    def test_future_and_late_updated_scores_are_not_inspected(self):
        self.match(1)
        before = self.fact()
        self.fixtures.extend([NS(id=2,home='Coventry City',away='Arsenal',kickoff_utc=self.cutoff),
                              NS(id=3,home='Coventry City',away='Arsenal',kickoff_utc=self.cutoff-timedelta(days=1))])
        # Score attributes intentionally absent: checking them would fail.
        self.results.extend([NS(fixture_id=2),NS(fixture_id=3,updated_at=self.cutoff)])
        self.assertEqual(before,self.fact())

    def test_gap_is_bounded_sample_not_current_or_season_streak(self):
        self.match(1); self.match(2).updated_at=self.cutoff; self.match(3)
        f=self.fact()
        self.assertEqual(f['evidence']['sample_size'],2)
        self.assertNotIn('unbeaten',f['claim']); self.assertNotIn('last',f['claim'])
        self.assertFalse(f['evidence']['complete_season'])
        self.assertEqual(f['writing']['comparison_scope']['kind'],'bounded_verified_results')

    def test_substantial_editorial_overlap_suppresses_form_without_mutation(self):
        for i in range(1,4): self.match(i)
        candidate=dict(subject_team='Coventry City',provenance=dict(rows=[dict(kickoff=f.kickoff_utc.isoformat()) for f in self.fixtures[:2]]))
        original=deepcopy(candidate)
        self.assertIsNone(self.fact([candidate]))
        self.assertEqual(candidate,original)

    def test_form_and_result_anchor_are_not_duplicated(self):
        for i in range(1,4): self.match(i)
        with sqlite3.connect(':memory:') as db:
            facts=fallback_facts(db,fixture=self.target,picked_team='Coventry City',history={},candidates=[],
                schedule=self.fixtures,results=self.results,cutoff=self.cutoff,recent_form=True)
        self.assertEqual([f['signal_type'] for f in facts],['RECENT_FORM_CONTEXT'])

    def test_editorial_rows_also_suppress_duplicate_result_anchors(self):
        self.match(1)
        candidate=dict(subject_team='Coventry City', provenance=dict(rows=[dict(fixture_id=1,
            kickoff=self.fixtures[0].kickoff_utc.isoformat())]))
        with sqlite3.connect(':memory:') as db:
            facts=fallback_facts(db,fixture=self.target,picked_team='Coventry City',history={},candidates=[candidate],
                schedule=self.fixtures,results=self.results,cutoff=self.cutoff,recent_form=True)
        self.assertEqual(facts,[])


class RecentScorerTests(unittest.TestCase):
    setUp=existing.FallbackTests.setUp
    fixture=existing.FallbackTests.fixture
    goal=existing.FallbackTests.goal
    history=existing.FallbackTests.history

    def official(self):
        f=NS(id=11,home='Arsenal',away='Man United',kickoff_utc=datetime(2026,8,1,tzinfo=timezone.utc))
        r=NS(fixture_id=11,home_score=2,away_score=0,outcome='Home',updated_at=f.kickoff_utc+timedelta(hours=2))
        return f,r

    def test_matching_reference_scorers_and_anonymous_goal_withholding(self):
        self.fixture(1,'2026-08-01',score=(2,0),season=3)
        self.goal(1,1,30,'1-0'); self.goal(2,1,60,'2-0',player=101)
        f,r=self.official()
        fact=recent_form_fact(self.target,'Arsenal',[f],[r],[],self.as_of,self.db)
        self.assertEqual({s['name'] for s in fact['writing']['scorers']},{'Player 100','Player 101'})
        self.assertTrue(all(s['team_id']==19 for s in fact['writing']['scorers']))
        self.db.execute('UPDATE football_reference_events SET player_name=NULL WHERE id=2')
        fact=recent_form_fact(self.target,'Arsenal',[f],[r],[],self.as_of,self.db)
        self.assertEqual(fact['writing']['scorers'],[])
        self.assertIn('2-0',fact['claim'])

    def test_club_leaders_include_ties_but_require_schedule_coverage(self):
        self.fixture(1,'2026-08-01',score=(2,0),season=3)
        self.goal(1,1,30,'1-0'); self.goal(2,1,60,'2-0',player=101)
        f,r=self.official()
        def facts(schedule):
            return fallback_facts(self.db,fixture=self.target,picked_team='Arsenal',history=self.history(),
                candidates=[],schedule=schedule,results=[r],cutoff=self.as_of)
        leader=next(x for x in facts([f]) if x['signal_type']=='SEASON_SCORERS')
        self.assertEqual(len(leader['evidence']['leaders']),2)
        self.assertEqual(leader['evidence']['goals'],1)
        self.assertEqual(leader['evidence']['team'],'Arsenal')
        missing=NS(id=12,home='Arsenal',away='Man City',kickoff_utc=f.kickoff_utc+timedelta(days=2))
        self.assertNotIn('SEASON_SCORERS',[x['signal_type'] for x in facts([f,missing])])
