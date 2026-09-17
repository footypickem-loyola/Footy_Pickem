import json
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
    def test_every_cron_invocation_syncs_without_weekday_or_timezone_gate(self):
        with patch.dict(trigger_score_sync.os.environ, {
            "SYNC_URL": "https://pickem.example/tasks/sync-results",
            "SYNC_SECRET": "test-secret",
            "SYNC_SCHEDULE_TIMEZONE": "unused-legacy-value",
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
