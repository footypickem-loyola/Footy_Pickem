"""Real registered Flask routes; synthetic two-league data and signed OIDC only."""
from contextlib import closing
import hashlib
import json
import sqlite3
import unittest
from unittest.mock import patch
from sqlalchemy import text
from sqlalchemy.orm import Session,aliased
from test_runtime_league_auth import RuntimeAuthTests,BASE
from runtime_league_auth import COOKIE
from runtime_legacy_boundary import ROUTES,signature,inventory


class BoundaryTests(unittest.TestCase):
    load_app=RuntimeAuthTests.load_app
    get=RuntimeAuthTests.get
    post=RuntimeAuthTests.post
    begin=RuntimeAuthTests.begin
    login=RuntimeAuthTests.login
    snapshot=RuntimeAuthTests.snapshot
    change=RuntimeAuthTests.change

    def setUp(self):
        RuntimeAuthTests.setUp(self)
        with closing(sqlite3.connect(self.game)) as db:
            db.executescript("""
            UPDATE leagues SET slug='loyola-pick-em' WHERE id=1;
            ALTER TABLE seasons ADD COLUMN league_id INTEGER;
            INSERT INTO players(id,name,league_id,status,role) VALUES(4,'Loyola Opponent',1,'active','member'),(5,'B Opponent',2,'active','member');
            INSERT INTO seasons(id,code,name,is_active,is_archived,league_id) VALUES(1,'loyola-season','Loyola Season',1,0,1),(2,'b-season','Private B Season',1,0,2);
            INSERT INTO weeks(id,season_id,number,room_code,status) VALUES(10,1,1,'SYNTHETIC','drafting'),(20,2,1,'SYNTHETIC-B','drafting');
            INSERT INTO fixtures(id,week_id,match_number,home,away) VALUES(100,10,1,'Loyola Home','Loyola Away'),(200,20,1,'Private B Home','Private B Away');
            INSERT INTO matchups(id,week_id,player_a_id,player_b_id,first_picker_id) VALUES(1000,10,1,4,1),(2000,20,2,5,2);
            INSERT INTO picks(id,matchup_id,player_id,fixture_id,team) VALUES(20,2000,2,200,'Private B Home');
            INSERT INTO results(id,fixture_id,outcome,source) VALUES(20,200,'Home','synthetic');
            """)

    def test_every_registered_route_has_an_explicit_signature_policy(self):
        actual={signature(r) for r in self.app.url_map.iter_rules()}
        self.assertEqual(actual,set(ROUTES))
        self.assertEqual(len(actual),57)
        self.assertNotIn('unclassified_deny',{r['category'] for r in inventory(self.app)})

    def test_all_private_browser_routes_reject_unauthenticated_and_name_only(self):
        paths=['/','/tab/current','/tab/open','/tab/season','/tab/stats','/tab/league',
               '/partials/fixtures/1','/partials/matchups/1','/partials/scores/1',
               '/partials/matchweek/1','/partials/outcome-options?fixture_id=100','/pick-insight/1000','/admin','/join']
        with self.client.session_transaction() as s:s['player_name']='Synthetic Player';s['is_admin']=True
        for path in paths:
            with self.subTest(path=path):self.assertEqual(self.get(path).status_code,401)
        self.assertEqual(self.client.head('/partials/fixtures/1',base_url=BASE).status_code,401)
        self.assertEqual(self.client.options('/tab/current',base_url=BASE).status_code,401)
        self.assertEqual(self.get('/static/v3/v3.css').status_code,200)
        for path in ('/pick','/auto-draft/1000','/admin/login','/set_result'):
            with self.subTest(path=path):self.assertEqual(self.post(path,{}).status_code,401)

    def test_verified_loyola_member_sees_only_loyola_through_global_views(self):
        self.login()
        with self.client.session_transaction() as s:s['player_name']='Other Player';s['is_admin']=True
        for path in ('/','/tab/current','/tab/open','/tab/season','/tab/stats','/tab/league',
                     '/partials/fixtures/1','/partials/matchups/1','/partials/scores/1','/partials/matchweek/1'):
            with self.subTest(path=path):
                response=self.get(path)
                self.assertEqual(response.status_code,200)
                for secret in (b'Private B',b'Other Player',b'B Opponent',b'Second Membership'):
                    self.assertNotIn(secret,response.data)
                self.assertEqual(response.headers['Cache-Control'],'private, no-store')

    def test_cross_league_season_fixture_player_and_indirect_ids_reject(self):
        self.login();before=self.snapshot()
        for path in ('/tab/current?season=b-season','/tab/season?season=b-season',
                     '/partials/fixtures/1?season=b-season','/partials/outcome-options?fixture_id=200',
                     '/pick-insight/2000','/tab/stats?player=2','/tab/league?opponent=2',
                     '/tab/current?league_id=2','/tab/current?season=loyola-season&season=b-season',
                     '/partials/outcome-options?fixture_id=9999999999999999999',
                     '/partials/outcome-options?fixture_id=-1'):
            with self.subTest(path=path):self.assertEqual(self.get(path).status_code,404)
        self.assertEqual(self.get('/partials/outcome-options?fixture_id=100').status_code,200)
        self.assertEqual(before,self.snapshot())

    def test_other_league_only_or_unlinked_loyola_player_gets_no_gameplay(self):
        self.wire.subject='synthetic-two';self.login()
        self.assertEqual(self.get('/auth/leagues/2').status_code,200)
        self.assertEqual(self.get('/tab/current').status_code,403)
        self.wire.subject='synthetic-one';self.login()
        self.change('UPDATE players SET user_id=NULL WHERE id=1')
        with self.client.session_transaction() as s:s['player_name']='Synthetic Player'
        self.assertEqual(self.get('/').status_code,403)
        self.assertEqual(self.get('/auth/leagues/3').status_code,200)

    def test_admin_join_and_result_writes_stay_denied_for_verified_members(self):
        self.login();self.change("UPDATE players SET role='commissioner' WHERE id=1")
        with self.client.session_transaction() as s:s['is_admin']=True
        for rule in self.app.url_map.iter_rules():
            if ROUTES[signature(rule)]!='denied_admin_or_join':continue
            path=rule.rule.replace('<int:source_id>','1')
            for method in rule.methods-{'HEAD','OPTIONS'}:
                with self.subTest(path=path,method=method):
                    self.assertEqual(self.client.open(path,method=method,base_url=BASE).status_code,403)

    def test_pick_uses_verified_player_and_rejects_cross_league_writes(self):
        self.login()
        with self.client.session_transaction() as s:s['player_name']='Other Player'
        payload=dict(season='loyola-season',week='1',matchup_id='1000',fixture_id='100',team='Loyola Home',expected_count='0')
        self.assertEqual(self.post('/pick',payload,origin='https://wrong.example.invalid').status_code,403)
        self.assertEqual(self.post('/pick',dict(payload,fixture_id='200')).status_code,404)
        self.assertEqual(self.post('/pick',dict(payload,matchup_id='2000')).status_code,404)
        self.assertEqual(self.post('/auto-draft/2000',dict(action='disable')).status_code,404)
        self.assertEqual(self.post('/pick',payload).status_code,303)
        with closing(sqlite3.connect(self.game)) as db:
            self.assertEqual(db.execute('SELECT player_id FROM picks WHERE matchup_id=1000').fetchall(),[(1,)])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM picks WHERE matchup_id=2000').fetchone()[0],1)

    def test_nested_auto_draft_fixture_ids_cannot_escape_scope(self):
        self.login();before=self.snapshot()
        for data in (dict(action='add',choice='200:Private B Home'),
                     dict(action='confirm',expected_count='0',preferences=json.dumps([dict(fixture_id=200,team='Private B Home')]))):
            with self.subTest(action=data['action']):
                self.assertEqual(self.post('/auto-draft/1000',data).status_code,400)
        self.assertEqual(self.snapshot(),before)

    def test_new_routes_and_prefix_impersonation_default_deny(self):
        # Register before the first request, as Flask requires.
        for endpoint,path in [('unclassified','/new-private'),('unclassified_task','/tasks/new-private'),('unclassified_auth','/auth/new-private')]:
            self.app.add_url_rule(path,endpoint,lambda:'LEAK')
        self.assertEqual(len([r for r in inventory(self.app) if r['category']=='unclassified_deny']),3)
        for path in ('/new-private','/tasks/new-private','/auth/new-private'):
            self.assertEqual(self.get(path).status_code,403)
        self.login()
        for path in ('/new-private','/tasks/new-private','/auth/new-private'):
            self.assertEqual(self.get(path).status_code,403)

    def test_orm_aliases_joins_and_fresh_sessions_are_scoped(self):
        # Reuse a classified route's view so the exact production guard runs.
        def probe():
            with Session(bind=self.module.engine) as db:
                player=aliased(self.module.Player)
                self.assertEqual([p.id for p in db.query(player).order_by(player.id)],[1,4])
                self.assertIsNone(db.get(self.module.Fixture,200))
                self.assertEqual([r.fixture_id for r in db.query(self.module.Result).join(self.module.Fixture)],[])
                with self.assertRaises(Exception) as caught:db.execute(text('SELECT * FROM players'))
                self.assertEqual(caught.exception.code,403)
            return 'SCOPED'
        self.app.view_functions['tab_current']=probe
        self.login();self.assertIn(b'SCOPED',self.get('/tab/current').data)

    def test_machine_credentials_are_separate_from_browser_sessions(self):
        with patch.dict('os.environ',dict(SYNC_SECRET='synthetic-service-secret',LIVE_INGEST_SECRET='synthetic-live-secret',CORRESPONDENT_INGEST_SECRET='synthetic-ingest-secret',CORRESPONDENT_AUTOMATION_SECRET='synthetic-automation-secret')):
            self.login()
            for rule in self.app.url_map.iter_rules():
                if ROUTES[signature(rule)]!='machine':continue
                path=rule.rule
                for method in rule.methods-{'HEAD','OPTIONS'}:
                    with self.subTest(path=path):
                        response=self.client.open(path,method=method,base_url=BASE)
                        self.assertEqual(response.status_code,403)
            self.assertEqual(self.client.post('/tasks/live-ingest',base_url=BASE,data='not-json',headers={'X-Live-Ingest-Secret':'synthetic-live-secret'}).status_code,415)
            # Preserve original service authentication; intercept only downstream
            # work, so no provider or scheduling action can execute in this test.
            endpoint='scheduled_sync_results';original=self.app.view_functions[endpoint]
            self.app.view_functions[endpoint]=lambda:('SERVICE_AUTHENTICATED',202)
            try:
                response=self.client.post('/tasks/sync-results',base_url=BASE,headers={'X-Sync-Secret':'synthetic-service-secret'})
                self.assertEqual(response.status_code,202)
                self.assertEqual(self.get('/tab/current',headers={'X-Sync-Secret':'synthetic-service-secret'}).status_code,200)
                self.client.delete_cookie(COOKIE)
                self.assertEqual(self.get('/tab/current',headers={'X-Sync-Secret':'synthetic-service-secret'}).status_code,401)
            finally:self.app.view_functions[endpoint]=original

    def test_auth_cookie_is_opaque_httponly_and_identity_is_server_side(self):
        _,response=self.login();cookie=self.client.get_cookie(COOKIE)
        self.assertTrue(cookie.http_only);self.assertTrue(cookie.secure)
        self.assertNotIn('.',cookie.value);self.assertNotIn('synthetic-one',cookie.value)
        with closing(sqlite3.connect(self.auth)) as db:
            row=db.execute('SELECT id,issuer,subject FROM auth_sessions WHERE subject IS NOT NULL').fetchone()
        self.assertEqual(row,(hashlib.sha256(cookie.value.encode('ascii')).hexdigest(),'https://oidc.example.invalid/','synthetic-one'))
        self.assertNotIn(b'eyJ',response.data)

    def test_disabled_mode_needs_no_new_schema(self):
        RuntimeAuthTests.test_feature_disabled_keeps_legacy_join_on_unmigrated_schema(self)
