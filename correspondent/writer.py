"""OpenAI transport for the weekly Footy Pick 'Em recap.

This module deliberately knows nothing about Flask, SQLAlchemy, or the game
database. The application supplies a complete, deterministic context payload;
the model is responsible only for editorial judgment and writing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


PROMPT_VERSION = "weekly-recap-v1"
DEFAULT_MODEL = "gpt-5.6-luna"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "weekly_recap_v1.md"


class CorrespondentError(RuntimeError):
    """Raised when a recap cannot be generated or validated."""


@dataclass(frozen=True)
class GeneratedRecap:
    title: str
    body_markdown: str
    model: str
    provider_response_id: Optional[str] = None


def load_system_prompt() -> str:
    try:
        prompt = PROMPT_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise CorrespondentError(f"Unable to load Correspondent prompt: {exc}") from exc
    if not prompt:
        raise CorrespondentError("Correspondent prompt is empty")
    return prompt


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body_markdown": {"type": "string"},
        },
        "required": ["title", "body_markdown"],
        "additionalProperties": False,
    }


def _validate_generated_payload(payload: Any, model: str, response_id: Optional[str]) -> GeneratedRecap:
    if not isinstance(payload, dict):
        raise CorrespondentError("OpenAI returned an invalid recap object")
    title = payload.get("title")
    body = payload.get("body_markdown")
    if not isinstance(title, str) or not title.strip():
        raise CorrespondentError("OpenAI returned a recap without a title")
    if not isinstance(body, str) or not body.strip():
        raise CorrespondentError("OpenAI returned a recap without body text")
    return GeneratedRecap(
        title=title.strip(),
        body_markdown=body.strip(),
        model=model,
        provider_response_id=response_id,
    )


def generate_weekly_recap(
    context: Mapping[str, Any],
    *,
    client: Any = None,
    model: Optional[str] = None,
) -> GeneratedRecap:
    """Generate a structured weekly recap from an application-owned fact packet."""
    selected_model = (model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL).strip()
    if not selected_model:
        raise CorrespondentError("OPENAI_MODEL is not configured")

    if client is None:
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise CorrespondentError("OPENAI_API_KEY is not configured")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise CorrespondentError("The openai Python package is not installed") from exc
        client = OpenAI(timeout=90.0, max_retries=2)

    context_json = json.dumps(context, ensure_ascii=False, sort_keys=True, indent=2)
    try:
        response = client.responses.create(
            model=selected_model,
            instructions=load_system_prompt(),
            input=(
                "Write the weekly Footy Pick 'Em recap using only the factual "
                "context below.\n\n"
                f"{context_json}"
            ),
            text={
                "format": {
                    "type": "json_schema",
                    "name": "footy_pickem_weekly_recap",
                    "strict": True,
                    "schema": _response_schema(),
                }
            },
            max_output_tokens=2500,
        )
    except CorrespondentError:
        raise
    except Exception as exc:
        raise CorrespondentError(f"OpenAI recap generation failed: {exc}") from exc

    output_text = getattr(response, "output_text", None)
    if not isinstance(output_text, str) or not output_text.strip():
        raise CorrespondentError("OpenAI returned an empty recap response")
    try:
        payload = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise CorrespondentError("OpenAI returned invalid recap JSON") from exc

    return _validate_generated_payload(
        payload,
        selected_model,
        getattr(response, "id", None),
    )
