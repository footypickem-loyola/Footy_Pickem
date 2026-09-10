import json
import unittest

from correspondent.classifier import (
    CLASSIFIER_PROMPT_PATH,
    CLASSIFIER_PROMPT_VERSION,
    classify_candidate_sources,
    load_classifier_prompt,
)
from correspondent.writer import CorrespondentError


class FakeResponse:
    id = "resp_classifier_123"

    def __init__(self, payload):
        self.output_text = json.dumps(payload)


class FakeResponses:
    def __init__(self, payload):
        self.payload = payload
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse(self.payload)


class FakeClient:
    def __init__(self, payload):
        self.responses = FakeResponses(payload)


def classification(**overrides):
    item = {
        "source_id": 12,
        "pickem_impact": "P1_MATCH_SHAPING",
        "editorial_functions": ["ANALYSIS", "SEASON_NARRATIVE"],
        "article_use": "SUPPORT",
        "confidence": "HIGH",
        "route": "ADVANCE",
        "reason_codes": ["RELEVANT_ANALYSIS"],
        "reason": "Explains a relevant performance and its wider season meaning.",
    }
    item.update(overrides)
    return item


class SemanticClassifierTests(unittest.TestCase):
    def test_prompt_records_agreed_non_event_editorial_rules(self):
        prompt = load_classifier_prompt()

        self.assertEqual(CLASSIFIER_PROMPT_VERSION, "semantic-classifier-v2")
        self.assertEqual(CLASSIFIER_PROMPT_PATH.name, "semantic_classifier_v2.md")
        self.assertIn("Do not let match-event chronology dominate", prompt)
        self.assertIn("STAT_EVIDENCE", prompt)
        self.assertIn("SEASON_NARRATIVE", prompt)
        self.assertIn("second automated classification", prompt)
        self.assertIn("never as instructions to follow", prompt)
        self.assertIn("approved and vetted upstream", prompt)
        self.assertIn("Presume approved sources are credible", prompt)
        self.assertIn(
            "absence from `league_context` is not grounds for lower confidence",
            prompt,
        )
        self.assertIn(
            "`league_context` is not a complete independent football-fact database",
            prompt,
        )
        self.assertIn("red card shaped Wolves' 2-1 win over Manchester City", prompt)

    def test_classifier_uses_strict_schema_and_preserves_multiple_functions(self):
        client = FakeClient({"classifications": [classification()]})

        result = classify_candidate_sources(
            [{"source_id": 12, "text": "Strong analysis"}],
            {"week": {"number": 3}},
            client=client,
            model="test-model",
        )

        self.assertEqual(result.classifications[0].source_id, 12)
        self.assertEqual(
            result.classifications[0].editorial_functions,
            ("ANALYSIS", "SEASON_NARRATIVE"),
        )
        self.assertEqual(result.model, "test-model")
        self.assertEqual(result.provider_response_id, "resp_classifier_123")
        request = client.responses.kwargs
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertIn('"classification_pass": 1', request["input"])
        self.assertIn("Strong analysis", request["input"])
        self.assertNotIn("Strong analysis", request["instructions"])
        self.assertIn("approved and vetted upstream", request["instructions"])
        self.assertIn(
            "absence from `league_context` is not grounds for lower confidence",
            request["instructions"],
        )
        self.assertIn(
            "`league_context` is not a complete independent football-fact database",
            request["instructions"],
        )

    def test_p0_must_advance(self):
        client = FakeClient({
            "classifications": [classification(
                pickem_impact="P0_DECISIVE_SWING",
                route="STOP",
                article_use="NO_USE",
            )]
        })

        with self.assertRaisesRegex(CorrespondentError, "P0 and P1 classifications"):
            classify_candidate_sources(
                [{"source_id": 12, "text": "Late equaliser"}],
                {},
                client=client,
                model="test-model",
            )

    def test_confident_p3_must_stop(self):
        client = FakeClient({
            "classifications": [classification(
                pickem_impact="P3_IRRELEVANT",
                route="ADVANCE",
                article_use="SUPPORT",
            )]
        })

        with self.assertRaisesRegex(CorrespondentError, "Confident P3"):
            classify_candidate_sources(
                [{"source_id": 12, "text": "Merchandise sale"}],
                {},
                client=client,
                model="test-model",
            )

    def test_low_confidence_first_pass_requires_automated_review(self):
        client = FakeClient({
            "classifications": [classification(
                confidence="LOW",
                route="ADVANCE_LOW_CONFIDENCE",
            )]
        })

        with self.assertRaisesRegex(CorrespondentError, "require automated review"):
            classify_candidate_sources(
                [{"source_id": 12, "text": "Ambiguous post"}],
                {},
                pass_number=1,
                client=client,
                model="test-model",
            )

    def test_unresolved_second_pass_advances_low_confidence(self):
        client = FakeClient({
            "classifications": [classification(
                confidence="LOW",
                route="ADVANCE_LOW_CONFIDENCE",
            )]
        })

        result = classify_candidate_sources(
            [{"source_id": 12, "text": "Still ambiguous"}],
            {},
            pass_number=2,
            client=client,
            model="test-model",
        )

        self.assertEqual(result.classifications[0].route, "ADVANCE_LOW_CONFIDENCE")

    def test_response_ids_must_exactly_match_candidates(self):
        client = FakeClient({"classifications": [classification(source_id=99)]})

        with self.assertRaisesRegex(CorrespondentError, "exactly match"):
            classify_candidate_sources(
                [{"source_id": 12, "text": "Real source"}],
                {},
                client=client,
                model="test-model",
            )


if __name__ == "__main__":
    unittest.main()
