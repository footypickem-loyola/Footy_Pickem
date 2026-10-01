import copy
import atexit
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from datetime import timedelta
from urllib.error import HTTPError

# Import-time migrations must also be isolated when this file is run alone.
IMPORT_TEMP = tempfile.TemporaryDirectory(prefix='footy-pr12-import-')
if 'pickem_flask_htmx_tabs' not in __import__('sys').modules:
    os.environ['DB_PATH'] = f"sqlite:///{Path(IMPORT_TEMP.name) / 'import.db'}"
    os.environ['INIT_ON_START'] = '0'
import pickem_flask_htmx_tabs as d
atexit.register(d.engine.dispose)
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import scoped_session, sessionmaker
from sportmonks_live import normalize_fixture, SportmonksClient, ProviderError
from live_sync import ingest
from live_sync_worker import cycle
from live_bridge import BridgeError
from live_matchweek import contribution, factual_context
from live_fakes import seed, payload, NOW


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='footy-pr12-test-')
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'test.db'}")
        d.Base.metadata.create_all(self.engine)
        self.sessions = scoped_session(sessionmaker(bind=self.engine))
        self.patch = patch.object(d, 'SessionLocal', self.sessions)
        self.patch.start()
        self.db = self.sessions()
        seed(self.db, d)
        self.week = self.db.query(d.Week).one()
        self.fixtures = self.db.query(d.Fixture).order_by(d.Fixture.id).all()
        d.app.config['TESTING'] = True

    def tearDown(self):
        self.db.close()
        self.sessions.remove()
        self.patch.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def sync(self, *raw, now=NOW):
        count = ingest(self.db, d, [normalize_fixture(x) for x in raw], now=now)
        self.db.commit()
        return count

    def model(self):
        with d.app.test_request_context('/'):
            return d.load_matchweek_model(self.db, self.week.season, self.week, include_context=False)

    def test_adapter_scores_periods_and_event_types(self):
        raw = payload(self.fixtures[0], minute=94)
        raw['events'] = [dict(raw['events'][0], id=i, type_id=i) for i in (14, 15, 16, 18, 19, 20, 21, 10)]
        result = normalize_fixture(raw)
        self.assertEqual((result.minute, result.extra_minute, result.home_score), (90, 4, 1))
        self.assertEqual({e['event_type'] for e in result.events}, {'goal','own_goal','penalty','substitution','yellow_card','red_card','second_yellow','var'})
        raw.pop('events'); raw.pop('scores'); raw.pop('periods')
        result = normalize_fixture(raw)
        self.assertIsNone(result.events)
        self.assertIsNone(result.home_score)
        self.assertIsNone(result.minute)

    def test_adapter_distinguishes_delays_interruptions_and_second_half(self):
        for state, expected in ((13,'not_started'),(16,'delayed'),(18,'interrupted'),(22,'live'),(5,'finished')):
            self.assertEqual(normalize_fixture(payload(self.fixtures[0],state=state)).state, expected)
        self.sync(payload(self.fixtures[0]))
        self.sync(payload(self.fixtures[0],state=19,score=(0,0)), now=NOW+timedelta(minutes=5))
        stored = self.db.query(d.LiveFixtureState).one()
        self.assertEqual((stored.state, stored.home_score, stored.last_synced_at), ('live',1,NOW))

    def test_client_safe_errors_timeout_rate_limit_and_no_token_in_url(self):
        seen = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({'data': [], 'rate_limit': {'remaining': 9}}).encode()
        def opener(request, timeout):
            seen.append((request.full_url, timeout, request.get_header('Authorization')))
            return Response()
        client = SportmonksClient(token='FAKE_SECRET', opener=opener)
        self.assertEqual(client.livescores(), [])
        self.assertEqual(client.rate_limit['remaining'], 9)
        self.assertEqual(seen[0][2], 'Bearer FAKE_SECRET')
        self.assertNotIn('FAKE_SECRET', seen[0][0])
        self.assertEqual(seen[0][1], 10)
        for error in (RuntimeError('FAKE_SECRET'), HTTPError('FAKE_SECRET', 429, 'FAKE_SECRET', {'Retry-After':'120'}, None)):
            with patch.object(client, 'opener', side_effect=error):
                with self.assertRaises(ProviderError) as caught: client.livescores()
                self.assertNotIn('FAKE_SECRET', str(caught.exception))
                if isinstance(error, HTTPError): self.assertEqual(caught.exception.retry_after, 120)

    def test_exact_mapping_aliases_reject_wrong_date_team_league_and_ambiguity(self):
        raw = payload(self.fixtures[2]); raw['participants'][0]['name'] = 'Manchester City'
        self.assertEqual(self.sync(raw), 1)
        self.assertEqual(self.fixtures[2].external_match_id, 10012)
        for field in ('date', 'team', 'league'):
            bad = payload(self.fixtures[0])
            if field == 'date': bad['starting_at'] = '2020-01-01T00:00:00'
            if field == 'team': bad['participants'][0]['name'] = 'Arsenal Women'
            if field == 'league': bad['league_id'] = 9
            self.assertEqual(self.sync(bad), 0)
        f = self.fixtures[0]
        self.db.add(d.Fixture(week_id=self.week.id, match_number=11, home=f.home, away=f.away, kickoff_utc=f.kickoff_utc))
        self.db.commit()
        self.assertEqual(self.sync(payload(f)), 0)

    def test_idempotency_corrections_withdrawal_missing_include_and_reactivation(self):
        raw = payload(self.fixtures[0])
        self.sync(raw); self.sync(raw)
        event = self.db.query(d.MatchEvent).one()
        self.assertEqual(event.revisions, [])
        raw['events'][0]['player_name'] = 'Corrected Scorer'
        self.sync(raw)
        self.assertEqual(event.player_name, 'Corrected Scorer')
        self.assertEqual(len(event.revisions), 1)
        missing = copy.deepcopy(raw); missing.pop('events')
        self.sync(missing); self.assertTrue(event.is_active)
        empty = dict(raw, events=[])
        self.sync(empty); self.assertFalse(event.is_active)
        self.sync(raw); self.assertTrue(event.is_active)
        self.assertEqual(self.db.query(d.MatchEvent).count(), 1)
        self.assertEqual(self.db.query(d.FixtureProviderLink).count(), 1)

    def test_fallback_identity_is_deterministic_and_correction_safe(self):
        raw = payload(self.fixtures[0]); raw['events'][0].pop('id')
        self.sync(raw); self.sync(raw)
        self.assertEqual(self.db.query(d.MatchEvent).count(), 1)
        raw['events'][0]['minute'] = 26
        self.sync(raw)
        self.assertEqual(self.db.query(d.MatchEvent).filter_by(is_active=True).count(), 1)
        self.assertEqual(self.db.query(d.MatchEvent).count(), 2)

    def test_consequences_equalizer_opponent_and_official_authority(self):
        self.assertEqual(contribution('A', 'A', 1, 0), 1)
        self.assertEqual(contribution('B', 'A', 1, 0), -1)
        self.assertEqual(contribution('A', 'A', 1, 1), 0)
        self.assertEqual(self.model()['primary']['state'], 'ready')
        self.sync(payload(self.fixtures[0]), payload(self.fixtures[1], score=(0, 1)))
        m = self.model()['primary']
        self.assertEqual([(p['for'], p['against'], p['net']) for p in m['players']], [(1,-1,2),(-1,1,-2)])
        self.assertEqual(m['projected_payout'], 10)
        self.assertEqual(m['timeline'][0]['impact'], (0,1))
        self.assertEqual(self.db.query(d.Result).count(), 0)
        self.sync(payload(self.fixtures[0], score=(1, 1)))
        self.assertEqual(self.model()['primary']['players'][0]['for'], 0)
        self.db.add(d.Result(fixture_id=self.fixtures[0].id, outcome='Away', home_score=0, away_score=3, source='manual'))
        self.db.commit()
        self.sync(payload(self.fixtures[0], score=(9, 0)))
        self.assertEqual(self.model()['primary']['players'][0]['for'], -1)
        self.assertEqual(self.db.query(d.Result).one().home_score, 0)
        self.week.status = 'finalized'; self.db.add(self.week); self.db.commit()
        self.assertEqual(self.model()['primary']['state'], 'final')

    def test_not_started_missing_scores_stale_archive_and_factual_context(self):
        self.sync(payload(self.fixtures[0], state=1))
        self.assertEqual(self.model()['primary']['state'], 'ready')
        raw = payload(self.fixtures[0]); raw['scores'] = []
        self.sync(raw, now=NOW-timedelta(days=1))
        m = self.model()['primary']
        self.assertTrue(m['stale'])
        self.assertEqual(factual_context(self.db,d,self.week.id)[0]['events'][0]['related_player_name'], 'Sam Winger')
        self.week.season.is_archived = 1; self.db.add(self.week.season); self.db.commit()
        self.assertEqual(self.model()['primary']['state'], 'ready')

    def test_get_refresh_no_writes_polling_and_escaping(self):
        raw = payload(self.fixtures[0]); raw['events'][0]['player_name'] = '<script>alert(1)</script>'
        self.sync(raw)
        with d.app.test_client() as client:
            for headers in ({}, {'HX-Request':'true'}, {'HX-History-Restore-Request':'true'}):
                response = client.get('/tab/current?season=pr12-local&force_week=1', headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'every 15s', response.data)
                self.assertIn(b'&lt;script&gt;', response.data)
                self.assertNotIn(b'<script>alert(1)</script>', response.data)
            client.get('/partials/matchweek/1?season=pr12-local')
        self.assertEqual(self.sessions().query(d.MatchEvent).count(), 1)
        self.assertEqual(self.sessions().query(d.Result).count(), 0)

    def test_additive_migration_repeated_preserves_pr11_rows(self):
        self.sessions.remove()
        for table in (d.MatchEvent.__table__, d.LiveFixtureState.__table__, d.FixtureProviderLink.__table__):
            table.drop(self.engine)
        with self.engine.connect() as connection:
            before = {table: connection.execute(text(f'SELECT * FROM {table}')).fetchall() for table in ('seasons','weeks','fixtures','picks','results')}
        d.ensure_database_schema(self.engine); d.ensure_database_schema(self.engine)
        with self.engine.connect() as connection:
            for table, rows in before.items():
                self.assertEqual(connection.execute(text(f'SELECT * FROM {table}')).fetchall(), rows)
        self.assertIn('match_events', inspect(self.engine).get_table_names())

    def test_worker_empty_multiple_live_and_transient_failure(self):
        items = [normalize_fixture(payload(f)) for f in self.fixtures[:2]]
        client = SimpleNamespace(league_id=8, rate_limit=None, livescores=lambda: [])
        bridge = SimpleNamespace(send=lambda fixtures, league: len(fixtures))
        self.assertEqual(cycle(client, bridge), 60)
        client.livescores = lambda: items
        self.assertEqual(cycle(client, bridge), 15)
        self.assertEqual(cycle(client, bridge), 15)
        with patch.object(client, 'livescores', side_effect=ProviderError('Unavailable', 120)):
            self.assertEqual(cycle(client, bridge), 120)
        self.assertEqual(self.db.query(d.LiveFixtureState).count(), 0)

    def test_worker_near_finished_rate_limit_and_bridge_failure(self):
        raw = payload(self.fixtures[0])
        client = SimpleNamespace(league_id=8, rate_limit=None,
                                 livescores=lambda: [normalize_fixture(dict(raw, state_id=5))])
        bridge = SimpleNamespace(send=lambda fixtures, league: len(fixtures))
        self.assertEqual(cycle(client, bridge), 60)
        client.livescores = lambda: [normalize_fixture(raw)]
        client.rate_limit = {'remaining': 0, 'resets_in_seconds': 240}
        self.assertEqual(cycle(client, bridge), 240)
        with patch.object(bridge, 'send', side_effect=BridgeError('Unavailable', 90)):
            self.assertEqual(cycle(client, bridge), 90)
        self.assertEqual(self.db.query(d.LiveFixtureState).count(), 0)

    def test_migration_keeps_official_and_archived_history(self):
        self.db.add(d.Result(fixture_id=self.fixtures[0].id, outcome='Away', home_score=0, away_score=2, source='manual'))
        self.week.season.is_archived = 1
        self.db.commit()
        for table in (d.MatchEvent.__table__, d.LiveFixtureState.__table__, d.FixtureProviderLink.__table__):
            table.drop(self.engine)
        d.ensure_database_schema(self.engine); d.ensure_database_schema(self.engine)
        self.assertEqual(self.db.query(d.Result).one().source, 'manual')
        self.assertEqual(self.db.query(d.Pick).count(), 30)
        self.assertEqual(self.sync(payload(self.fixtures[0])), 0)
        self.assertEqual(self.db.query(d.LiveFixtureState).count(), 0)

    def test_timeline_order_missing_progress_and_official_final_stops_poll(self):
        raw = payload(self.fixtures[0])
        raw['events'] = [dict(raw['events'][0], id=2, minute=90, extra_minute=4, result='2-1'),
                         dict(raw['events'][0], id=1, minute=25, result='1-0')]
        self.sync(raw)
        timeline = self.model()['primary']['timeline']
        self.assertEqual([e['minute'] for e in timeline], [25,90])
        self.assertIsNone(timeline[-1]['impact'])
        self.week.status = 'finalized'; self.db.add(self.week); self.db.commit()
        with d.app.test_client() as client:
            response = client.get('/partials/matchweek/1?season=pr12-local')
            self.assertNotIn(b'every 15s', response.data)
            self.assertNotIn(b'projected payout', response.data)

    def test_client_rejects_partial_responses_invalid_scores_and_missing_token(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"data": [], "pagination": {"has_more": true}}'
        with self.assertRaises(ProviderError):
            SportmonksClient(token='fake', opener=lambda *a, **k: Response()).livescores()
        with self.assertRaises(ProviderError):
            SportmonksClient(token='').livescores()
        raw = payload(self.fixtures[0]); raw['scores'][0]['score']['goals'] = -1
        with self.assertRaises(ValueError): normalize_fixture(raw)

    def test_goal_correction_does_not_change_current_score_by_counting_events(self):
        raw = payload(self.fixtures[0], score=(0,0), events=[])
        self.sync(raw)
        self.assertEqual(self.model()['primary']['players'][0]['for'], 0)
        raw['events'] = payload(self.fixtures[0])['events']
        self.sync(raw)
        self.assertEqual(self.model()['primary']['players'][0]['for'], 0)
        raw['events'].append(dict(id=9, type_id=10, sub_type_id=1512, minute=27))
        self.sync(raw)
        self.assertIsNone(self.model()['primary']['timeline'][0]['impact'])


if __name__ == '__main__':
    unittest.main()
