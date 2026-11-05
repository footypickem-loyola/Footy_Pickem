"""Two-league acceptance on actual Stage 2 + league-key migration, synthetic only."""
from contextlib import closing
from datetime import datetime,timedelta
from pathlib import Path
import sqlite3
import unittest
from test_foundation_league_keys import LeagueKeysTests
from test_runtime_league_auth import RuntimeAuthTests
from runtime_league_draft import LeagueDraftService


class CoexistenceTests(unittest.TestCase):
    manifest=LeagueKeysTests.manifest
    plan=LeagueKeysTests.plan
    clone=LeagueKeysTests.clone
    apply_stage2=LeagueKeysTests.apply_stage2
    apply=LeagueKeysTests.apply
    populate_preserved_history=LeagueKeysTests.populate_preserved_history
    load_app=RuntimeAuthTests.load_app

    def setUp(self):
        LeagueKeysTests.setUp(self)
        self.folder=Path(self.temp.name)
        self.values=dict(DB_PATH='sqlite:///'+str(self.path),INIT_ON_START='0',FLASK_SECRET='synthetic-no-real-secret',RUNTIME_AUTH0_ENABLED='0')
        self.d=self.load_app(self.values);self.d.app.testing=True
        self.apply()
        with self.d.SessionLocal() as db:
            week=db.get(self.d.Week,10)
            self.original_points=self.d.weekly_points_map(db,week)
            self.original_payout=self.d.payouts_for_week(db,week)
            self.assertEqual(self.original_points,{9:1,2:0,15:0})
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("UPDATE leagues SET slug='loyola-pick-em' WHERE id=1")
            db.commit()
            self.single=self.folder/'single-league-after003.db'
            with closing(sqlite3.connect(self.single)) as copy:db.backup(copy)
            self.historical=db.execute('SELECT * FROM picks ORDER BY id').fetchall()
            self.result_rows=db.execute('SELECT * FROM results ORDER BY id').fetchall()
            self.pair=db.execute('SELECT id,user_id,name,name_key FROM players WHERE id IN (9,2) ORDER BY id DESC').fetchall()
            football,round=db.execute('SELECT football_season_id,football_matchweek_id FROM fixtures WHERE id=100').fetchone()
            self.code=db.execute('SELECT code FROM seasons WHERE id=1').fetchone()[0]
            now=datetime.utcnow().isoformat();future=(datetime.utcnow()+timedelta(days=2)).isoformat(' ')
            db.execute("INSERT INTO leagues(id,slug,name,status,created_at,updated_at) VALUES(100,'synthetic-b','Synthetic B','active',?,?)",(now,now))
            for id,row in zip((100,101),self.pair):
                db.execute("INSERT INTO players(id,name,league_id,user_id,name_key,role,status) VALUES(?,?,100,?,?,'member','active')",(id,row[2],row[1],row[3]))
            db.execute("INSERT INTO seasons(id,code,name,is_active,is_archived,league_id,football_season_id) VALUES(100,?,'Synthetic B Season',1,0,100,?)",(self.code,football))
            db.execute("INSERT INTO weeks(id,season_id,number,room_code,status,football_matchweek_id) VALUES(100,100,1,'SYNTHETIC','drafting',?)",(round,))
            db.execute('INSERT INTO league_week_fixtures VALUES(100,100,1)')
            db.execute('INSERT INTO league_season_memberships(season_id,membership_id) VALUES(100,100),(100,101)')
            db.execute('INSERT INTO matchups(id,week_id,player_a_id,player_b_id,first_picker_id) VALUES(100,100,100,101,100)')
            # Add a second unplayed shared canonical fixture for the real guarded draft.
            home,away=db.execute('SELECT home_club_id,away_club_id FROM fixtures WHERE id=100').fetchone()
            db.execute("INSERT INTO fixtures(id,week_id,match_number,home,away,kickoff_utc,football_season_id,football_matchweek_id,home_club_id,away_club_id) VALUES(999,10,99,'Home FC','Away FC',?,?,?,?,?)",(future,football,round,home,away))
            db.execute('INSERT INTO league_week_fixtures VALUES(10,999,99),(100,999,2)')

    def test_duplicate_names_codes_shared_result_points_records_and_payouts(self):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("INSERT INTO picks(matchup_id,player_id,fixture_id,team,selected_club_id) SELECT 100,100,100,'Away FC',away_club_id FROM fixtures WHERE id=100")
            db.execute("UPDATE weeks SET status='finalized' WHERE id=100")
        with self.d.SessionLocal() as db:
            lo=db.get(self.d.Week,10);b=db.get(self.d.Week,100)
            self.assertEqual(self.d.weekly_points_map(db,lo),self.original_points)
            self.assertEqual(self.d.payouts_for_week(db,lo),self.original_payout)
            self.assertEqual(self.d.weekly_points_map(db,b),{100:-1,101:0})
            self.assertEqual(self.d.weekly_pick_records(db,b)[100],dict(correct=0,incorrect=1,draws=0))
            self.assertEqual(self.d.weekly_for_against(db,b)[101],{'for':0,'against':-1})
            self.assertEqual(self.d.payouts_for_week(db,b)[0]['payout'],5)
            standings=self.d.standings_through_week(db,db.get(self.d.Season,100),1)
            self.assertEqual({r['player_id'] for r in standings},{100,101})
            self.assertEqual(standings[0]['player_id'],101)
            self.assertEqual(self.d.season_totals_finalized(db,db.get(self.d.Season,100))[100]['net'],-1)
            records=self.d.club_records_for_player(db,db.get(self.d.Season,100),db.get(self.d.Player,100))
            self.assertEqual(records[0]['incorrect'],1)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT * FROM picks WHERE id IN (1,2) ORDER BY id').fetchall(),self.historical)
            self.assertEqual(db.execute('SELECT * FROM results ORDER BY id').fetchall(),self.result_rows)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM results WHERE fixture_id=100').fetchone()[0],1)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_shared_future_fixture_is_drafted_independently(self):
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute("UPDATE leagues SET status='active' WHERE id=1")
            db.execute("UPDATE seasons SET is_active=1,is_archived=0 WHERE id=1")
            db.execute("UPDATE weeks SET status='drafting' WHERE id=10")
            db.execute("UPDATE players SET status='active' WHERE id IN (9,2)")
        # Existing Loyola count=1, next turn is its second participant.
        a=LeagueDraftService(self.d,lambda db:self.pair[1][1],1,1,1,None)
        with closing(sqlite3.connect(self.path)) as db:
            home,away=db.execute('SELECT home_club_id,away_club_id FROM fixtures WHERE id=999').fetchone()
        a.club_id=home
        # Baseline may retain an enabled legacy preference; no new queue feature is used.
        with closing(sqlite3.connect(self.path)) as db,db:db.execute('DELETE FROM auto_draft_settings')
        a.command(1,None,manual=(999,'Home FC'),expected_count=1)
        b=LeagueDraftService(self.d,lambda db:self.pair[0][1],100,100,1,away)
        b.command(100,None,manual=(999,'Away FC'),expected_count=0)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT matchup_id,fixture_id FROM picks WHERE fixture_id=999 ORDER BY matchup_id').fetchall(),[(1,999),(100,999)])

    def test_scoped_duplicates_unowned_rows_and_disabled_legacy_lookup(self):
        with closing(sqlite3.connect(self.path)) as db:
            with self.assertRaises(sqlite3.IntegrityError):db.execute("INSERT INTO players(id,name) VALUES(999,'Unowned synthetic')")
            db.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO seasons(id,code,name,is_active,is_archived,league_id,football_season_id) SELECT 101,code,'Duplicate',1,0,100,football_season_id FROM seasons WHERE id=100")
            db.rollback()
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO players(id,name,user_id,league_id,name_key,status,role) SELECT 999,'Synthetic alias',user_id,100,name_key,'active','member' FROM players WHERE id=100")
            db.rollback()
        with self.d.app.test_request_context('/?season='+self.code):
            with self.d.SessionLocal() as db:
                self.d.session['player_name']=self.pair[0][2]
                self.assertEqual(self.d.current_player(db).id,self.pair[0][0])
                self.assertEqual(self.d.requested_season(db).id,1)
                from runtime_league_compat import refuse_unscoped_seed
                with self.assertRaises(ValueError):refuse_unscoped_seed(db,self.d.Season)
        client=self.d.app.test_client()
        with client.session_transaction() as state:state['player_name']=self.pair[0][2]
        page=client.get('/tab/current',query_string=dict(season=self.code,force_week=1))
        self.assertEqual(page.status_code,403)
        self.assertNotIn(b'Synthetic B Season',page.data)
        for path in ('/admin','/join','/partials/outcome-options?fixture_id=999','/pick-insight/100'):
            self.assertEqual(client.get(path).status_code,403,path)
        self.assertEqual(client.post('/set_result',data=dict(fixture_id=999,outcome='Home')).status_code,403)
        with closing(sqlite3.connect(self.path)) as db,db:db.execute("UPDATE leagues SET status='setup' WHERE id=100")
        self.assertEqual(client.get('/tab/current').status_code,403)

    def test_migrated_single_league_disabled_fallback_remains_usable(self):
        single=self.load_app(dict(self.values,DB_PATH='sqlite:///'+str(self.single)))
        single.app.testing=True
        client=single.app.test_client()
        with client.session_transaction() as state:state['player_name']=self.pair[0][2]
        page=client.get('/tab/current',query_string=dict(season=self.code,force_week=1))
        self.assertEqual(page.status_code,200)
        self.assertEqual(client.get('/join').status_code,200)


if __name__=='__main__':unittest.main()
