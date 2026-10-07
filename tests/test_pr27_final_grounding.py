"""Second-run regressions: no network, provider, persistence or source mutations."""
from copy import deepcopy
import unittest

from test_pr27_grounding import fact,validate
from correspondent.football_claim_checks import validate_derived_claims,h2h_sample
from correspondent.venue_grounding import validate_venues
from correspondent.writer import CorrespondentError


def h2h(subject='Nottingham'):
    rows=[]
    for i,(home,away,hs,aws) in enumerate([
        ('Nottingham Forest','Crystal Palace',1,0),
        ('Crystal Palace','Nottingham Forest',1,1),
        ('Crystal Palace','Nottingham Forest',1,1),
        ('Nottingham Forest','Crystal Palace',1,1)],1):
        rows.append(dict(reference_fixture_id=i,home_team=home,away_team=away,home_score=hs,away_score=aws))
    f=fact(subject,signal='H2H_UNBEATEN_RUN')
    f.update(provenance=dict(fixtures=rows),sample=dict(size=4,fixture_ids=[1,2,3,4]))
    return f


class FinalGroundingTests(unittest.TestCase):
    def test_exact_liverpool_unsupplied_venue_rejected(self):
        f=fact('Man City',signal='LATE_DECISIVE_GOAL')
        f.update(evidence=dict(kind='winner'),provenance=dict(fixtures=[dict(home_team='Liverpool',away_team='Manchester City')]))
        f['writing'].update(specific_goal_story=True,scorers=[dict(name='Erling Haaland',minute=90,extra_minute=3)])
        text='On 8 February 2026 at Anfield, Erling Haaland scored for Man City at 90+3 to complete a 2-1 win over Liverpool.'
        with self.assertRaisesRegex(CorrespondentError,'Venue name absent'):
            validate('Liverpool','Man City',[f],[(text,[f['id']])])
        f['provenance']['fixtures'][0]['venue_name']='Anfield'
        validate('Liverpool','Man City',[f],[(text,[f['id']])])

    def test_venue_rule_uses_cited_fields_not_blacklist_or_other_facts(self):
        f=fact('Arsenal')
        for name in ('Anfield','Old Trafford','the Emirates','New Example Park','St James’ Park'):
            with self.subTest(name=name),self.assertRaises(CorrespondentError):
                validate_venues('They scored at '+name+'.',[f])
        for text in ('Anfield hosted the match.','They scored inside Old Trafford.',
                     'They scored at anfield.','They won in New Example Park.',
                     'New Example Stadium hosted the match.', 'Anfield’s crowd saw the goal.',
                     'They recalled the Old Trafford meeting.'):
            with self.subTest(text=text),self.assertRaises(CorrespondentError):validate_venues(text,[f])
        f['claim']='They played at Anfield.'
        with self.assertRaises(CorrespondentError):validate_venues('They scored at Anfield.',[f])
        supplied=deepcopy(f);supplied['evidence']['venue']=dict(name='New Example Park')
        validate_venues('They scored at New Example Park.',[supplied])
        with self.assertRaises(CorrespondentError):
            validate('Arsenal','Liverpool',[f,dict(supplied,id='other')],[('Arsenal scored at New Example Park.',[f['id']])])

    def test_supported_club_locations_and_temporal_phrases_remain_allowed(self):
        f=fact('Man City');f['provenance']=dict(fixtures=[dict(home_team='Liverpool',away_team='Manchester City')])
        for text in ('City scored away at Liverpool.','At Liverpool, City scored.',
                     'They scored at 90+3.','They scored at the start.',
                     'They scored in August.','They scored in the Premier League.'):
            validate_venues(text,[f])
        validate_venues('Man City scored at away grounds.',[f])
        with self.assertRaises(CorrespondentError):validate_venues('City scored at Old Trafford.',[f])
        g=fact('Arsenal');g['provenance']['rows'][0]['home']=True
        validate_venues('Arsenal scored at home.',[g])

    def test_exact_chelsea_continuous_subject(self):
        f=fact('Bournemouth','LLDDD')
        text='Bournemouth have scored in four of their five opening league matches but remain without a win, with two defeats and three draws.'
        validate('Chelsea','Bournemouth',[f],[(text,[f['id']])])

    def test_conjunction_continuity_and_explicit_subject_switches(self):
        b,c=fact('Bournemouth','LLDDD'),fact('Chelsea','WWLDL')
        for conjunction in ('but','and','while'):
            validate_derived_claims(f'Bournemouth scored goals {conjunction} remain without a win, with two defeats and three draws.',[b])
            validate_derived_claims(f'Bournemouth had three draws {conjunction} Chelsea recorded two wins.',[b,c])
            with self.assertRaises(CorrespondentError):
                validate_derived_claims(f'Bournemouth had three draws {conjunction} Chelsea recorded three draws.',[b,c])
            with self.assertRaises(CorrespondentError):
                validate_derived_claims(f'Bournemouth had three draws {conjunction} Chelsea recorded three draws.',[b],fixture_teams=('Chelsea','Bournemouth'))
            for other in ('the visitors','Unknown Club'):
                with self.assertRaises(CorrespondentError):
                    validate_derived_claims(f'Bournemouth had three draws {conjunction} {other} recorded three draws.',[b])

    def test_exact_nottingham_bounded_h2h(self):
        f=h2h()
        text='Nottingham were unbeaten in four league meetings with Crystal Palace between 21 October 2024 and 1 February 2026, including three draws and a 1-0 win.'
        validate('Crystal Palace','Nottingham',[f],[(text,[f['id']])])

    def test_h2h_home_away_perspective_not_home_score_order(self):
        n,p=h2h(),h2h('Crystal Palace')
        validate_derived_claims('Nottingham had one win and three draws.',[n])
        validate_derived_claims('Crystal Palace had zero wins, three draws and one defeat.',[p])
        with self.assertRaises(CorrespondentError):validate_derived_claims('Crystal Palace had one win and three draws.',[p])
        # Switch the decisive fixture to a Nottingham away win; W-D-L is identical.
        row=n['provenance']['fixtures'][0]
        row.update(home_team='Crystal Palace',away_team='Nottingham Forest',home_score=0,away_score=1)
        validate_derived_claims('Nottingham had one win and three draws.',[n])
        with self.assertRaises(CorrespondentError):validate_derived_claims('Nottingham had three draws and a 0-1 defeat.',[n])
        with self.assertRaises(CorrespondentError):validate_derived_claims('Nottingham had three draws and a 2-0 win.',[n])

    def test_h2h_opponent_object_not_a_new_subject(self):
        n,p=h2h(),h2h('Crystal Palace')
        validate_derived_claims('Nottingham had four meetings with Crystal Palace, including one win and three draws.',[n],fixture_teams=('Nottingham','Crystal Palace'))
        validate_derived_claims('Nottingham had four meetings with Crystal Palace but Crystal Palace had one defeat.',[n,p])
        with self.assertRaises(CorrespondentError):
            validate_derived_claims('Nottingham had four meetings with Crystal Palace but Crystal Palace had one win.',[n,p])
        with self.assertRaises(CorrespondentError):
            validate_derived_claims('Nottingham had four meetings with Crystal Palace who had one win.',[n,p])

    def test_h2h_incomplete_unknown_duplicate_or_out_of_sample_rows_fail_closed(self):
        for change in ('missing_score','bool_score','negative_score','unknown_team','other_opponent','missing_row','extra_row','duplicate','wrong_ids','duplicate_sample_id'):
            f=h2h();rows=f['provenance']['fixtures']
            if change=='missing_score':rows[0].pop('away_score')
            if change=='bool_score':rows[0]['away_score']=False
            if change=='negative_score':rows[0]['away_score']=-1
            if change=='unknown_team':rows[0]['home_team']='Unknown'
            if change=='other_opponent':rows[0]['away_team']='Arsenal'
            if change=='missing_row':rows.pop()
            if change=='extra_row':rows.append(dict(rows[0],reference_fixture_id=5))
            if change=='duplicate':rows[1]=deepcopy(rows[0])
            if change=='wrong_ids':f['sample']['fixture_ids']=[1,2,3,99]
            if change=='duplicate_sample_id':f['sample']['fixture_ids'].append(4)
            with self.subTest(change=change),self.assertRaises(CorrespondentError):
                validate_derived_claims('Nottingham had one win and three draws.',[f])

    def test_h2h_never_adds_uncited_reference_fixtures(self):
        f=h2h();f['provenance']['fixtures']=f['provenance']['fixtures'][1:]
        f['sample']=dict(size=3,fixture_ids=[2,3,4])
        validate_derived_claims('Nottingham had three draws and no wins.',[f])
        with self.assertRaises(CorrespondentError):validate_derived_claims('Nottingham had three draws and a 1-0 win.',[f])


if __name__=='__main__':unittest.main()
