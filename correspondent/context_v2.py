"""Pure context assembly for Correspondent V2.

The Flask application remains responsible for querying and validating game
data.  This module only combines the proven V1 fact packet with normalized,
application-owned external source records.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def serialize_source(
    source: Any,
    classification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the bounded source shape that may be sent to the writer."""
    submitter = getattr(source, "submitted_by", None)
    serialized = {
        "source_id": source.id,
        "provider": source.provider,
        "source_type": source.source_type,
        "canonical_url": source.canonical_url,
        "author": source.author_name,
        "text": source.body_text,
        "published_at": _isoformat(source.published_at),
        "submitted_by": None if submitter is None else submitter.name,
        "submission_note": source.submission_note,
    }
    if classification is not None:
        serialized["classification"] = dict(classification)
    return serialized


def build_v2_context(
    league_context: Mapping[str, Any],
    sources: Iterable[Any],
    classifications: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Combine authoritative V1 facts with normalized external candidates."""
    candidates = [
        serialize_source(
            source,
            None if classifications is None else classifications.get(source.id),
        )
        for source in sources
    ]
    if not candidates:
        raise ValueError("Correspondent V2 requires at least one accepted source")
    return {
        "schema_version": "weekly_recap.v2",
        "league_context": dict(league_context),
        "external_context": {
            "candidate_source_count": len(candidates),
            "candidate_sources": candidates,
        },
    }
