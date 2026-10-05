from contextlib import closing, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import football_reference_tasks as tasks
from football_reference import ReferenceClient, ReferenceError, inspect_reference
from fixture_history import load_fixture_history
from test_football_reference import season_data
import trigger_football_reference_sync as trigger


class ReferenceTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.db'
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE results(id INTEGER PRIMARY KEY, score TEXT)')
            db.execute("INSERT INTO results VALUES(1,'protected')")
            db.commit()
        self.now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        self.metadata, self.fixtures = season_data(28083, future=True)
        self.client = Mock()
        self.client.season.side_effect = lambda _: deepcopy(self.metadata)
        self.client.fixtures.side_effect = lambda _: deepcopy(self.fixtures)

    def run_task(self):
        return tasks.run_reference_task(self.path, self.client, clock=lambda: self.now)

    def advance(self):
        self.now += timedelta(hours=2)

    def query(self, sql):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute(sql).fetchall()

    def snapshot(self):
        return {name: self.query('SELECT * FROM ' + name) for name in
                ('football_reference_seasons', 'football_reference_fixtures',
                 'football_reference_events', 'football_reference_syncs', 'results')}

    def test_bootstrap_freshness_progression_correction_withdrawal(self):
        _, final = season_data(28083)
        self.fixtures[0] = final[0]
        report, status = self.run_task()
        self.assertEqual(status, 200)
        self.assertEqual([report[k] for k in ('fixture_count', 'final_fixture_count', 'scored_fixture_count', 'total_event_count')], [380, 1, 1, 1])
        self.assertEqual(self.run_task()[0]['status'], 'fresh')
        self.assertEqual(self.client.fixtures.call_count, 1)
        self.advance()
        self.fixtures[1] = final[1]
        self.fixtures[1]['state_id'] = 2
        self.assertEqual(self.run_task()[0]['final_fixture_count'], 1)
        self.advance()
        self.fixtures[1]['state_id'] = 5
        self.fixtures[0]['events'][0]['minute'] = 40
        self.assertEqual(self.run_task()[0]['final_fixture_count'], 2)
        self.assertEqual(self.query('SELECT minute FROM football_reference_events ORDER BY external_event_id')[0][0], 40)
        self.advance()
        self.fixtures[0]['events'][0]['rescinded'] = True
        self.fixtures[1]['events'] = []
        self.assertEqual(self.run_task()[1], 200)
        report = inspect_reference(self.path)[0]
        self.assertEqual((report['rescinded_event_count'], report['withdrawn_event_count']), (1, 1))
        self.advance()
        self.assertEqual(self.run_task()[1], 200)
        self.assertEqual(self.query('SELECT count(*) FROM football_reference_fixtures'), [(380,)])
        self.assertEqual(self.query('SELECT count(*) FROM football_reference_events'), [(2,)])
        self.assertEqual(self.query('SELECT * FROM results'), [(1, 'protected')])

    def test_failed_refresh_preserves_good_data_and_success_timestamp(self):
        self.run_task()
        before = self.snapshot()
        self.advance()
        self.fixtures.pop()
        result, status = self.run_task()
        self.assertEqual((status, result['error_code']), (502, 'validation_failed'))
        self.assertEqual(self.snapshot(), before)
        report = inspect_reference(self.path)[0]
        self.assertEqual(report['sync_status'], 'completed')
        self.assertEqual(report['latest_attempt']['status'], 'failed')
        self.assertEqual(report['last_successful_sync_at'], report['latest_attempt']['last_successful_sync_at'])
        self.assertEqual(self.run_task()[0]['status'], 'cooldown')

    def test_final_regression_rolls_back(self):
        _, final = season_data(28083)
        self.fixtures[0] = final[0]
        self.run_task()
        before = self.snapshot()
        self.advance()
        self.fixtures[0]['state_id'] = 2
        self.assertEqual(self.run_task()[1], 502)
        self.assertEqual(before, self.snapshot())

    def test_overlap_skips_network_and_fetch_holds_no_write_lock(self):
        def fetch(_):
            with closing(sqlite3.connect(self.path, timeout=0)) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("UPDATE results SET score='protected'")
                db.commit()
            other = Mock()
            result, status = tasks.run_reference_task(self.path, other, clock=lambda: self.now)
            self.assertEqual((status, result['status']), (200, 'already_running'))
            other.season.assert_not_called()
            return deepcopy(self.fixtures)
        self.client.fixtures.side_effect = fetch
        self.assertEqual(self.run_task()[1], 200)

    def test_expired_worker_cannot_overwrite_replacement(self):
        def fetch(_):
            self.now += timedelta(minutes=11)
            replacement = Mock()
            replacement.season.return_value = self.metadata
            newer = deepcopy(self.fixtures)
            _, final = season_data(28083)
            newer[0] = final[0]
            replacement.fixtures.return_value = newer
            self.assertEqual(tasks.run_reference_task(self.path, replacement, clock=lambda: self.now)[1], 200)
            return self.fixtures
        self.client.fixtures.side_effect = fetch
        result, status = self.run_task()
        self.assertEqual((status, result['error_code'], result['failure_recorded']), (409, 'lease_lost', False))
        report = inspect_reference(self.path)[0]
        self.assertEqual((report['final_fixture_count'], report['latest_attempt']['status']), (1, 'completed'))

    def test_expiry_at_commit_rolls_back_entire_refresh(self):
        self.run_task()
        before = self.snapshot()
        self.advance()
        original = tasks.finish
        def expired(db, token, now):
            return original(db, token, now + timedelta(minutes=11))
        with patch.object(tasks, 'finish', side_effect=expired):
            self.assertEqual(self.run_task()[0]['error_code'], 'lease_lost')
        self.assertEqual(self.snapshot(), before)

    def test_crashed_claim_recovers_after_expiry(self):
        tasks.claim(self.path, self.now)
        self.assertEqual(self.run_task()[0]['status'], 'already_running')
        self.now += timedelta(minutes=11)
        self.assertEqual(self.run_task()[0]['status'], 'completed')

    def test_busy_claim_does_not_fetch(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('BEGIN IMMEDIATE')
            result, status = self.run_task()
        self.assertEqual((status, result['error_code']), (503, 'database_busy'))
        self.client.season.assert_not_called()

    def test_transaction_failure_rolls_back(self):
        self.run_task()
        before = self.snapshot()
        self.advance()
        with patch.object(tasks, 'finish', side_effect=sqlite3.OperationalError('private secret')):
            result, status = self.run_task()
        self.assertEqual(status, 503)
        self.assertNotIn('private secret', json.dumps(result))
        self.assertEqual(before, self.snapshot())

    def test_failed_first_attempt_inspection_and_safe_errors(self):
        for code, status in [('missing_token', 503), ('rate_limited', 429),
                             ('provider_http_error', 502), ('provider_unavailable', 502)]:
            with self.subTest(code=code):
                self.client.season.side_effect = ReferenceError('SECRET response body', code=code, retry_after=1800)
                result, actual = self.run_task()
                self.assertEqual(actual, status)
                self.assertNotIn('SECRET', json.dumps(result))
                report = inspect_reference(self.path)[0]
                self.assertEqual(report['sync_status'], 'not_imported')
                self.assertIsNone(report['last_successful_sync_at'])
                self.assertEqual(report['latest_attempt']['error_code'], code)
                self.advance()

    def test_partial_fetch_preserves_snapshot(self):
        self.run_task()
        before = self.snapshot()
        self.advance()
        self.client.fixtures.side_effect = ReferenceError('Malformed second page')
        self.assertEqual(self.run_task()[1], 502)
        self.assertEqual(before, self.snapshot())

    def test_missing_token_without_network(self):
        with patch.dict('os.environ', {'SPORTMONKS_API_TOKEN': ''}), patch('football_reference.build_opener') as opener:
            result, status = tasks.run_reference_task(self.path, clock=lambda: self.now)
        self.assertEqual((status, result['error_code']), (503, 'missing_token'))
        opener.return_value.open.assert_not_called()

    def test_real_client_failure_paths_preserve_snapshot(self):
        self.run_task()
        before = self.snapshot()
        for mode in ('malformed', 'truncated', 'late_page', 'rate_limit', 'http'):
            self.advance()
            def fetch(req, **kwargs):
                if '/seasons/' in req.full_url:
                    return io.BytesIO(json.dumps({'data': self.metadata}).encode())
                page = int(parse_qs(urlparse(req.full_url).query)['page'][0])
                if mode in ('rate_limit', 'http'):
                    raise HTTPError('https://SECRET', 429 if mode == 'rate_limit' else 500, 'SECRET', {'Retry-After': '1200'}, None)
                if mode == 'malformed' or (mode == 'late_page' and page == 2):
                    return io.BytesIO(b'SECRET malformed response')
                return io.BytesIO(json.dumps(dict(data=self.fixtures[:50], pagination=dict(current_page=page, has_more=mode != 'truncated'))).encode())
            with self.subTest(mode=mode):
                result, status = tasks.run_reference_task(self.path, ReferenceClient(token='SECRET', opener=fetch), clock=lambda: self.now)
                self.assertEqual(status, 429 if mode == 'rate_limit' else 502)
                self.assertNotIn('SECRET', json.dumps(result))
                self.assertEqual(before, self.snapshot())

    def test_pr21_sees_same_season_meeting_after_sync(self):
        fixture = SimpleNamespace(home='Chelsea', away='Arsenal', kickoff_utc='2027-02-01T15:00:00+00:00')
        def history():
            with closing(sqlite3.connect(self.path)) as db:
                return load_fixture_history(db, fixture=fixture, as_of=fixture.kickoff_utc)
        self.assertIsNone(history()['most_recent_meeting'])
        _, final = season_data(28083)
        for i, raw in enumerate(self.fixtures):
            home, away = [p['id'] for p in raw['participants']]
            raw['starting_at'] = '2027-05-01 15:00:00'
            if (home, away) == (19, 18):
                self.fixtures[i] = final[i]
                self.fixtures[i]['starting_at'] = '2026-10-01 15:00:00'
        self.assertEqual(self.run_task()[1], 200)
        meeting = history()['most_recent_meeting']
        self.assertEqual((meeting['external_season_id'], meeting['home_team_id'], meeting['away_team_id'], meeting['home_score'], meeting['away_score']), (28083, 19, 18, 1, 0))
        self.assertEqual(len(meeting['events']), 1)


class ReferenceEndpointTests(unittest.TestCase):
    def test_auth_input_and_success(self):
        from test_app import app_module
        client = app_module.app.test_client()
        url = '/tasks/sync-football-reference'
        with patch.dict('os.environ', {'SYNC_SECRET': 'task-secret'}), patch.object(tasks, 'run_reference_task', return_value=({'ok': True, 'status': 'completed'}, 200)) as run:
            self.assertEqual(client.get(url).status_code, 405)
            for headers in ({}, {'X-Sync-Secret': 'wrong'}):
                self.assertEqual(client.post(url, headers=headers).status_code, 403)
            headers = {'X-Sync-Secret': 'task-secret'}
            for body in ('[]', 'null', '{', '{"season_id":23614}', 'x'*1025):
                self.assertEqual(client.post(url, headers=headers, data=body).status_code, 400)
            run.assert_not_called()
            response = client.post(url, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            run.assert_called_once_with(app_module.engine.url.database)
        with patch.dict('os.environ', {'SYNC_SECRET': ''}):
            self.assertEqual(client.post(url).status_code, 503)

    def test_rate_limit_header(self):
        from test_app import app_module
        with patch.dict('os.environ', {'SYNC_SECRET': 'secret'}), patch.object(tasks, 'run_reference_task', return_value=({'ok': False, 'error_code': 'rate_limited', 'retry_after': 1800}, 429)):
            response = app_module.app.test_client().post('/tasks/sync-football-reference', headers={'X-Sync-Secret': 'secret'})
            self.assertEqual((response.status_code, response.headers['Retry-After']), (429, '1800'))


class ReferenceTriggerTests(unittest.TestCase):
    def test_single_dispatch_safe_output(self):
        for result in ('completed', 'fresh', 'already_running', 'cooldown'):
            with patch.object(trigger, 'trigger_score_sync', return_value={'ok': True, 'status': result, 'private': 'SECRET'}) as call, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(trigger.main(), 0)
                call.assert_called_once()
                self.assertNotIn('SECRET', output.getvalue())
        with patch.object(trigger, 'trigger_score_sync', side_effect=RuntimeError('SECRET provider body')) as call, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(trigger.main(), 1)
            call.assert_called_once()
            self.assertNotIn('SECRET', output.getvalue())
