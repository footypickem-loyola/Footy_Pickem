"""AI Correspondent support for Footy Pick 'Em."""

from .writer import (
    DEFAULT_MODEL,
    PROMPT_VERSION,
    CorrespondentError,
    GeneratedRecap,
    generate_weekly_recap,
)
from .context_v2 import build_v2_context
from .writer_v2 import (
    PROMPT_VERSION as V2_PROMPT_VERSION,
    GeneratedRecapV2,
    generate_weekly_recap_v2,
)
from .classifier import (
    CLASSIFIER_PROMPT_VERSION,
    ClassificationBatch,
    SourceClassification,
    build_classification_batch_request,
    classifier_client,
    classifier_model,
    classify_candidate_sources,
    validate_classification_response,
)

__all__ = [
    "DEFAULT_MODEL",
    "PROMPT_VERSION",
    "CorrespondentError",
    "GeneratedRecap",
    "generate_weekly_recap",
    "build_v2_context",
    "V2_PROMPT_VERSION",
    "GeneratedRecapV2",
    "generate_weekly_recap_v2",
    "CLASSIFIER_PROMPT_VERSION",
    "ClassificationBatch",
    "SourceClassification",
    "build_classification_batch_request",
    "classifier_client",
    "classifier_model",
    "classify_candidate_sources",
    "validate_classification_response",
]
