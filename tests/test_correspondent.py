import json
import unittest

from correspondent.writer import (
    DEFAULT_MODEL,
    PROMPT_VERSION,
    CorrespondentError,
    generate_weekly_recap,
    load_system_prompt,
)


class FakeResponse:
    id = "resp_test_123"
    output_text = json.dumps({
        "title": "A Grave Matter at the Summit",
        "body_markdown": "Steve won. The committee has opened an inquiry.",
    })


class FakeResponses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse()


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


class CorrespondentWriterTests(unittest.TestCase):
    def test_prompt_is_versioned_and_contains_factual_rules(self):
        prompt = load_system_prompt()

        self.assertEqual(PROMPT_VERSION, "weekly-recap-v1")
        self.assertIn("overly serious English football columnist", prompt)
        self.assertIn("Never invent", prompt)

    def test_writer_uses_responses_api_and_structured_output(self):
        client = FakeClient()
        context = {
            "schema_version": "weekly_recap.v1",
            "week": {"number": 4, "status": "finalized"},
        }

        recap = generate_weekly_recap(context, client=client, model="test-model")

        self.assertEqual(recap.title, "A Grave Matter at the Summit")
        self.assertEqual(recap.model, "test-model")
        self.assertEqual(recap.provider_response_id, "resp_test_123")
        self.assertEqual(client.responses.kwargs["model"], "test-model")
        self.assertEqual(
            client.responses.kwargs["text"]["format"]["type"],
            "json_schema",
        )
        self.assertIn('"number": 4', client.responses.kwargs["input"])

    def test_writer_rejects_invalid_json(self):
        class InvalidResponse:
            id = "resp_invalid"
            output_text = "not json"

        client = FakeClient()
        client.responses.create = lambda **kwargs: InvalidResponse()

        with self.assertRaisesRegex(CorrespondentError, "invalid recap JSON"):
            generate_weekly_recap({}, client=client, model=DEFAULT_MODEL)


if __name__ == "__main__":
    unittest.main()
