import json
import io
import unittest
from unittest.mock import patch

import trigger_score_sync


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class TriggerScoreSyncTests(unittest.TestCase):
    def run_dispatch(self, status='no_work', failure=None, score_failure=None, url='https://pickem.example/tasks/sync-pick-insight'):
        calls = []
        output, errors = io.StringIO(), io.StringIO()
        def score(*args, **kwargs):
            calls.append('score')
            if score_failure:
                raise score_failure
            return {'ok': True, 'results_imported': 10, 'pending_matches': 0}
        def enrichment(*args, **kwargs):
            calls.append('enrichment')
            if failure:
                raise failure
            return dict(status=status, clubs=20 if status == 'completed' else 0, clubs_with_scorers=0)
        with patch.dict(trigger_score_sync.os.environ, {
            'SYNC_URL':'https://pickem.example/tasks/sync-results', 'SYNC_SECRET':'test-secret',
            'PICK_INSIGHT_SYNC_URL':url, 'SYNC_TIMEOUT_SECONDS':'45'}), \
             patch.object(trigger_score_sync,'trigger_score_sync',side_effect=score), \
             patch.object(trigger_score_sync,'dispatch_pick_insight',side_effect=enrichment) as dispatch, \
             patch('sys.stdout',output), patch('sys.stderr',errors):
            result = trigger_score_sync.main()
            if calls == ['score','enrichment']:
                dispatch.assert_called_once_with(url,'test-secret',45)
        return result, calls, output.getvalue(), errors.getvalue()

    def test_score_success_enrichment_no_work_and_already_completed(self):
        for status in ('no_work','already_completed'):
            result,calls,output,errors=self.run_dispatch(status)
            self.assertEqual(result,0)
            self.assertEqual(calls,['score','enrichment'])
            self.assertIn(status,output)
            self.assertEqual(errors,'')

    def test_score_success_enrichment_completed(self):
        result,calls,output,errors=self.run_dispatch('completed')
        self.assertEqual(result,0)
        self.assertEqual(calls,['score','enrichment'])
        self.assertIn('"clubs": 20',output)
        self.assertLess(output.index('Scheduled score sync completed'),output.index('Pick Insight task'))
        self.assertEqual(errors,'')

    def test_enrichment_failure_does_not_fail_successful_score_sync_or_leak_details(self):
        for failure in (RuntimeError('token=private /data/private.db'), TimeoutError('private'), ValueError('private')):
            result,calls,output,errors=self.run_dispatch(failure=failure)
            self.assertEqual(result,0)
            self.assertEqual(calls,['score','enrichment'])
            self.assertIn('Scheduled score sync completed',output)
            self.assertIn('dispatch failed; score sync succeeded',errors)
            self.assertNotIn('private',output+errors)

    def test_score_failure_never_attempts_enrichment(self):
        result,calls,output,errors=self.run_dispatch(score_failure=RuntimeError('score unavailable'))
        self.assertEqual(result,1)
        self.assertEqual(calls,['score'])
        self.assertIn('Scheduled score sync failed',errors)

    def test_unset_enrichment_url_preserves_score_only_behavior(self):
        result,calls,output,errors=self.run_dispatch(url='   ')
        self.assertEqual(result,0)
        self.assertEqual(calls,['score'])
        self.assertNotIn('Pick Insight',output+errors)

    def test_enrichment_transport_posts_explicit_flask_url_and_only_returns_safe_fields(self):
        def opener(request,timeout):
            self.assertEqual(request.full_url,'https://pickem.example/tasks/sync-pick-insight')
            self.assertEqual(request.method,'POST')
            self.assertEqual(request.data,b'')
            self.assertEqual(request.get_header('X-sync-secret'),'test-secret')
            self.assertEqual(timeout,45)
            return FakeResponse(dict(ok=True,status='no_work',clubs=0,clubs_with_scorers=0,private='never log'))
        self.assertEqual(trigger_score_sync.dispatch_pick_insight(
            'https://pickem.example/tasks/sync-pick-insight','test-secret',45,opener),
            dict(status='no_work',clubs=0,clubs_with_scorers=0))

    def test_enrichment_rejects_unexpected_success_payload(self):
        for payload in (dict(ok=True,status='running',clubs=0,clubs_with_scorers=0),
                        dict(ok=True,status='completed',clubs='secret',clubs_with_scorers=0)):
            with self.assertRaises(RuntimeError):
                trigger_score_sync.dispatch_pick_insight('https://pickem.example/tasks/sync-pick-insight',
                    'test-secret',45,lambda *args,**kwargs:FakeResponse(payload))

    def test_every_cron_invocation_syncs_without_weekday_or_timezone_gate(self):
        with patch.dict(trigger_score_sync.os.environ, {
            "SYNC_URL": "https://pickem.example/tasks/sync-results",
            "SYNC_SECRET": "test-secret",
            "SYNC_SCHEDULE_TIMEZONE": "unused-legacy-value",
            "PICK_INSIGHT_SYNC_URL": "",
        }), patch.object(trigger_score_sync, "trigger_score_sync", return_value={"ok": True}) as sync:
            self.assertEqual(trigger_score_sync.main(), 0)
            sync.assert_called_once_with(
                "https://pickem.example/tasks/sync-results", "test-secret", timeout_seconds=180,
            )

    def test_trigger_posts_secret_and_returns_summary(self):
        captured = {}

        def fake_open(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse({
                "ok": True,
                "results_imported": 3,
                "pending_matches": 377,
            })

        summary = trigger_score_sync.trigger_score_sync(
            "https://pickem.example/tasks/sync-results",
            "test-secret",
            timeout_seconds=45,
            opener=fake_open,
        )

        self.assertEqual(summary["results_imported"], 3)
        self.assertEqual(captured["timeout"], 45)
        self.assertEqual(captured["request"].method, "POST")
        self.assertEqual(
            captured["request"].get_header("X-sync-secret"), "test-secret"
        )

    def test_trigger_rejects_failure_payload(self):
        def fake_open(request, timeout):
            return FakeResponse({"ok": False, "error": "No writable active season"})

        with self.assertRaisesRegex(RuntimeError, "reported failure"):
            trigger_score_sync.trigger_score_sync(
                "https://pickem.example/tasks/sync-results",
                "test-secret",
                opener=fake_open,
            )


if __name__ == "__main__":
    unittest.main()
