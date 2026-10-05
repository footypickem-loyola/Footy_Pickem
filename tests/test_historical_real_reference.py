"""Small football-only extract of the user's verified PR20 snapshot; no API calls."""
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace as NS
import unittest

from fixture_history import load_fixture_history
from fixture_intelligence import build_fixture_intelligence
from football_reference_schema import create_schema

SAMPLE = Path(__file__).parent / "fixtures" / "fixture-history-real-sample.json"


class RealReferenceTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(SAMPLE.read_text(encoding="utf-8"))
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        create_schema(self.db)
        for suffix in ("seasons", "fixtures", "events"):
            for row in self.data[suffix]:
                columns = list(row)
                self.db.execute(f"INSERT INTO football_reference_{suffix} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", list(row.values()))
        self.db.commit()
        self.db.execute("PRAGMA query_only=ON")

    def packet(self, fixture_id):
        row = next(t for t in self.data["targets"] if t["id"] == fixture_id)
        f = NS(**dict(row, week_id=1, kickoff_utc=datetime.fromisoformat(row["kickoff_utc"])))
        as_of = datetime.fromisoformat(self.data["cutoff"])
        history = load_fixture_history(self.db, fixture=f, as_of=as_of)
        return build_fixture_intelligence(fixture=f, fixtures=[f], weeks=[NS(id=1, season_id=1)],
            season_id=1, results=[], as_of=as_of, reference_history=history)

    def fact(self, packet, kind, **values):
        return next(c for c in packet["candidates"] if c["signal_type"] == kind
                    and all(c["evidence"].get(k) == v for k, v in values.items()))

    def test_exact_arsenal_leeds_real_fixture(self):
        p = self.packet(811)
        r = p["history"]["exact_prior_season_fixture"]
        self.assertEqual((r["reference_fixture_id"], r["external_fixture_id"]), (491, 19427465))
        self.assertEqual((r["home_score"], r["away_score"], r["season"]), (5, 0, "2025/2026"))

    def test_eze_hat_trick_and_five_goal_meeting_total(self):
        p = self.packet(1098)
        hat = self.fact(p, "HAT_TRICK", player_id=7643)
        self.assertEqual(hat["provenance"]["external_event_ids"], [152199999, 152200527, 152201396])
        self.assertEqual(hat["evidence"]["goals"], 3)
        total = self.fact(p, "PLAYER_VS_OPPONENT", player_id=7643)
        self.assertEqual((total["evidence"]["goals"], total["evidence"]["meetings"]), (5, 4))
        self.assertIsNone(total["evidence"]["appearance_count"])
        anchor = self.fact(p, "EXACT_PRIOR_SEASON_FIXTURE")
        decision = next(d for d in p["ranking_decisions"] if d["candidate_id"] == anchor["id"])
        self.assertEqual(decision["reason"], "historical_anchor_covered_by_event")
        self.assertEqual(decision["suppressed_by"], hat["id"])
        runs = [c for c in p["ranked_candidates"] if c["signal_type"].startswith("H2H_")]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["signal_type"], "H2H_WINNING_RUN")

    def test_haaland_90_plus_3_winner(self):
        p = self.packet(819)
        c = self.fact(p, "LATE_DECISIVE_GOAL", player_id=154421)
        self.assertEqual((c["evidence"]["kind"], c["evidence"]["minute"], c["evidence"]["extra_minute"]), ("winner", 90, 3))
        self.assertEqual(c["evidence"]["final_score"], [1, 2])
        self.assertIn(152728950, c["provenance"]["external_event_ids"])

    def test_de_ligt_90_plus_6_equalizer_reverse_fixture(self):
        p = self.packet(816)
        c = self.fact(p, "LATE_DECISIVE_GOAL", player_id=26768)
        self.assertEqual((c["evidence"]["kind"], c["evidence"]["extra_minute"]), ("equalizer", 6))
        self.assertEqual(c["provenance"]["fixtures"][0]["external_fixture_id"], 19427564)
        self.assertNotEqual(p["history"]["exact_prior_season_fixture"]["external_fixture_id"], 19427564)

    def test_chelsea_four_meeting_lower_bound_and_late_equalizer(self):
        p = self.packet(813)
        c = next(c for c in p["candidates"] if c["signal_type"] == "H2H_UNBEATEN_RUN" and c["subject_team"] == "Chelsea")
        self.assertEqual((c["evidence"]["count"], c["evidence"]["lower_bound"]), (4, True))
        equalizer = self.fact(p, "LATE_DECISIVE_GOAL", player_id=581220)
        self.assertEqual((equalizer["evidence"]["minute"], equalizer["evidence"]["extra_minute"]), (90, 5))


if __name__ == "__main__":
    unittest.main()
