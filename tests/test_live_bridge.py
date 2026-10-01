"""Bridge tests use temporary PR12 fixtures and fake HTTP transports only."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import test_live_matchweek as live_tests
from live_fakes import payload, NOW
from live_bridge import (LiveIngestClient, BridgeError, SnapshotError, MAX_SNAPSHOT_BYTES,
                         encode_snapshot, decode_snapshot, _NoRedirect)
from live_sync import ingest
from live_matchweek import factual_context
from sportmonks_live import normalize_fixture, SportmonksClient, ProviderError
from live_sync_worker import cycle

d = live_tests.d


class BridgeEndpointTests(unittest.TestCase):
    setUp = live_tests.LiveTests.setUp
    tearDown = live_tests.LiveTests.tearDown
    model = live_tests.LiveTests.model

    def post(self, body, secret='FAKE_BRIDGE_SECRET', content_type='application/json'):
        # Match a real request boundary, without the test setup's open read transaction.
        self.db.close()
        self.sessions.remove()
        with patch.dict(os.environ, {'LIVE_INGEST_SECRET': 'FAKE_BRIDGE_SECRET'}):
            return d.app.test_client().post('/tasks/live-ingest', data=body,
                content_type=content_type, headers={'X-Live-Ingest-Secret': secret} if secret is not None else {})

    def snapshot(self, raw=None):
        return encode_snapshot([normalize_fixture(raw or payload(self.fixtures[0]))], 8)

    def counts(self):
        with self.sessions() as db:
            return tuple(db.query(model).count() for model in (d.LiveFixtureState, d.MatchEvent, d.FixtureProviderLink))

    def test_authenticated_snapshot_persists_normalized_facts_and_is_idempotent(self):
        body = self.snapshot()
        for _ in range(2):
            response = self.post(body)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json, {'ok': True, 'stored': 1})
        self.assertEqual(self.counts(), (1, 1, 1))
        with self.sessions() as db:
            state = db.query(d.LiveFixtureState).one()
            self.assertEqual((state.home_score, state.away_score, state.minute), (1, 0, 30))
            event = db.query(d.MatchEvent).one()
            self.assertEqual(event.revisions, [])
            facts = factual_context(db, d, db.query(d.Week).one().id)
            self.assertEqual(facts[0]['events'][0]['running_score'], '1-0')
            self.assertEqual(facts[0]['events'][0]['related_player_name'], 'Sam Winger')

    def test_authentication_disabled_missing_wrong_and_existing_secrets_rejected(self):
        body = self.snapshot()
        with patch.object(d, 'SessionLocal', side_effect=AssertionError('DB must not open')):
            for secret in (None, '', 'wrong', 'FAKE_SYNC_SECRET', 'é'):
                self.assertEqual(self.post(body, secret).status_code, 403)
            with patch.dict(os.environ, {'LIVE_INGEST_SECRET': '', 'SYNC_SECRET': 'FAKE_SYNC_SECRET'}):
                self.assertEqual(d.app.test_client().post('/tasks/live-ingest', data=body,
                    content_type='application/json', headers={'X-Live-Ingest-Secret': 'FAKE_SYNC_SECRET'}).status_code, 503)
            self.assertEqual(d.app.test_client().get('/tasks/live-ingest').status_code, 405)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_rejects_raw_unknown_fields_invalid_fixture_and_event_before_db_access(self):
        valid = json.loads(self.snapshot())
        invalid = [payload(self.fixtures[0]), [], {'fixtures': []}]
        for key, value in (('provider','other'),('league_id',9),('league_id',True)):
            invalid.append(dict(valid, **{key:value}))
        for key, value in (('league_id',9),('state','INPLAY_1ST_HALF'),('home_score',True),
                           ('away_score',-1),('minute','30'),('home',{}),('kickoff','not a date'),
                           ('provider_updated_at',{}),('events',{})):
            bad = copy.deepcopy(valid)
            bad['fixtures'].append(dict(bad['fixtures'][0], external_id='second', **{key:value}))
            invalid.append(bad)
        for key, value in (('is_active',1),('event_type','commentary'),('minute',-1),('player_name',{}),
                           ('side','unknown'),('running_score','not a score'),('fixture_id',123)):
            bad = copy.deepcopy(valid)
            bad['fixtures'][0]['events'].append(dict(bad['fixtures'][0]['events'][0], external_event_id='second', **{key:value}))
            invalid.append(bad)
        bad = copy.deepcopy(valid)
        bad['fixtures'][0]['events'][0].pop('is_active')
        invalid.append(bad)
        bad = copy.deepcopy(valid)
        bad['fixtures'][0]['participants'] = []
        invalid.append(bad)
        for body in invalid:
            with self.subTest(body=body), patch.object(d, 'SessionLocal', side_effect=AssertionError('DB must not open')):
                self.assertEqual(self.post(json.dumps(body)).status_code, 400)
        for body in (b'{', b'{"provider":"sportmonks","provider":"other"}', b'[' * 2000):
            self.assertEqual(self.post(body).status_code, 400)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_payload_content_type_size_collection_bounds_and_duplicate_ids(self):
        valid = json.loads(self.snapshot())
        with patch.object(d, 'SessionLocal', side_effect=AssertionError('DB must not open')):
            self.assertEqual(self.post(self.snapshot(), content_type='text/plain').status_code, 415)
            self.assertEqual(self.post(b' ' * (MAX_SNAPSHOT_BYTES + 1)).status_code, 413)
            # Bound streams even if no Content-Length was provided by the sender.
            with patch.dict(os.environ, {'LIVE_INGEST_SECRET': 'FAKE_BRIDGE_SECRET'}):
                response = d.app.test_client().post('/tasks/live-ingest', content_type='application/json',
                    headers={'X-Live-Ingest-Secret': 'FAKE_BRIDGE_SECRET'}, environ_overrides={
                        'wsgi.input': io.BytesIO(b' ' * (MAX_SNAPSHOT_BYTES + 1)),
                        'wsgi.input_terminated': True, 'CONTENT_LENGTH': ''})
                self.assertEqual(response.status_code, 413)
            for count in (2, 101):
                self.assertEqual(self.post(json.dumps(dict(valid, fixtures=valid['fixtures'] * count))).status_code, 400)
            for count in (2, 501):
                bad = copy.deepcopy(valid)
                bad['fixtures'][0]['events'] *= count
                self.assertEqual(self.post(json.dumps(bad)).status_code, 400)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_bridge_preserves_corrections_omitted_events_withdrawals_and_reactivation(self):
        raw = payload(self.fixtures[0])
        self.post(self.snapshot(raw))
        raw['events'][0]['player_name'] = 'Corrected Scorer'
        self.post(self.snapshot(raw))
        missing = dict(raw)
        missing.pop('events')
        self.post(self.snapshot(missing))
        with self.sessions() as db:
            event = db.query(d.MatchEvent).one()
            self.assertEqual(event.player_name, 'Corrected Scorer')
            self.assertEqual(len(event.revisions), 1)
            self.assertTrue(event.is_active)
        self.post(self.snapshot(dict(raw, events=[])))
        with self.sessions() as db:
            self.assertFalse(db.query(d.MatchEvent).one().is_active)
        self.post(self.snapshot(raw))
        with self.sessions() as db:
            self.assertTrue(db.query(d.MatchEvent).one().is_active)
        self.assertEqual(self.counts(), (1, 1, 1))

    def test_transaction_failure_rolls_back_entire_snapshot_and_returns_safe_error(self):
        items = [normalize_fixture(payload(f)) for f in self.fixtures[:2]]
        def fail_after_write(db, models, fixtures, **kwargs):
            ingest(db, models, fixtures, **kwargs)
            raise RuntimeError('FAKE_BRIDGE_SECRET private payload')
        with patch('live_sync.ingest', side_effect=fail_after_write), self.assertLogs(d.app.logger, level='ERROR') as logs:
            response = self.post(encode_snapshot(items, 8))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('FAKE_BRIDGE_SECRET', str(logs.output) + response.get_data(as_text=True))
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.post(encode_snapshot(items, 8)).json['stored'], 2)
        self.assertEqual(self.counts(), (2, 2, 2))

    def test_official_results_week_picks_and_correspondent_context_remain_authoritative(self):
        fixture_id = self.fixtures[0].id
        self.db.add(d.Result(fixture_id=fixture_id, outcome='Away', home_score=0, away_score=3, source='manual'))
        self.db.commit()
        body = self.snapshot(payload(self.fixtures[0], score=(9, 0)))
        self.assertEqual(self.post(body).status_code, 200)
        with self.sessions() as db:
            official = db.query(d.Result).one()
            self.assertEqual((official.outcome, official.source, official.home_score, official.away_score), ('Away','manual',0,3))
            week = db.query(d.Week).one()
            self.assertEqual(week.status, 'provisional')
            self.assertEqual(db.query(d.Pick).count(), 30)
            facts = factual_context(db,d,week.id)
            self.assertEqual((facts[0]['official'],facts[0]['home_score'],facts[0]['away_score']), (True,0,3))
            with d.app.test_request_context('/'):
                model = d.load_matchweek_model(db, week.season, week, include_context=False)
                self.assertEqual(model['primary']['players'][0]['for'], -1)
        with self.sessions() as db:
            db.query(d.Week).one().status = 'finalized'
            db.commit()
        self.assertEqual(self.post(body).json['stored'], 0)
        with self.sessions() as db:
            self.assertEqual(db.query(d.Week).one().status, 'finalized')
            self.assertEqual(db.query(d.Result).one().source, 'manual')

    def test_fake_worker_http_transport_round_trip_and_restart_has_no_duplicate_events(self):
        raw = payload(self.fixtures[0])
        raw['private_provider_extra'] = 'must-not-forward'
        seen = []
        def opener(request, timeout):
            seen.append((request, timeout))
            result = self.post(request.data, request.get_header('X-live-ingest-secret'))
            return FakeResponse(result.data, result.status_code)
        bridge = LiveIngestClient('http://flask.test/tasks/live-ingest', 'FAKE_BRIDGE_SECRET', opener)
        client = SportmonksClient(token='FAKE_SPORTMONKS', opener=lambda *a, **k: FakeResponse(json.dumps({'data':[raw]}).encode()))
        for _ in range(2):
            self.assertEqual(cycle(client, bridge), 15)
        self.assertEqual(self.counts(), (1, 1, 1))
        request, timeout = seen[0]
        self.assertEqual(request.method, 'POST')
        self.assertEqual(request.get_header('X-live-ingest-secret'), 'FAKE_BRIDGE_SECRET')
        self.assertEqual(timeout, 10)
        self.assertNotIn('FAKE_BRIDGE_SECRET', request.full_url)
        self.assertNotIn(b'must-not-forward', request.data)
        self.assertNotIn(b'FAKE_SPORTMONKS', request.data)
        self.assertNotIn(b'participants', request.data)
        self.assertEqual(decode_snapshot(request.data)[0].home_score, 1)


class FakeResponse:
    def __init__(self, body=b'{"ok":true,"stored":0}', status=200):
        self.body, self.status = body, status
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size=-1): return self.body if size == -1 else self.body[:size]


class BridgeWorkerTests(unittest.TestCase):
    def test_bridge_rejects_http_timeout_invalid_ack_and_does_not_leak_secrets(self):
        errors = [TimeoutError('FAKE_SECRET'), URLError('FAKE_SECRET'),
                  HTTPError('http://FAKE_SECRET',429,'FAKE_SECRET',{'Retry-After':'120'},None)]
        for error in errors:
            bridge = LiveIngestClient('http://flask.test/tasks/live-ingest','FAKE_SECRET', Mock(side_effect=error))
            with self.assertRaises(BridgeError) as caught: bridge.send([],8)
            self.assertNotIn('FAKE_SECRET', str(caught.exception))
            if isinstance(error, HTTPError): self.assertEqual(caught.exception.retry_after,120)
        for body, status in ((b'{}',200),(b'not json',200),(b'{"ok":false,"stored":0}',200),
                             (b'{"ok":true,"stored":true}',200),(b'{"ok":true,"stored":1}',200),
                             (b'{"ok":true,"stored":0}',500),(b' ' * 5000,200)):
            bridge = LiveIngestClient('http://flask.test/tasks/live-ingest','FAKE_SECRET',
                                     lambda *a, **k: FakeResponse(body,status))
            with self.assertRaises(BridgeError): bridge.send([],8)

    def test_configuration_and_redirects_do_not_expose_shared_secret(self):
        for url, secret in (('', 'fake'),('http://flask.test/tasks/live-ingest',''),
                            ('http://flask.test/tasks/live-ingest?secret=fake','fake'),
                            ('http://user:fake@flask.test/tasks/live-ingest','fake'),
                            ('file:///tmp/ingest','fake')):
            with self.assertRaises(BridgeError): LiveIngestClient(url,secret)
        self.assertIsNone(_NoRedirect().redirect_request(None,None,302,'',{},'http://other.test'))

    def test_worker_failure_backoff_never_logs_credentials_or_claims_success(self):
        client = Mock(league_id=8, rate_limit=None)
        bridge = Mock()
        for failure in (ProviderError('FAKE_SECRET',120), RuntimeError('FAKE_SECRET')):
            client.livescores.side_effect = failure
            with self.assertLogs(level='WARNING') as logs:
                self.assertGreaterEqual(cycle(client,bridge),60)
            self.assertNotIn('FAKE_SECRET',str(logs.output))
            bridge.send.assert_not_called()
        client.livescores.side_effect = None
        client.livescores.return_value = []
        bridge.send.side_effect = BridgeError('FAKE_SECRET',300)
        with self.assertLogs(level='INFO') as logs:
            self.assertEqual(cycle(client,bridge),300)
        self.assertNotIn('stored',str(logs.output))
        self.assertNotIn('FAKE_SECRET',str(logs.output))

    def test_enabled_worker_main_without_db_path_cannot_import_app_or_database(self):
        # Fresh interpreter: guard imports before importing/running the enabled worker.
        code = '''
import builtins, os, sys
from unittest.mock import patch
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.split('.')[0] in {'sqlite3', 'sqlalchemy', 'pickem_flask_htmx_tabs', 'live_sync', 'live_models'}:
        raise AssertionError('Worker attempted database import')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import live_sync_worker as worker
assert 'DB_PATH' not in os.environ
with patch.object(worker.SportmonksClient, 'livescores', return_value=[]), \
     patch.object(worker.LiveIngestClient, 'send', return_value=0) as send, \
     patch.object(worker.time, 'sleep', side_effect=KeyboardInterrupt):
    worker.main()
    send.assert_called_once_with([], 8)
assert not any(name in sys.modules for name in ('sqlite3','sqlalchemy','pickem_flask_htmx_tabs'))
print('stateless worker verified')
'''
        env = dict(os.environ, LIVE_SYNC_ENABLED='1', LIVE_INGEST_URL='http://flask.test/tasks/live-ingest',
                   LIVE_INGEST_SECRET='FAKE_SECRET', SPORTMONKS_API_TOKEN='FAKE_TOKEN', SPORTMONKS_LEAGUE_ID='8',
                   PYTHONPATH=str(Path(__file__).resolve().parents[1]), PYTHONDONTWRITEBYTECODE='1')
        env.pop('DB_PATH', None)
        with tempfile.TemporaryDirectory(prefix='footy-pr13-worker-') as directory:
            result = subprocess.run([sys.executable,'-B','-c',code],env=env,cwd=directory,
                                    capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stdout + result.stderr)
            self.assertEqual(list(Path(directory).iterdir()),[])
            self.assertIn('stateless worker verified',result.stdout)


if __name__ == '__main__':
    unittest.main()
