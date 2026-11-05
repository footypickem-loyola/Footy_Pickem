"""Scoped server-rendered pick controls, using only the synthetic runtime fixture."""
from html.parser import HTMLParser
import unittest
from urllib.parse import urlsplit
from test_runtime_league_draft import LeagueDraftTests
from test_runtime_league_auth import BASE


class Forms(HTMLParser):
    def __init__(self,html):
        super().__init__();self.forms=[];self.current=None;self.feed(html)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='form':self.current=dict(action=attrs['action'],data={})
        if tag=='input' and self.current is not None:self.current['data'][attrs['name']]=attrs.get('value','')
    def handle_endtag(self,tag):
        if tag=='form':self.forms.append(self.current);self.current=None


class MatchweekUITests(unittest.TestCase):
    load_app=LeagueDraftTests.load_app
    get=LeagueDraftTests.get
    post=LeagueDraftTests.post
    begin=LeagueDraftTests.begin
    login=LeagueDraftTests.login
    snapshot=LeagueDraftTests.snapshot
    change=LeagueDraftTests.change
    submit=LeagueDraftTests.submit
    rows=LeagueDraftTests.rows
    url=LeagueDraftTests.url
    as_opponent=LeagueDraftTests.as_opponent
    setUp=LeagueDraftTests.setUp

    def view(self,league=2,season=2,week=1):
        return f'/auth/leagues/{league}/seasons/{season}/matchweeks/{week}/view'

    def send_form(self,form):
        return self.client.post(form['action'],data=form['data'],base_url=BASE,
            headers={'Origin':BASE,'Accept':'text/html'})

    def test_authoritative_state_and_visible_own_matchup(self):
        before=self.snapshot()
        data=self.get(self.view()[:-5]).json
        state=data['matchups'][0]['draft']
        self.assertEqual(state['turn_player_id'],2);self.assertTrue(state['can_pick'])
        self.assertFalse(state['locked']);self.assertEqual(state['expected_count'],0)
        page=self.get(self.view());self.assertEqual(page.status_code,200)
        self.assertIn(b'You vs B Opponent',page.data);self.assertIn(b'Your turn',page.data)
        self.assertIn(b'No picks yet',page.data);self.assertNotIn(b'Synthetic Player',page.data)
        forms=Forms(page.get_data(as_text=True)).forms
        self.assertEqual(len(forms),20)
        self.assertTrue(all(f['action']==self.url() and f['data']['expected_count']=='0' and f['data']['csrf']==self.csrf for f in forms))
        self.assertIn("style-src 'self'",page.headers['Content-Security-Policy'])
        self.assertNotIn('unsafe-inline',page.headers['Content-Security-Policy'])
        self.assertIn('no-store',page.headers['Cache-Control'])
        self.assertEqual(self.snapshot(),before)

    def test_form_submits_to_authenticated_endpoint_then_refreshes(self):
        form=Forms(self.get(self.view()).get_data(as_text=True)).forms[0]
        response=self.send_form(form);self.assertEqual(response.status_code,303)
        self.assertEqual(urlsplit(response.location).path,self.view())
        refreshed=self.get(response.location);self.assertEqual(refreshed.status_code,200)
        self.assertIn(b'Pick saved. Draft refreshed.',refreshed.data)
        self.assertIn(b'Waiting for B Opponent',refreshed.data)
        self.assertIn(b'1 of 10 picks made',refreshed.data)
        self.assertEqual(Forms(refreshed.get_data(as_text=True)).forms,[])
        state=self.get(self.view()[:-5]).json['matchups'][0]['draft']
        self.assertEqual(state['expected_count'],1);self.assertEqual(state['turn_player_id'],5)
        self.assertNotIn(100,[f['id'] for f in state['available_fixtures']])
        self.assertEqual(self.rows(),[(2,100,10)])

    def test_stale_form_refreshes_without_replay(self):
        form=Forms(self.get(self.view()).get_data(as_text=True)).forms[0]
        self.assertEqual(self.submit().status_code,201)
        response=self.send_form(form);self.assertEqual(response.status_code,303)
        self.assertIn('notice=changed',response.location)
        page=self.get(response.location)
        self.assertIn(b'Pick not accepted. The draft was refreshed',page.data)
        self.assertIn(b'Waiting for B Opponent',page.data)
        self.assertEqual(len(self.rows()),1)

    def test_locked_week_and_started_fixtures_have_no_controls(self):
        self.change("UPDATE fixtures SET kickoff_utc='2000-01-01' WHERE id=100")
        data=self.get(self.view()[:-5]).json['matchups'][0]['draft']
        self.assertNotIn(100,[f['id'] for f in data['available_fixtures']])
        forms=Forms(self.get(self.view()).get_data(as_text=True)).forms
        self.assertTrue(all(f['data']['fixture_id']!='100' for f in forms))
        self.change("UPDATE weeks SET status='finalized' WHERE id=20")
        page=self.get(self.view());self.assertIn(b'Draft locked: Week is read only',page.data)
        self.assertEqual(Forms(page.get_data(as_text=True)).forms,[])
        self.assertFalse(self.get(self.view()[:-5]).json['matchups'][0]['draft']['can_pick'])

    def test_membership_and_season_locks_match_write_rules(self):
        for change,restore in [("UPDATE leagues SET status='setup' WHERE id=2","UPDATE leagues SET status='active' WHERE id=2"),
            ("UPDATE league_season_memberships SET left_at='2026-01-01' WHERE season_id=2 AND membership_id=5","UPDATE league_season_memberships SET left_at=NULL WHERE season_id=2 AND membership_id=5"),
            ("UPDATE seasons SET is_archived=1 WHERE id=2","UPDATE seasons SET is_archived=0 WHERE id=2")]:
            self.change(change)
            self.assertTrue(self.get(self.view()[:-5]).json['matchups'][0]['draft']['locked'])
            self.assertEqual(Forms(self.get(self.view()).get_data(as_text=True)).forms,[])
            self.assertEqual(self.submit().status_code,409);self.change(restore)

    def test_unauthorized_cross_league_and_selector_refusal(self):
        self.assertEqual(self.get(self.view(1,1)).status_code,404)
        self.assertEqual(self.get(self.view(2,1)).status_code,404)
        self.assertEqual(self.get(self.view()+'?fixture_id=300').status_code,400)
        self.assertEqual(self.get(self.view()+'?notice=arbitrary').status_code,400)
        self.assertEqual(self.get(self.view(),client=self.app.test_client()).status_code,401)
        self.assertEqual(self.get('/tab/current').status_code,403)
        self.change("UPDATE players SET status='inactive' WHERE id=2")
        self.assertEqual(self.get(self.view()).status_code,404)

    def test_waiting_member_and_unscheduled_member_have_no_forms(self):
        self.as_opponent()
        page=self.get(self.view());self.assertIn(b'Waiting for Other Player',page.data)
        self.assertEqual(Forms(page.get_data(as_text=True)).forms,[])
        self.change('UPDATE players SET user_id=NULL WHERE id=5')
        self.change("INSERT INTO players(id,name,user_id,league_id,status,role) VALUES(6,'Synthetic Spectator',1,2,'active','member')")
        page=self.get(self.view());self.assertIn(b'No matchup scheduled for you',page.data)
        self.assertEqual(Forms(page.get_data(as_text=True)).forms,[])

    def test_names_and_team_labels_are_escaped_and_mobile_styles_present(self):
        self.change("UPDATE players SET name='<script>alert(1)</script>' WHERE id=5")
        page=self.get(self.view());self.assertNotIn(b'<script>',page.data)
        self.assertIn(b'&lt;script&gt;',page.data);self.assertIn(b'name="viewport"',page.data)
        css=self.get('/static/runtime_league_matchweek.css')
        self.assertEqual(css.status_code,200);self.assertIn(b'@media(max-width:420px)',css.data)
        self.assertIn(b'min-height:48px',css.data)

    def test_disabled_legacy_path_unchanged_and_no_new_view(self):
        LeagueDraftTests.test_disabled_mode_legacy_http_pick_and_new_route_absent(self)
        module=self.load_app(dict(self.values,RUNTIME_AUTH0_ENABLED='0'))
        self.assertEqual(module.app.test_client().get(self.view()).status_code,404)

if __name__=='__main__':unittest.main()
