"""Authenticated Matchweek API in the real app, with shared synthetic fixtures."""
from contextlib import closing
import sqlite3
import unittest
import test_runtime_legacy_boundary as boundary_fixture
from test_runtime_league_auth import RuntimeAuthTests,BASE
from foundation_schema import TABLE_DDL


class MatchweekTests(unittest.TestCase):
    load_app=RuntimeAuthTests.load_app
    get=RuntimeAuthTests.get
    post=RuntimeAuthTests.post
    begin=RuntimeAuthTests.begin
    login=RuntimeAuthTests.login
    snapshot=RuntimeAuthTests.snapshot
    change=RuntimeAuthTests.change

    def setUp(self):
        boundary_fixture.BoundaryTests.setUp(self)
        names={'competitions','football_seasons','football_matchweeks','clubs','football_season_clubs',
               'league_season_memberships','league_week_fixtures'}
        with closing(sqlite3.connect(self.game)) as db:
            for ddl in TABLE_DDL:
                if ddl.split()[2] in names:db.execute(ddl)
            db.executescript("""
            ALTER TABLE seasons ADD COLUMN football_season_id INTEGER;
            ALTER TABLE weeks ADD COLUMN football_matchweek_id INTEGER;
            ALTER TABLE fixtures ADD COLUMN football_season_id INTEGER;
            ALTER TABLE fixtures ADD COLUMN football_matchweek_id INTEGER;
            ALTER TABLE fixtures ADD COLUMN home_club_id INTEGER;
            ALTER TABLE fixtures ADD COLUMN away_club_id INTEGER;
            ALTER TABLE picks ADD COLUMN selected_club_id INTEGER;
            INSERT INTO competitions(id,code,name) VALUES(1,'SYNTH','Synthetic Competition');
            INSERT INTO football_seasons(id,competition_id,season_key,name,start_year) VALUES(1,1,'shared','Shared Football Season',2026),(2,1,'foreign','Foreign Football Season',2027);
            INSERT INTO football_matchweeks(id,football_season_id,number) VALUES(101,1,1),(201,2,1);
            INSERT INTO clubs(id,name) VALUES(10,'Loyola Home'),(20,'Loyola Away'),(30,'Private B Home'),(40,'Private B Away');
            INSERT INTO football_season_clubs VALUES(1,10),(1,20),(1,30),(1,40),(2,30),(2,40);
            UPDATE seasons SET football_season_id=1;
            UPDATE weeks SET football_matchweek_id=101;
            UPDATE fixtures SET football_season_id=1,football_matchweek_id=101;
            UPDATE fixtures SET home_club_id=10,away_club_id=20 WHERE id=100;
            UPDATE fixtures SET home_club_id=30,away_club_id=40 WHERE id=200;
            UPDATE picks SET selected_club_id=30 WHERE id=20;
            INSERT INTO league_season_memberships(season_id,membership_id) VALUES(1,1),(1,4),(2,2),(2,5);
            INSERT INTO league_week_fixtures VALUES(10,100,1),(20,100,1),(20,200,2);
            INSERT INTO picks(id,matchup_id,player_id,fixture_id,team,selected_club_id) VALUES(10,1000,1,100,'Loyola Home',10),(21,2000,5,100,'Loyola Away',20);
            INSERT INTO results(id,fixture_id,outcome,source,home_score,away_score) VALUES(10,100,'Home','synthetic',2,1);
            INSERT INTO seasons(id,code,name,is_active,is_archived,league_id,football_season_id) VALUES(3,'foreign-season','Foreign Season',0,1,3,2);
            INSERT INTO weeks(id,season_id,number,room_code,status,football_matchweek_id) VALUES(30,3,1,'FOREIGN','finalized',201);
            INSERT INTO fixtures(id,week_id,match_number,home,away,football_season_id,football_matchweek_id,home_club_id,away_club_id) VALUES(300,30,1,'Foreign Home','Foreign Away',2,201,30,40);
            """)

    def url(self,league=2,season=2,week=1):
        return f'/auth/leagues/{league}/seasons/{season}/matchweeks/{week}'

    def test_league_b_reads_shared_slate_and_own_picks_without_writes(self):
        self.wire.subject='synthetic-two';before=self.snapshot();self.login()
        response=self.get(self.url());self.assertEqual(response.status_code,200)
        data=response.json
        self.assertTrue(data['read_only']);self.assertEqual(data['viewer']['membership_id'],2)
        self.assertEqual(data['week']['id'],20);self.assertEqual([f['id'] for f in data['fixtures']],[100,200])
        self.assertEqual(data['fixtures'][0]['result']['outcome'],'Home')
        self.assertEqual(data['fixtures'][0]['result']['home_score'],2)
        self.assertEqual([m['id'] for m in data['matchups']],[2000])
        self.assertEqual([p['id'] for p in data['matchups'][0]['picks']],[20,21])
        self.assertEqual({p['id'] for p in data['matchups'][0]['players']},{2,5})
        self.assertNotIn(b'Synthetic Player',response.data)
        self.assertEqual(before,self.snapshot())
        with closing(sqlite3.connect(self.game)) as db:
            self.assertEqual(db.execute('SELECT week_id FROM fixtures WHERE id=100').fetchone()[0],10)
        self.assertEqual(response.headers['Cache-Control'],'private, no-store')
        self.assertEqual(self.get('/tab/current').status_code,403)

    def test_loyola_reads_only_its_slate_matchups_and_picks(self):
        self.login();response=self.get(self.url(1,1));self.assertEqual(response.status_code,200)
        self.assertEqual([f['id'] for f in response.json['fixtures']],[100])
        self.assertEqual([m['id'] for m in response.json['matchups']],[1000])
        self.assertEqual([p['id'] for p in response.json['matchups'][0]['picks']],[10])
        self.assertNotIn(b'Other Player',response.data)
        self.assertNotIn(b'Private B',response.data)
        self.assertEqual(self.get(self.url()).status_code,404)

    def test_unauthenticated_and_legacy_name_sessions_cannot_view(self):
        self.assertEqual(self.get(self.url()).status_code,401)
        with self.client.session_transaction() as s:s['player_name']='Other Player';s['user_id']=2
        self.assertEqual(self.get(self.url()).status_code,401)

    def test_foreign_season_week_and_query_selectors_refuse(self):
        self.wire.subject='synthetic-two';self.login()
        for path in (self.url(2,1),self.url(2,3),self.url(1,1),self.url(2,2,2),self.url(2,999),self.url(2,2,2**64)):
            with self.subTest(path=path):self.assertEqual(self.get(path).status_code,404)
        for key in ('season','season_id','matchup_id','fixture_id','player','player_id','league_id'):
            with self.subTest(key=key):self.assertEqual(self.get(self.url()+'?'+key+'=1').status_code,400)
        self.assertEqual(self.client.get(self.url(),base_url=BASE,json={'fixture_id':300}).status_code,400)

    def test_foreign_roster_matchup_pick_and_fixture_edges_fail_closed(self):
        self.wire.subject='synthetic-two';self.login()
        cases=[("UPDATE matchups SET player_a_id=1 WHERE id=2000","UPDATE matchups SET player_a_id=2 WHERE id=2000"),
               ("UPDATE picks SET player_id=1 WHERE id=20","UPDATE picks SET player_id=2 WHERE id=20"),
               ("UPDATE picks SET fixture_id=300 WHERE id=20","UPDATE picks SET fixture_id=200 WHERE id=20"),
               ("UPDATE picks SET selected_club_id=10 WHERE id=20","UPDATE picks SET selected_club_id=30 WHERE id=20"),
               ("UPDATE league_season_memberships SET membership_id=4 WHERE season_id=2 AND membership_id=5","UPDATE league_season_memberships SET membership_id=5 WHERE season_id=2 AND membership_id=4"),
               ("INSERT INTO league_week_fixtures VALUES(20,300,3)","DELETE FROM league_week_fixtures WHERE week_id=20 AND fixture_id=300")]
        for change,restore in cases:
            with self.subTest(change=change):
                self.change(change)
                response=self.get(self.url());self.assertEqual(response.status_code,409)
                self.assertNotIn(b'Synthetic Player',response.data);self.assertNotIn(b'Foreign Home',response.data)
                self.change(restore)
        self.assertEqual(self.get(self.url()).status_code,200)

    def test_inactive_or_left_membership_and_disabled_account_refuse(self):
        self.wire.subject='synthetic-two';self.login()
        self.change("UPDATE players SET status='inactive' WHERE id=2")
        self.assertEqual(self.get(self.url()).status_code,404)
        self.change("UPDATE players SET status='active',left_at='2026-01-01' WHERE id=2")
        self.assertEqual(self.get(self.url()).status_code,404)
        self.change("UPDATE players SET left_at=NULL WHERE id=2")
        self.change("UPDATE users SET status='disabled' WHERE id=2")
        self.assertEqual(self.get(self.url()).status_code,403)

    def test_multi_member_uses_selected_membership_without_switching_legacy(self):
        self.change("INSERT INTO players(id,name,user_id,league_id,status,role) VALUES(6,'Synthetic Second League Membership',1,2,'active','member')")
        self.login();response=self.get(self.url());self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['viewer']['membership_id'],6)
        self.assertEqual(self.get(self.url(1,1)).json['viewer']['membership_id'],1)
        legacy=self.get('/tab/current');self.assertEqual(legacy.status_code,200)
        self.assertNotIn(b'Other Player',legacy.data)
        self.assertEqual(self.get('/tab/current?season=b-season').status_code,404)

    def test_get_is_read_only_and_legacy_null_pick_club_is_preserved(self):
        self.wire.subject='synthetic-two';self.login()
        self.change('UPDATE picks SET selected_club_id=NULL WHERE id=21')
        before=self.snapshot()
        response=self.get(self.url());self.assertEqual(response.status_code,200)
        self.assertIsNone(response.json['matchups'][0]['picks'][1]['selected_club_id'])
        self.assertEqual(self.post(self.url(),{}).status_code,405)
        self.assertEqual(before,self.snapshot())

    def test_missing_canonical_link_is_not_replaced_by_legacy_week_lookup(self):
        self.wire.subject='synthetic-two';self.login()
        self.change('UPDATE weeks SET football_matchweek_id=NULL WHERE id=20')
        self.assertEqual(self.get(self.url()).status_code,409)
        self.change('UPDATE weeks SET football_matchweek_id=201 WHERE id=20')
        self.assertEqual(self.get(self.url()).status_code,409)

    def test_disabled_mode_keeps_legacy_join_and_has_no_matchweek_api(self):
        RuntimeAuthTests.test_feature_disabled_keeps_legacy_join_on_unmigrated_schema(self)
        values=dict(self.values,RUNTIME_AUTH0_ENABLED='0',DB_PATH='sqlite:///'+str(self.folder/'cold-disabled.db'))
        module=self.load_app(values);module.app.testing=True
        self.assertEqual(module.app.test_client().get(self.url()).status_code,404)

if __name__=='__main__':unittest.main()
