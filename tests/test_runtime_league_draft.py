"""Real Flask scoped manual drafting, synthetic accounts and game data only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3
import threading
import unittest
from test_runtime_matchweek import MatchweekTests
from test_runtime_league_auth import BASE
from runtime_league_auth import COOKIE
from urllib.parse import urlsplit


class LeagueDraftTests(unittest.TestCase):
    load_app=MatchweekTests.load_app
    get=MatchweekTests.get
    post=MatchweekTests.post
    begin=MatchweekTests.begin
    login=MatchweekTests.login
    snapshot=MatchweekTests.snapshot
    change=MatchweekTests.change

    def setUp(self):
        MatchweekTests.setUp(self)
        self.change('DELETE FROM picks');self.change('DELETE FROM results')
        self.change("UPDATE leagues SET status='active'")
        self.change('UPDATE players SET user_id=1 WHERE id=5')
        self.change("INSERT INTO user_identities(user_id,issuer,subject) VALUES(1,'https://oidc.example.invalid/','synthetic-opponent')")
        future=(datetime.now(timezone.utc)+timedelta(days=2)).replace(tzinfo=None).isoformat(' ')
        self.change('UPDATE fixtures SET kickoff_utc=?',(future,))
        for i in range(3,11):
            self.change("INSERT INTO fixtures(id,week_id,match_number,home,away,kickoff_utc,football_season_id,football_matchweek_id,home_club_id,away_club_id) VALUES(?,10,?,'Loyola Home','Loyola Away',?,1,101,10,20)",(100+i,i,future))
            self.change('INSERT INTO league_week_fixtures VALUES(20,?,?)',(100+i,i))
        self.wire.subject='synthetic-two';self.login()
        self.csrf=self.get('/auth/leagues').json['logout_csrf']

    def url(self,league=2,season=2,week=1,matchup=2000):
        return f'/auth/leagues/{league}/seasons/{season}/matchweeks/{week}/matchups/{matchup}/picks'

    def submit(self,fixture=100,team='Loyola Home',club=10,count=0,path=None,**extra):
        data=dict(fixture_id=str(fixture),team=team,selected_club_id=str(club),expected_count=str(count),csrf=self.csrf)
        data.update(extra)
        return self.post(path or self.url(),data)

    def rows(self):
        with closing(sqlite3.connect(self.game)) as db:
            return db.execute('SELECT player_id,fixture_id,selected_club_id FROM picks WHERE matchup_id=2000 ORDER BY id').fetchall()

    def as_opponent(self):
        self.wire.subject='synthetic-opponent';self.login()
        self.csrf=self.get('/auth/leagues').json['logout_csrf']

    def test_success_snake_order_and_club_links(self):
        self.assertEqual(self.submit().status_code,201)
        self.assertEqual(self.submit(103,count=1).status_code,400)
        self.as_opponent()
        self.assertEqual(self.submit(103,count=1).status_code,201)
        self.assertEqual(self.submit(104,count=2).status_code,201)
        self.assertEqual(self.submit(105,count=3).status_code,400)
        self.wire.subject='synthetic-two';self.login();self.csrf=self.get('/auth/leagues').json['logout_csrf']
        self.assertEqual(self.submit(105,count=3).status_code,201)
        self.assertEqual([r[0] for r in self.rows()],[2,5,5,2])
        self.assertTrue(all(r[2]==10 for r in self.rows()))

    def test_unauthenticated_csrf_origin_and_legacy_names_refuse(self):
        client=self.app.test_client()
        with client.session_transaction() as s:s['player_name']='Other Player'
        data=dict(fixture_id='100',team='Loyola Home',selected_club_id='10',expected_count='0',csrf=self.csrf)
        self.assertEqual(self.post(self.url(),data,client=client).status_code,401)
        self.assertEqual(self.submit(csrf='wrong').status_code,403)
        self.assertEqual(self.client.post(self.url(),data=data,base_url=BASE).status_code,403)
        self.assertEqual(self.post(self.url(),data,origin='https://foreign.example.invalid').status_code,403)
        self.assertEqual(self.rows(),[])

    def test_foreign_selectors_and_nonparticipant_refuse_without_writes(self):
        before=self.snapshot()
        for path in (self.url(1,1,1,1000),self.url(season=1),self.url(matchup=1000),self.url(week=2),self.url(matchup=999),self.url(league=2**64)):
            with self.subTest(path=path):self.assertEqual(self.submit(path=path).status_code,404)
        self.assertEqual(self.submit(300).status_code,404)
        for key in ('player_id','league_id','matchup_id','season_id'):
            self.assertEqual(self.submit(**{key:'1'}).status_code,400)
        self.change("INSERT INTO players(id,name,user_id,league_id,status,role) VALUES(6,'Synthetic Spectator',2,2,'active','member')")
        self.change('UPDATE players SET user_id=NULL WHERE id=2')
        self.assertEqual(self.submit().status_code,403)
        self.change('DELETE FROM players WHERE id=6');self.change('UPDATE players SET user_id=2 WHERE id=2')
        self.assertEqual(self.snapshot(),before)

    def test_duplicates_stale_version_bad_team_and_club(self):
        for team,club in (('Draw',10),('Loyola Away',10),('Loyola Home',30)):
            self.assertEqual(self.submit(team=team,club=club).status_code,400)
        self.assertEqual(self.submit().status_code,201)
        self.assertEqual(self.submit().status_code,409)
        self.as_opponent();self.assertEqual(self.submit(count=1).status_code,400)
        self.assertEqual(len(self.rows()),1)

    def test_shared_fixture_picks_in_other_league_do_not_block(self):
        self.change("INSERT INTO picks(matchup_id,player_id,fixture_id,team,selected_club_id) VALUES(1000,1,100,'Loyola Away',20)")
        self.assertEqual(self.submit().status_code,201)
        self.assertEqual(self.rows(),[(2,100,10)])
        self.assertEqual(self.get('/tab/current').status_code,403)

    def test_simultaneous_same_player_double_turn_has_single_winner(self):
        self.assertEqual(self.submit().status_code,201)
        self.as_opponent()  # Same player owns turns 1 and 2; expected_count is essential.
        sid=self.client.get_cookie(COOKIE,domain=urlsplit(BASE).hostname).value
        barrier=threading.Barrier(2)
        expected=1
        def send(fixture):
            client=self.app.test_client();client.set_cookie(COOKIE,sid,domain=urlsplit(BASE).hostname,secure=True)
            barrier.wait()
            return self.post(self.url(),dict(fixture_id=str(fixture),team='Loyola Home',selected_club_id='10',expected_count=str(expected),csrf=self.csrf),client=client).status_code
        with ThreadPoolExecutor(2) as pool:statuses=list(pool.map(send,[103,104]))
        self.assertEqual(sorted(statuses),[201,409]);self.assertEqual(len(self.rows()),2)
        expected=2
        with ThreadPoolExecutor(2) as pool:statuses=list(pool.map(send,[105,105]))
        self.assertEqual(sorted(statuses),[201,409]);self.assertEqual(len(self.rows()),3)

    def test_kickoff_missing_started_and_result_locks(self):
        for kickoff in (None,'2000-01-01 12:00:00'):
            self.change('UPDATE fixtures SET kickoff_utc=? WHERE id=100',(kickoff,))
            self.assertEqual(self.submit().status_code,409)
        self.change("UPDATE fixtures SET kickoff_utc='2099-01-01 12:00:00' WHERE id=100")
        self.change("INSERT INTO results(fixture_id,outcome,source) VALUES(100,'Home','synthetic')")
        self.assertEqual(self.submit().status_code,409);self.assertEqual(self.rows(),[])

    def test_draft_season_and_membership_locks(self):
        pairs=[("UPDATE weeks SET status='finalized' WHERE id=20","UPDATE weeks SET status='drafting' WHERE id=20"),
               ("UPDATE weeks SET status='provisional' WHERE id=20","UPDATE weeks SET status='drafting' WHERE id=20"),
               ("UPDATE seasons SET is_archived=1 WHERE id=2","UPDATE seasons SET is_archived=0 WHERE id=2"),
               ("UPDATE seasons SET is_active=0 WHERE id=2","UPDATE seasons SET is_active=1 WHERE id=2"),
               ("UPDATE leagues SET status='setup' WHERE id=2","UPDATE leagues SET status='active' WHERE id=2"),
               ("UPDATE league_season_memberships SET left_at='2026-01-01' WHERE season_id=2 AND membership_id=5","UPDATE league_season_memberships SET left_at=NULL WHERE season_id=2 AND membership_id=5")]
        for change,restore in pairs:
            self.change(change);self.assertEqual(self.submit().status_code,409);self.change(restore)
        self.change("UPDATE players SET status='inactive' WHERE id=2")
        self.assertEqual(self.submit().status_code,404);self.assertEqual(self.rows(),[])

    def test_foundation_failure_rolls_back_legacy_insert(self):
        self.change("CREATE TRIGGER synthetic_failure BEFORE UPDATE OF selected_club_id ON picks BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        self.assertEqual(self.submit().status_code,409);self.assertEqual(self.rows(),[])

    def test_auto_queue_uses_scoped_slate_and_canonical_club(self):
        self.change('INSERT INTO auto_draft_settings(matchup_id,player_id,enabled) VALUES(2000,5,1)')
        self.change("INSERT INTO auto_draft_preferences(matchup_id,player_id,fixture_id,team,priority) VALUES(2000,5,103,'Loyola Away',0),(2000,5,300,'Foreign Home',1)")
        response=self.submit();self.assertEqual(response.status_code,201)
        self.assertEqual(response.json['auto_draft']['count'],1)
        self.assertEqual(self.rows(),[(2,100,10),(5,103,20)])

    def test_complete_draft_cannot_accept_eleventh_pick(self):
        order=[2,5,5,2,2,5,5,2,2,5]
        for player,fixture in zip(order,[100,200,*range(103,111)]):
            club=30 if fixture==200 else 10;team='Private B Home' if fixture==200 else 'Loyola Home'
            self.change('INSERT INTO picks(matchup_id,player_id,fixture_id,team,selected_club_id) VALUES(2000,?,?,?,?)',(player,fixture,team,club))
        self.assertEqual(self.submit(110,count=9).status_code,409)
        self.assertEqual(len(self.rows()),10)

    def test_legacy_draft_engine_default_path_unchanged(self):
        from auto_draft import DraftService
        self.change("INSERT INTO fixtures(id,week_id,match_number,home,away) VALUES(900,10,90,'Synthetic Legacy Home','Synthetic Legacy Away')")
        # Direct legacy service still accepts the old shape without foundation club data.
        result=DraftService(self.module).command(1000,1,manual=(900,'Synthetic Legacy Home'),expected_count=0)
        self.assertEqual(result['count'],0)
        with closing(sqlite3.connect(self.game)) as db:
            self.assertEqual(db.execute('SELECT selected_club_id FROM picks WHERE fixture_id=900').fetchone()[0],None)

    def test_disabled_mode_legacy_http_pick_and_new_route_absent(self):
        # Fallback compatibility uses a genuinely single-league legacy database.
        # Multi-league owned data must use verified mode (covered by coexistence tests).
        game=self.folder/'legacy-single-disabled.db'
        module=self.load_app(dict(self.values,RUNTIME_AUTH0_ENABLED='0',DB_PATH='sqlite:///'+str(game)))
        module.Base.metadata.create_all(module.engine)
        with closing(sqlite3.connect(game)) as db,db:
            db.executescript("""INSERT INTO players(id,name) VALUES(1,'Synthetic Player'),(4,'Synthetic Opponent');
            INSERT INTO seasons(id,code,name,is_active,is_archived) VALUES(1,'loyola-season','Synthetic Loyola',1,0);
            INSERT INTO weeks(id,season_id,number,room_code,status) VALUES(10,1,1,'SYNTHETIC','drafting');
            INSERT INTO fixtures(id,week_id,match_number,home,away) VALUES(100,10,1,'Loyola Home','Loyola Away');
            INSERT INTO matchups(id,week_id,player_a_id,player_b_id,first_picker_id) VALUES(1000,10,1,4,1);""")
        module.app.testing=True;client=module.app.test_client()
        with client.session_transaction() as session:session['player_name']='Synthetic Player'
        response=client.post('/pick',data=dict(season='loyola-season',week='1',matchup_id='1000',fixture_id='100',team='Loyola Home',expected_count='0'))
        self.assertEqual(response.status_code,303)
        with closing(sqlite3.connect(game)) as db:
            self.assertEqual(db.execute('SELECT player_id FROM picks WHERE matchup_id=1000').fetchall(),[(1,)])
        self.assertEqual(client.post(self.url()).status_code,404)

    def test_review_rejects_other_matchup_in_same_league(self):
        self.change("INSERT INTO players(id,name,league_id,status,role) VALUES(6,'Synthetic Third Member',2,'active','member')")
        self.change('INSERT INTO league_season_memberships(season_id,membership_id) VALUES(2,6)')
        self.change('INSERT INTO matchups(id,week_id,player_a_id,player_b_id,first_picker_id) VALUES(2001,20,5,6,5)')
        before=self.snapshot()
        self.assertEqual(self.submit(path=self.url(matchup=2001)).status_code,403)
        self.assertEqual(self.snapshot(),before)

    def test_review_duplicate_fields_and_missing_version_refuse(self):
        from werkzeug.datastructures import MultiDict
        data=[('fixture_id','100'),('team','Loyola Home'),('selected_club_id','10'),('expected_count','0'),('csrf',self.csrf)]
        for key,value in data:
            self.assertEqual(self.post(self.url(),MultiDict(data+[(key,value)])).status_code,400)
        self.assertEqual(self.post(self.url(),dict(data[:3]+data[4:])).status_code,400)
        self.assertEqual(self.rows(),[])

    def test_review_revocation_while_request_waits_for_write_lock(self):
        from sqlalchemy import event
        waiting=threading.Event()
        def before_execute(conn,cursor,statement,parameters,context,executemany):
            if statement=='BEGIN IMMEDIATE':waiting.set()
        event.listen(self.module.engine,'before_cursor_execute',before_execute)
        try:
            with closing(sqlite3.connect(self.game)) as writer:
                writer.execute('BEGIN IMMEDIATE')
                with ThreadPoolExecutor(1) as pool:
                    future=pool.submit(self.submit)
                    try:
                        self.assertTrue(waiting.wait(3),'Request never reached write lock')
                        writer.execute("UPDATE players SET status='inactive' WHERE id=2")
                    finally:writer.commit()
                    self.assertEqual(future.result(timeout=10).status_code,404)
        finally:event.remove(self.module.engine,'before_cursor_execute',before_execute)
        self.assertEqual(self.rows(),[])

if __name__=='__main__':unittest.main()
