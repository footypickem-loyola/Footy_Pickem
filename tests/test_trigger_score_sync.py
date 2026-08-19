import json
import unittest
from datetime import datetime, timezone

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
    def test_schedule_runs_every_quarter_hour_on_eastern_weekends(self):
        saturday = datetime(2026, 8, 22, 16, 15, tzinfo=timezone.utc)

        self.assertTrue(trigger_score_sync.should_sync_now(saturday))

    def test_schedule_runs_only_at_top_of_hour_on_eastern_weekdays(self):
        monday_quarter_hour = datetime(2026, 8, 24, 16, 15, tzinfo=timezone.utc)
        monday_top_of_hour = datetime(2026, 8, 24, 17, 0, tzinfo=timezone.utc)

        self.assertFalse(trigger_score_sync.should_sync_now(monday_quarter_hour))
        self.assertTrue(trigger_score_sync.should_sync_now(monday_top_of_hour))

    def test_schedule_uses_eastern_day_at_utc_boundary(self):
        saturday_utc_friday_eastern = datetime(
            2026, 8, 22, 0, 15, tzinfo=timezone.utc
        )

        self.assertFalse(
            trigger_score_sync.should_sync_now(saturday_utc_friday_eastern)
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
