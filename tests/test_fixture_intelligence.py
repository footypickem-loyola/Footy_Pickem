import copy
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest

from fixture_intelligence import build_fixture_intelligence
from scripts.review_fixture_intelligence import inspect

START = datetime(2026, 8, 1, tzinfo=timezone.utc)


class EngineTests(unittest.TestCase):
    def packet(self, scores, venues=None):
        self.weeks = [NS(id=1, season_id=1)]
        self.fixtures, self.results = [], []
        for i, (gf, ga) in enumerate(scores, 1):
            home = venues[i-1] if venues else True
            f = NS(id=i, week_id=1, home="A" if home else "B", away="B" if home else "A", kickoff_utc=START + timedelta(days=i))
            self.fixtures.append(f)
            hs, aws = (gf, ga) if home else (ga, gf)
            self.results.append(NS(fixture_id=i, home_score=hs, away_score=aws,
                                   outcome="Draw" if hs == aws else "Home" if hs > aws else "Away",
                                   updated_at=f.kickoff_utc + timedelta(hours=2), source="manual"))
        self.target = NS(id=999, week_id=1, home="A", away="B", kickoff_utc=START + timedelta(days=100))
        self.fixtures.append(self.target)
        return self.build()

    def build(self, as_of=None):
        return build_fixture_intelligence(fixture=self.target, fixtures=self.fixtures, results=self.results,
                                          weeks=self.weeks, season_id=1, as_of=as_of or self.target.kickoff_utc)

    def fact(self, packet, kind, scope="overall", ranked=False):
        return next((c for c in packet["ranked_candidates" if ranked else "candidates"]
                     if c["subject_team"] == "A" and c["signal_type"] == kind and c["scope"] == scope), None)

    def test_four_clean_sheets_in_five_not_a_four_match_run(self):
        p = self.packet([(1, 0), (2, 0), (0, 1), (1, 0), (0, 0)])
        self.assertEqual(self.fact(p, "CLEAN_SHEET_LAST_5")["evidence"]["count"], 4)
        self.assertEqual(self.fact(p, "CLEAN_SHEET_RUN")["evidence"]["count"], 2)
        self.assertIsNotNone(self.fact(p, "CLEAN_SHEET_LAST_5", ranked=True))

    def test_four_wins_from_five_is_not_four_consecutive(self):
        p = self.packet([(2, 1), (2, 1), (0, 1), (2, 1), (2, 1)])
        self.assertEqual(self.fact(p, "WINNING_LAST_5")["evidence"]["count"], 4)
        self.assertEqual(self.fact(p, "WINNING_RUN")["evidence"]["count"], 2)

    def test_long_drought_and_conditional(self):
        p = self.packet([(1, 1)] * 12)
        self.assertEqual(self.fact(p, "NO_CLEAN_SHEET_RUN")["evidence"]["count"], 12)
        self.assertEqual(self.fact(p, "NO_CLEAN_SHEET_CONTINUATION")["evidence"]["would_be"], 13)
        self.assertEqual(self.fact(p, "NO_CLEAN_SHEET_SEASON_START")["evidence"]["comparison"], "this season only")

    def test_compound_contrast(self):
        p = self.packet([(0, 0), (0, 1)] * 5, [True, False] * 5)
        c = self.fact(p, "FORM_CONTRAST", "split", ranked=True)
        self.assertEqual(c["evidence"]["overall_winless"], 10)
        self.assertEqual(c["evidence"]["venue_unbeaten"], 5)
        self.assertEqual(len(c["components"]), 2)
        self.assertIsNotNone(self.fact(p, "HOME_AWAY_SPLIT", "split"))
        self.assertIsNotNone(self.fact(p, "OVERALL_VENUE_CONTRAST", "split"))

    def test_weak_two_of_five_filtered_but_retained(self):
        p = self.packet([(1, 0), (0, 1), (0, 1), (1, 0), (0, 1)])
        weak = self.fact(p, "CLEAN_SHEET_LAST_5")
        self.assertEqual(weak["evidence"]["count"], 2)
        self.assertIsNone(self.fact(p, "CLEAN_SHEET_LAST_5", ranked=True))
        self.assertTrue(any(d["candidate_id"] == weak["id"] and d["reason"] == "below_editorial_threshold" for d in p["ranking_decisions"]))

    def test_future_and_equal_kickoffs_do_not_change_packet(self):
        before = self.packet([(1, 0)] * 5)
        for i in (1000, 1001):
            self.fixtures.append(NS(id=i, week_id=1, home="A", away="B", kickoff_utc=self.target.kickoff_utc + timedelta(days=i-1000)))
            self.results.append(NS(fixture_id=i, outcome="Away", home_score=0, away_score=9,
                                   updated_at=self.target.kickoff_utc - timedelta(days=1), source="manual"))
        self.assertEqual(before, self.build())

    def test_availability_cutoff_gaps_and_unknown_scores(self):
        self.packet([(1, 0)] * 5)
        self.results[-2].updated_at = self.target.kickoff_utc
        p = self.build()
        self.assertEqual(self.fact(p, "WINNING_RUN")["evidence"]["count"], 1)
        self.assertIsNone(self.fact(p, "WINNING_LAST_5"))
        self.results[-1].home_score = None
        self.assertIsNone(self.fact(self.build(), "CLEAN_SHEET_RUN"))
        self.assertIsNotNone(self.fact(self.build(), "WINNING_RUN"))

    def test_now_bound_and_season_isolation(self):
        self.packet([(1, 0)] * 5)
        p = self.build(START + timedelta(days=3))
        self.assertEqual(self.fact(p, "WINNING_RUN")["evidence"]["count"], 2)
        self.weeks.append(NS(id=2, season_id=2))
        self.fixtures.append(NS(id=1234, week_id=2, home="A", away="B", kickoff_utc=START))
        self.assertEqual(p, self.build(START + timedelta(days=3)))

    def test_unknown_dates_invalid_scores_and_duplicate_inputs(self):
        self.packet([(1, 0)] * 5)
        self.results[-1].home_score = -1
        self.assertIsNone(self.fact(self.build(), "CLEAN_SHEET_RUN"))
        self.results[-1].home_score = 0  # inconsistent with Home outcome
        self.assertIsNone(self.fact(self.build(), "CLEAN_SHEET_RUN"))
        self.fixtures[0].kickoff_utc = None
        self.assertEqual(self.build()["candidates"], [])
        self.target.kickoff_utc = None
        with self.assertRaises(ValueError):
            self.build(as_of=START)

    def test_deterministic_and_no_input_mutation(self):
        before = self.packet([(2, 0), (1, 1), (3, 1), (0, 0), (1, 0)])
        snapshot = copy.deepcopy(self.results)
        random.Random(3).shuffle(self.fixtures)
        random.Random(5).shuffle(self.results)
        self.assertEqual(before, self.build())
        self.assertEqual({r.fixture_id: vars(r) for r in snapshot}, {r.fixture_id: vars(r) for r in self.results})
        json.dumps(before)
        self.assertEqual(len({c["id"] for c in before["candidates"]}), len(before["candidates"]))

    def test_full_league_defensive_extremes_and_insufficient_coverage(self):
        self.packet([(1, 0)] * 5)
        for pair in range(9):
            for day in range(1, 6):
                fid = 10 + pair * 5 + day
                self.fixtures.append(NS(id=fid, week_id=1, home=f"C{pair}", away=f"D{pair}", kickoff_utc=START + timedelta(days=day)))
                self.results.append(NS(fixture_id=fid, outcome="Draw", home_score=2, away_score=2,
                                       updated_at=START + timedelta(days=day, hours=2), source="manual"))
        p = self.build()
        self.assertEqual(self.fact(p, "BEST_DEFENCE")["evidence"]["goals_against"], 0)
        for r in self.results[:5]:
            r.home_score, r.away_score, r.outcome = 0, 4, "Away"
        self.assertEqual(self.fact(self.build(), "WORST_DEFENCE")["evidence"]["goals_against"], 20)
        self.results[-1].away_score = None
        self.assertIsNone(self.fact(self.build(), "WORST_DEFENCE"))

    def test_debug_sqlite_does_not_change_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.db"
            with closing(sqlite3.connect(path)) as db:
                db.executescript("CREATE TABLE weeks(id INTEGER, season_id INTEGER); CREATE TABLE fixtures(id INTEGER, week_id INTEGER, home TEXT, away TEXT, kickoff_utc TEXT); CREATE TABLE results(fixture_id INTEGER, outcome TEXT, home_score INTEGER, away_score INTEGER, updated_at TEXT, source TEXT); INSERT INTO weeks VALUES(1,1); INSERT INTO fixtures VALUES(1,1,'A','B','2026-08-10 12:00:00');")
            before = path.read_bytes()
            p = inspect(path, 1, START)
            self.assertEqual(p["candidates"], [])
            self.assertEqual(path.read_bytes(), before)

    def test_run_families_and_away_scope(self):
        p = self.packet([(0, 2)] * 6, [False] * 6)
        for kind in ("LOSING_RUN", "WINLESS_RUN", "NO_SCORING_RUN", "NO_CLEAN_SHEET_RUN"):
            self.assertEqual(self.fact(p, kind, "away")["evidence"]["count"], 6)
        p = self.packet([(3, 0)] * 6, [False] * 6)
        for kind in ("WINNING_RUN", "UNBEATEN_RUN", "SCORING_RUN", "CLEAN_SHEET_RUN"):
            self.assertEqual(self.fact(p, kind, "away")["evidence"]["count"], 6)
        self.assertIsNone(self.fact(p, "WINNING_CONTINUATION", "away", ranked=True))
        self.assertEqual(self.fact(p, "RECENT_GOALS", "away")["evidence"]["goals_for"], 15)

    def test_season_opening_record_is_not_historical_comparison(self):
        p = self.packet([(2, 0), (1, 0), (0, 0), (3, 0)])
        record = self.fact(p, "SEASON_START_RECORD")
        self.assertEqual(record["evidence"]["wins"], 3)
        self.assertEqual(record["evidence"]["played"], 4)
        self.assertEqual(record["evidence"]["comparison"], "this season only")

    def test_naive_utc_and_timezone_offsets_are_equivalent(self):
        before = self.packet([(2, 0)] * 5)
        for f in self.fixtures:
            f.kickoff_utc = f.kickoff_utc.replace(tzinfo=None)
        for r in self.results:
            r.updated_at = r.updated_at.astimezone(timezone(timedelta(hours=-4)))
        self.assertEqual(before, self.build())

    def test_duplicate_results_and_fixtures_rejected(self):
        self.packet([(1, 0)] * 5)
        self.results.append(self.results[0])
        with self.assertRaises(ValueError):
            self.build()
        self.results.pop()
        self.fixtures.append(self.fixtures[0])
        with self.assertRaises(ValueError):
            self.build()

    def test_run_redundancy_retains_distinct_frequency_evidence(self):
        p = self.packet([(1, 0), (1, 0), (0, 0), (1, 0), (1, 0)])
        # Four wins is distinct from five unbeaten, even with identical IDs.
        unbeaten = self.fact(p, "UNBEATEN_SEASON_START", ranked=True)
        self.assertIsNotNone(unbeaten)
        # The win count may be represented by the richer season-opening W/D/L.
        self.assertTrue(self.fact(p, "WINNING_LAST_5", ranked=True) or self.fact(p, "SEASON_START_RECORD", ranked=True))
        run = self.fact(p, "UNBEATEN_RUN")
        decisions = {d["candidate_id"]: d for d in p["ranking_decisions"]}
        self.assertEqual(decisions[run["id"]]["suppressed_by"], unbeaten["id"])


if __name__ == "__main__":
    unittest.main()
