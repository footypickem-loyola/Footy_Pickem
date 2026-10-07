"""Offline regressions for the observed PR27 false acceptances/rejections."""
from copy import deepcopy
import unittest

from correspondent.pre_match_writer import validate_fixture_output
from correspondent.football_claim_checks import validate_derived_claims
from correspondent.writer import CorrespondentError


def fact(team, outcomes='WDDDW', signal='UNBEATEN_SEASON_START'):
    return dict(id=team+':'+signal,subject_team=team,signal_type=signal,
        evidence={},provenance=dict(rows=[dict(fixture_id=i,outcome=o) for i,o in enumerate(outcomes)]),
        writing=dict(scorers=[],specific_goal_story=False,comparison_scope=None))


def validate(home, away, facts, sentences):
    pick=dict(fixture_id=1,picked_team=home,home=home,away=away,candidates=facts,fallback_facts=[])
    output=dict(fixture_id=1,picked_team=home,heading=f'{home} vs {away}',
                sentences=[dict(text=text,used_fact_ids=ids) for text,ids in sentences])
    return validate_fixture_output(output,pick)


class GroundingTests(unittest.TestCase):
    def test_exact_hull_false_ranking_date_rejected(self):
        f=fact('Everton',signal='BEST_DEFENCE')
        text='By 19 September 2026, Everton had conceded three goals in five league matches, joint fewest with Leeds United.'
        with self.assertRaisesRegex(CorrespondentError,'historical sample date'):
            validate('Hull City','Everton',[f],[(text,[f['id']])])

    def test_ranking_date_range_and_iso_date_rejected_but_team_stat_allowed(self):
        f=fact('Everton',signal='BEST_DEFENCE')
        for date in ('from 22 August to 19 September 2026','on 2026-09-19','by September','after their first five matches','by 19 Sept. 2026','by then'):
            with self.subTest(date=date),self.assertRaises(CorrespondentError):
                validate_derived_claims('Everton conceded three goals '+date+', joint fewest in the league.',[f])
        validate_derived_claims('Everton conceded three goals from 22 August to 19 September 2026.',[f])
        validate_derived_claims('Everton have conceded three goals, joint fewest in the current league season.',[f])

    def test_exact_villa_false_wdl_rejected(self):
        f=fact('Brentford')
        text='Brentford scored in each of five matches from 22 August to 18 September 2026 and remained unbeaten throughout, with three wins and two draws.'
        with self.assertRaisesRegex(CorrespondentError,'W-D-L count contradicts'):
            validate('Aston Villa','Brentford',[f],[(text,[f['id']])])
        validate('Aston Villa','Brentford',[f],[(text.replace('three wins and two draws','two wins and three draws'),[f['id']])])

    def test_numeric_nouns_verbs_and_zero_defeats(self):
        f=fact('Brentford')
        for text in ('Brentford recorded 2 wins, 3 draws and 0 defeats.',
                     'Brentford won twice, drawing three and losing zero.',
                     'Brentford had two victories, three draws and no losses.'):
            validate_derived_claims(text,[f])
        for text in ('Brentford won three and drew two.', 'Brentford had 1 defeat.', 'Brentford recorded 3 wins.'):
            with self.subTest(text=text),self.assertRaises(CorrespondentError):validate_derived_claims(text,[f])

    def test_explicit_structured_counts_and_singular_win(self):
        f=fact('Brentford')
        f.update(provenance={},evidence=dict(played=5,wins=2,draws=3,losses=0))
        validate_derived_claims('Brentford had two wins and three draws.',[f])
        with self.assertRaises(CorrespondentError):validate_derived_claims('Brentford had a win and four draws.',[f])
        f['evidence']['played']=6
        with self.assertRaises(CorrespondentError):validate_derived_claims('Brentford won two.',[f])

    def test_scores_are_not_wdl_counts_and_without_a_win_is_zero(self):
        for text in ('Arsenal won 1-0 on 1 August 2026.', 'Arsenal drew 2–2.', 'Arsenal recorded a 2 - 2 draw.'):
            validate_derived_claims(text,[])
        f=fact('Fulham','DDLLL')
        validate_derived_claims('Fulham began without a win, drawing twice and losing three.',[f])
        with self.assertRaises(CorrespondentError):validate_derived_claims('Fulham recorded a win.',[f])

    def test_writer_ranking_metadata_is_cutoff_bounded(self):
        from pre_match_facts import writing_metadata
        f=fact('Everton',signal='BEST_DEFENCE')
        f.update(sample={},recomputability=dict(cutoff='2026-10-07T12:48:03+00:00'))
        self.assertEqual(writing_metadata(f)['comparison_scope'],dict(kind='league_ranking_at_cutoff',as_of='2026-10-07T12:48:03+00:00'))

    def test_wdl_must_bind_to_team_and_one_sample(self):
        a,b=fact('Arsenal','WWWWW'),fact('Brentford')
        validate_derived_claims('Arsenal won all five; Brentford had two wins and three draws.',[a,b])
        with self.assertRaises(CorrespondentError):validate_derived_claims('Brentford won five.',[a,b])
        with self.assertRaises(CorrespondentError):validate_derived_claims('They won five.',[a,b])
        with self.assertRaises(CorrespondentError):validate_derived_claims('Arsenal won five.',[a,fact('Arsenal','WWW')])
        # Duplicate facts about the same complete sample do not create ambiguity.
        validate_derived_claims('Arsenal won five.',[a,deepcopy(a)])
        with self.assertRaises(CorrespondentError):validate_derived_claims('Brentford won two.',[dict(b,provenance={})])

    def test_cannot_borrow_opponents_count_or_guess_nested_subject(self):
        f=fact('Brentford')
        with self.assertRaises(CorrespondentError):
            validate('Aston Villa','Brentford',[f],[('Aston Villa recorded two wins and three draws.',[f['id']])])
        with self.assertRaises(CorrespondentError):
            validate_derived_claims('Brentford against Arsenal won five.',[f,fact('Arsenal','WWWWW')])

    def test_palace_verified_alias_only(self):
        f=fact('Crystal Palace',signal='RECENT_GOALS')
        text='Across five matches from 22 August to 20 September 2026, Palace scored six goals but conceded 11.'
        validate('Crystal Palace','Nottingham',[f],[(text,[f['id']])])
        for actor in ('Crystal','Pal','Unknown Player'):
            with self.subTest(actor=actor),self.assertRaises(CorrespondentError):
                validate('Crystal Palace','Nottingham',[f],[(text.replace('Palace scored',actor+' scored'),[f['id']])])

    def test_coventry_unambiguous_team_pronoun(self):
        f=fact('Coventry City','WLLLL')
        sentences=[('Coventry City opened the season with one win, no draws and four defeats from five matches.',[f['id']]),
                   ('They scored once across five matches between 21 August and 19 September 2026, with four games ending without a Coventry goal.',[f['id']])]
        validate('Coventry City','Newcastle',[f],sentences)
        with self.assertRaises(CorrespondentError):validate('Coventry City','Newcastle',[f],[sentences[1]])
        with self.assertRaises(CorrespondentError):
            validate('Coventry City','Newcastle',[f],[('Coventry City and Newcastle scored goals.',[f['id']]),sentences[1]])

    def goal(self,name='Matthijs de Ligt'):
        f=fact('Man United',signal='LATE_DECISIVE_GOAL')
        f['evidence']=dict(kind='equalizer')
        f['writing'].update(specific_goal_story=True,scorers=[dict(name=name,minute=90,extra_minute=6)])
        return f

    def test_both_exact_de_ligt_sentences(self):
        f=self.goal('Matthijs de Ligt\u00a0')
        for text in ('At Tottenham on 8 November 2025, Matthijs de Ligt scored for Man United at 90+6 to equalise in a 2-2 draw.',
                     'In the 8 November 2025 meeting at Tottenham, Matthijs de Ligt scored for Manchester United at 90+6 to make it 2-2.'):
            validate('Man United','Tottenham',[f],[(text,[f['id']])])

    def test_authoritative_particles_and_uncited_actor_protection(self):
        for name in ('Matthijs de Ligt','Example van der Name','Example von Name'):
            f=self.goal(name)
            validate('Man United','Tottenham',[f],[(f'{name} scored for Man United at 90+6.',[f['id']])])
        f=self.goal()
        for text in ('Unknown de Ligt scored at 90+6.',
                     'Matthijs de Ligt watched Unknown Player score; They scored at 90+6.',
                     'They scored at 90+6.', 'Matthijs de Ligt scored at 90+5.',
                     'Matthijs de Ligt scored in the 90+6th-minute.'):
            with self.subTest(text=text),self.assertRaises(CorrespondentError):
                validate('Man United','Tottenham',[f],[(text,[f['id']])])

    def test_known_player_still_needs_same_sentence_citation(self):
        goal,aggregate=self.goal(),fact('Man United')
        with self.assertRaises(CorrespondentError):
            validate('Man United','Tottenham',[goal,aggregate],[('Matthijs de Ligt scored for Man United.',[aggregate['id']])])


if __name__=='__main__':unittest.main()
