"""Real Flask navigation with only synthetic verified multi-league memberships."""
from html.parser import HTMLParser
import unittest
from test_runtime_league_draft import LeagueDraftTests


class Links(HTMLParser):
    def __init__(self,html):
        super().__init__();self.urls=[];self.feed(html)
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.urls.append(dict(attrs)['href'])


class NavigationTests(unittest.TestCase):
    load_app=LeagueDraftTests.load_app
    get=LeagueDraftTests.get
    post=LeagueDraftTests.post
    begin=LeagueDraftTests.begin
    login=LeagueDraftTests.login
    snapshot=LeagueDraftTests.snapshot
    change=LeagueDraftTests.change
    as_opponent=LeagueDraftTests.as_opponent
    setUp=LeagueDraftTests.setUp
    url=LeagueDraftTests.url

    def html(self,path,client=None):return self.get(path,client=client,headers={'Accept':'text/html'})
    def links(self,response):return Links(response.get_data(as_text=True)).urls

    def two_leagues(self):
        self.change("UPDATE leagues SET status='inactive' WHERE id=3")
        self.as_opponent()  # Verified user 1 has Loyola membership 1 and B membership 5.

    def test_login_to_my_leagues_to_season_to_matchweek(self):
        self.two_leagues();_,callback=self.login()
        self.assertEqual(callback.location,'/auth/leagues')
        before=self.snapshot()
        leagues=self.html(callback.location);self.assertEqual(leagues.status_code,200)
        self.assertIn('/auth/leagues/1',self.links(leagues));self.assertIn('/auth/leagues/2',self.links(leagues))
        self.assertNotIn('/auth/leagues/3',self.links(leagues))
        seasons=self.html('/auth/leagues/1');self.assertIn('/auth/leagues/1/seasons/1',self.links(seasons))
        weeks=self.html('/auth/leagues/1/seasons/1')
        self.assertIn(b'Continue to Matchweek 1',weeks.data)
        view='/auth/leagues/1/seasons/1/matchweeks/1/view'
        self.assertIn(view,self.links(weeks))
        page=self.html(view);self.assertEqual(page.status_code,200)
        self.assertIn(b'You vs Loyola Opponent',page.data);self.assertIn(b'Your turn',page.data)
        self.assertIn('/auth/leagues',self.links(page))
        self.assertEqual(self.snapshot(),before)

    def test_multi_league_switching_never_changes_authority(self):
        self.two_leagues()
        for league,season,opponent in [(1,1,b'Loyola Opponent'),(2,2,b'Other Player'),(1,1,b'Loyola Opponent')]:
            page=self.html(f'/auth/leagues/{league}/seasons/{season}/matchweeks/1/view')
            self.assertEqual(page.status_code,200);self.assertIn(opponent,page.data)
            self.assertIn(f'/auth/leagues/{league}/seasons/{season}',self.links(page))
        self.assertEqual(self.html('/auth/leagues/2/seasons/1').status_code,404)
        self.assertEqual(self.html('/tab/current?season=b-season').status_code,404)

    def test_browser_and_json_clients_keep_separate_contracts(self):
        self.assertTrue(self.html('/auth/leagues').mimetype=='text/html')
        api=self.get('/auth/leagues');self.assertEqual(api.mimetype,'application/json')
        self.assertEqual([l['id'] for l in api.json['leagues']],[2])
        explicit=self.get('/auth/leagues',headers={'Accept':'application/json'})
        self.assertEqual(api.json,explicit.json)
        self.assertEqual(self.get('/auth/leagues/2').json['leagues'][0]['id'],2)
        self.assertIn('no-store',self.html('/auth/leagues').headers['Cache-Control'])
        self.assertIn("style-src 'self'",self.html('/auth/leagues').headers['Content-Security-Policy'])

    def test_direct_links_cannot_grant_membership(self):
        for path in ('/auth/leagues/1','/auth/leagues/1/seasons/1','/auth/leagues/1/seasons/1/matchweeks/1/view',
                     '/auth/leagues/2/seasons/1','/auth/leagues/2/seasons/999','/auth/leagues/2/seasons/2/matchweeks/999/view'):
            self.assertEqual(self.html(path).status_code,404,path)
        self.assertEqual(self.html('/auth/leagues/2/seasons/2',client=self.app.test_client()).status_code,401)

    def test_membership_revocation_refuses_remembered_urls(self):
        self.assertEqual(self.html('/auth/leagues/2/seasons/2').status_code,200)
        self.change("UPDATE players SET left_at='2026-01-01' WHERE id=2")
        for path in ('/auth/leagues/2','/auth/leagues/2/seasons/2','/auth/leagues/2/seasons/2/matchweeks/1/view'):
            self.assertEqual(self.html(path).status_code,404)
        listing=self.html('/auth/leagues');self.assertIn(b'No active league memberships',listing.data)
        self.assertNotIn('/auth/leagues/2',self.links(listing))

    def test_inactive_membership_account_and_league_refuse(self):
        for change,restore in [("UPDATE players SET status='inactive' WHERE id=2","UPDATE players SET status='active' WHERE id=2"),
            ("UPDATE leagues SET status='archived' WHERE id=2","UPDATE leagues SET status='active' WHERE id=2")]:
            self.change(change);self.assertEqual(self.html('/auth/leagues/2/seasons/2').status_code,404);self.change(restore)
        self.change("UPDATE users SET status='disabled' WHERE id=2")
        self.assertEqual(self.html('/auth/leagues').status_code,403)
        self.assertEqual(self.html('/auth/leagues/2/seasons/2').status_code,403)

    def test_inactive_and_archived_seasons_are_not_navigation_targets(self):
        for change,restore in [("UPDATE seasons SET is_active=0 WHERE id=2","UPDATE seasons SET is_active=1 WHERE id=2"),
            ("UPDATE seasons SET is_archived=1 WHERE id=2","UPDATE seasons SET is_archived=0 WHERE id=2")]:
            self.change(change)
            self.assertNotIn('/auth/leagues/2/seasons/2',self.links(self.html('/auth/leagues/2')))
            self.assertEqual(self.html('/auth/leagues/2/seasons/2').status_code,404)
            # Previously supported direct historical read remains read-only.
            page=self.html('/auth/leagues/2/seasons/2/matchweeks/1/view')
            self.assertEqual(page.status_code,200);self.assertIn(b'Draft locked',page.data)
            self.change(restore)

    def test_multiple_unfinished_weeks_require_explicit_choice(self):
        self.change('INSERT INTO football_matchweeks(id,football_season_id,number) VALUES(102,1,2)')
        self.change("INSERT INTO weeks(id,season_id,number,room_code,status,football_matchweek_id) VALUES(21,2,2,'SYNTHETIC-NEXT','drafting',102)")
        page=self.html('/auth/leagues/2/seasons/2')
        self.assertNotIn(b'Continue to Matchweek',page.data)
        self.assertIn('/auth/leagues/2/seasons/2/matchweeks/2/view',self.links(page))
        self.change("UPDATE weeks SET status='finalized' WHERE id=20")
        self.assertIn(b'Continue to Matchweek 2',self.html('/auth/leagues/2/seasons/2').data)
        self.change("UPDATE weeks SET status='finalized' WHERE id=21")
        page=self.html('/auth/leagues/2/seasons/2')
        self.assertNotIn(b'Continue to Matchweek',page.data);self.assertIn(b'Finalized',page.data)

    def test_inconsistent_canonical_relationships_fail_closed(self):
        self.change('UPDATE weeks SET football_matchweek_id=201 WHERE id=20')
        self.assertEqual(self.html('/auth/leagues/2/seasons/2').status_code,409)
        self.change('UPDATE weeks SET football_matchweek_id=101 WHERE id=20')
        self.change('INSERT INTO league_week_fixtures VALUES(20,300,99)')
        self.assertEqual(self.html('/auth/leagues/2/seasons/2').status_code,409)
        self.change('DELETE FROM league_week_fixtures WHERE week_id=20 AND fixture_id=300')
        self.change('UPDATE seasons SET football_season_id=NULL WHERE id=2')
        self.assertEqual(self.html('/auth/leagues/2/seasons/2').status_code,409)
        self.assertNotIn('/auth/leagues/2/seasons/2',self.links(self.html('/auth/leagues/2')))

    def test_invalid_selectors_cannot_override_navigation_context(self):
        for path in ('/auth/leagues/2?league_id=1','/auth/leagues/2/seasons/2?season_id=1',
                     '/auth/leagues/2/seasons/2?week=30','/auth/leagues/2/seasons/2?next=https://foreign.example.invalid'):
            self.assertIn(self.html(path).status_code,(400,403))
        for path in (f'/auth/leagues/{2**64}',f'/auth/leagues/2/seasons/{2**64}'):
            self.assertEqual(self.html(path).status_code,404)

    def test_empty_schedules_setup_and_escaped_names(self):
        self.change("UPDATE leagues SET name='<script>bad</script>',status='setup' WHERE id=2")
        listing=self.html('/auth/leagues');self.assertNotIn(b'<script>',listing.data)
        self.assertIn(b'&lt;script&gt;',listing.data);self.assertIn(b'Setup',listing.data)
        self.change('DELETE FROM picks');self.change('DELETE FROM league_week_fixtures WHERE week_id=20')
        self.change('DELETE FROM auto_draft_preferences');self.change('DELETE FROM auto_draft_settings')
        self.change('DELETE FROM matchups WHERE week_id=20');self.change('DELETE FROM fixtures WHERE week_id=20')
        self.change('DELETE FROM weeks WHERE id=20')
        self.assertIn(b'No Matchweeks scheduled yet',self.html('/auth/leagues/2/seasons/2').data)

    def test_disabled_mode_keeps_legacy_experience_and_has_no_navigation(self):
        LeagueDraftTests.test_disabled_mode_legacy_http_pick_and_new_route_absent(self)
        module=self.load_app(dict(self.values,RUNTIME_AUTH0_ENABLED='0'))
        client=module.app.test_client()
        for path in ('/auth/leagues','/auth/leagues/2/seasons/2'):
            self.assertEqual(client.get(path).status_code,404)

if __name__=='__main__':unittest.main()
