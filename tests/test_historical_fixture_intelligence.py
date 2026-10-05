from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest

from fixture_history import load_fixture_history
from fixture_intelligence import build_fixture_intelligence
from football_reference_schema import create_schema


class HistoricalTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        create_schema(self.db)
        for sid, year in ((1, 2024), (2, 2025), (3, 2026), (4, 2023)):
            self.db.execute("INSERT INTO football_reference_seasons VALUES(?, 'sportmonks', ?, 8, ?, 1, 0, 'completed', 380, '2026-10-04T00:00:00+00:00')",
                            (sid, sid*100, f"{year}/{year+1}"))
        self.target = NS(id=999, week_id=1, home="Arsenal", away="Man United", kickoff_utc=datetime(2026, 8, 20, tzinfo=timezone.utc))
        self.as_of = self.target.kickoff_utc

    def fixture(self, fid, kickoff, score=(2, 0), home=19, away=14, season=2, state=5):
        names = {19: "Arsenal", 14: "Manchester United", 9: "Manchester City", 6: "Tottenham Hotspur"}
        self.db.execute("""INSERT INTO football_reference_fixtures
            (id,provider,external_fixture_id,reference_season_id,league_id,kickoff_utc,
             home_team_id,home_team_name,away_team_id,away_team_name,home_score,away_score,state_id,state,created_at,updated_at)
            VALUES(?, 'sportmonks', ?, ?, 8, ?, ?, ?, ?, ?, ?, ?, ?, 'finished', '2026-10-04', '2026-10-04')""",
            (fid, fid+10000, season, kickoff, home, names[home], away, names[away], *score, state))

    def goal(self, eid, fid, minute, running, player=100, team=19, type_id=14, extra=None, order=None, active=1):
        self.db.execute("""INSERT INTO football_reference_events
            (id, reference_fixture_id, provider, external_event_id, type_id, event_type, minute,
             extra_minute, team_provider_id, player_provider_id, player_name, running_score, sort_order,
             rescinded, is_present, is_active, created_at, updated_at)
            VALUES(?, ?, 'sportmonks', ?, ?, 'goal', ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, '2026-10-04','2026-10-04')""",
            (eid, fid, eid+100000, type_id, minute, extra, team, player, f"Player {player}", running,
             order if order is not None else eid, active))

    def history(self):
        return load_fixture_history(self.db, fixture=self.target, as_of=self.as_of)

    def packet(self):
        return build_fixture_intelligence(fixture=self.target, fixtures=[self.target], results=[],
            weeks=[NS(id=1, season_id=1)], season_id=1, as_of=self.as_of, reference_history=self.history())

    def candidates(self, kind, ranked=False):
        return [c for c in self.packet()["ranked_candidates" if ranked else "candidates"] if c["signal_type"] == kind]

    def test_exact_prior_season_orientation_and_latest_reverse(self):
        self.fixture(1, "2024-09-01T15:00:00+00:00", season=1)
        self.fixture(2, "2025-09-01T15:00:00+00:00")
        self.fixture(3, "2026-03-01T15:00:00+00:00", home=14, away=19)
        h = self.history()
        self.assertEqual(h["exact_prior_season_fixture"]["reference_fixture_id"], 2)
        self.assertEqual(h["most_recent_meeting"]["reference_fixture_id"], 3)
        self.assertEqual([r["reference_fixture_id"] for r in h["venue_h2h"]], [2, 1])
        self.assertEqual(self.candidates("EXACT_PRIOR_SEASON_FIXTURE")[0]["evidence"]["season"], "2025/2026")

    def test_prior_season_gap_does_not_claim_last_season(self):
        self.fixture(1, "2024-09-01T15:00:00+00:00", season=1)
        c = self.candidates("EXACT_PRIOR_SEASON_FIXTURE")[0]
        self.assertFalse(c["evidence"]["immediately_previous_season"])
        self.assertIn("2024/2025", c["claim"])

    def test_last_five_order_lower_bound_and_venue_minimum(self):
        for i in range(1, 7):
            self.fixture(i, f"2025-{i:02d}-01T15:00:00+00:00", home=19 if i % 2 else 14,
                         away=14 if i % 2 else 19, score=(2, 0) if i % 2 else (0, 2), season=1)
        h = self.history()
        self.assertEqual([r["reference_fixture_id"] for r in h["recent_h2h"]], [6, 5, 4, 3, 2])
        c = self.candidates("H2H_WINNING_RUN")[0]
        self.assertEqual(c["evidence"]["count"], 5)
        self.assertTrue(c["evidence"]["lower_bound"])
        self.assertEqual(self.candidates("VENUE_H2H_WINNING_RUN")[0]["evidence"]["count"], 3)

    def test_streak_stops_on_draw_and_gap(self):
        for i in range(1, 5):
            self.fixture(i, f"2025-{i:02d}-01T15:00:00+00:00", score=(0, 0) if i == 1 else (1, 0), season=1)
        c = self.candidates("H2H_WINNING_RUN")[0]
        self.assertEqual(c["evidence"]["count"], 3)
        self.assertFalse(c["evidence"]["lower_bound"])
        self.fixture(5, "2025-03-15T15:00:00+00:00", score=(None, None), state=1)
        self.assertEqual(self.candidates("H2H_WINNING_RUN"), [])

    def test_single_venue_not_a_streak(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00")
        self.assertEqual(self.candidates("VENUE_H2H_WINNING_RUN"), [])

    def test_hat_trick_outscores_and_suppresses_anchor_but_history_remains(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00", score=(3, 0))
        for i in range(1, 4):
            self.goal(i, 1, i*20, f"{i}-0")
        hat = self.candidates("HAT_TRICK", ranked=True)[0]
        self.assertEqual(hat["evidence"]["goals"], 3)
        self.assertEqual(self.candidates("EXACT_PRIOR_SEASON_FIXTURE", ranked=True), [])
        self.assertEqual(self.candidates("PLAYER_VS_OPPONENT", ranked=True), [])
        self.assertIsNotNone(self.packet()["history"]["exact_prior_season_fixture"])

    def test_away_hat_trick_also_suppresses_home_anchor(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00", score=(0, 3))
        for i in range(1, 4):
            self.goal(i, 1, i*20, f"0-{i}", team=14)
        self.assertEqual(len(self.candidates("HAT_TRICK", ranked=True)), 1)
        self.assertEqual(self.candidates("EXACT_PRIOR_SEASON_FIXTURE", ranked=True), [])

    def test_rescinded_and_withdrawn_events_never_make_a_brace(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00", score=(1, 0))
        self.goal(1, 1, 30, "1-0")
        self.goal(2, 1, 60, "2-0", active=0)
        self.goal(3, 1, 90, "3-0")
        self.db.execute("UPDATE football_reference_events SET is_present=0 WHERE id=3")
        self.assertEqual(self.candidates("BRACE"), [])
        self.assertEqual(self.candidates("PLAYER_VS_OPPONENT")[0]["evidence"]["goals"], 1)

    def test_brace_and_own_goal_exclusion(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00", score=(2, 1))
        self.goal(1, 1, 10, "1-0")
        self.goal(2, 1, 20, "2-0")
        self.goal(3, 1, 30, "2-1", type_id=15)
        self.assertEqual(self.candidates("HAT_TRICK"), [])
        self.assertEqual(self.candidates("BRACE")[0]["evidence"]["goals"], 2)
        self.assertEqual(self.candidates("PLAYER_VS_OPPONENT")[0]["evidence"]["goals"], 2)

    def test_stoppage_time_winner(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00", score=(1, 0))
        self.goal(1, 1, 90, "1-0", extra=4)
        c = self.candidates("LATE_DECISIVE_GOAL")[0]
        self.assertEqual((c["evidence"]["kind"], c["evidence"]["minute"], c["evidence"]["extra_minute"]), ("winner", 90, 4))
        self.assertEqual(c["provenance"]["external_event_ids"], [100001])

    def test_late_equalizer_and_insurance_not_decisive(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00", score=(1, 1))
        self.goal(1, 1, 20, "0-1", team=14)
        self.goal(2, 1, 88, "1-1")
        self.assertEqual(self.candidates("LATE_DECISIVE_GOAL")[0]["evidence"]["kind"], "equalizer")
        self.db.execute("UPDATE football_reference_fixtures SET home_score=2,away_score=0")
        self.db.execute("UPDATE football_reference_events SET running_score='1-0',team_provider_id=19 WHERE id=1")
        self.db.execute("UPDATE football_reference_events SET running_score='2-0' WHERE id=2")
        self.assertEqual(self.candidates("LATE_DECISIVE_GOAL"), [])

    def test_late_temporary_lead_is_not_final_winner(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00", score=(1, 1))
        self.goal(1, 1, 85, "1-0")
        self.goal(2, 1, 90, "1-1", team=14)
        c = self.candidates("LATE_DECISIVE_GOAL")
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["evidence"]["kind"], "equalizer")

    def test_incomplete_or_inconsistent_timeline_fails_closed(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00", score=(2, 0))
        self.goal(1, 1, 90, "2-0")
        self.assertEqual(self.candidates("LATE_DECISIVE_GOAL"), [])
        self.assertEqual(self.candidates("PLAYER_VS_OPPONENT"), [])
        self.goal(2, 1, 89, "1-0", order=1)
        self.assertEqual(self.candidates("LATE_DECISIVE_GOAL"), [])  # ambiguous order

    def test_player_totals_are_meetings_not_appearances(self):
        for i in range(1, 5):
            self.fixture(i, f"2025-{i:02d}-01T15:00:00+00:00", score=(1, 0), season=1)
            self.goal(i, i, 30, "1-0", player=100 if i < 4 else 200)
        c = next(c for c in self.candidates("PLAYER_VS_OPPONENT") if c["evidence"]["player_id"] == 100)
        self.assertEqual((c["evidence"]["goals"], c["evidence"]["meetings"]), (3, 4))
        self.assertIsNone(c["evidence"]["appearance_count"])
        self.assertIn("stored PL meetings", c["claim"])

    def test_missing_goal_in_one_meeting_withholds_exact_player_total(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00", score=(2, 0))
        self.goal(1, 1, 30, "1-0")
        self.fixture(2, "2026-01-01T15:00:00+00:00", score=(1, 0))
        self.goal(2, 2, 30, "1-0")
        self.assertEqual(self.candidates("PLAYER_VS_OPPONENT"), [])

    def test_cutoff_excludes_same_and_later_fixtures(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00")
        before = self.packet()
        self.fixture(2, self.target.kickoff_utc.isoformat(), season=3)
        self.fixture(3, "2026-09-01T00:00:00+00:00", season=3)
        self.assertEqual(before, self.packet())
        self.as_of = datetime(2025, 8, 1, tzinfo=timezone.utc)
        self.assertEqual(self.history()["recent_h2h"], [])

    def test_unknown_alias_fails_closed(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00")
        self.target.home = "Arsenel"
        self.assertEqual(self.history()["status"], "unmapped_or_ambiguous_club")
        self.assertEqual(self.packet()["candidates"], [])

    def test_select_only_and_json_deterministic(self):
        self.fixture(1, "2025-09-01T15:00:00+00:00")
        self.db.commit()
        before = list(self.db.iterdump())
        self.db.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY if action in
            (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE) else sqlite3.SQLITE_OK)
        self.assertEqual(json.dumps(self.packet(), sort_keys=True), json.dumps(self.packet(), sort_keys=True))
        self.assertEqual(list(self.db.iterdump()), before)

    def test_missing_reference_schema_keeps_current_engine_available(self):
        with closing(sqlite3.connect(":memory:")) as empty:
            h = load_fixture_history(empty, fixture=self.target, as_of=self.as_of)
        self.assertEqual(h["status"], "reference_unavailable")
        p = build_fixture_intelligence(fixture=self.target, fixtures=[self.target], results=[],
            weeks=[NS(id=1, season_id=1)], season_id=1, as_of=self.as_of, reference_history=h)
        self.assertIn("history", p)


if __name__ == "__main__":
    unittest.main()
