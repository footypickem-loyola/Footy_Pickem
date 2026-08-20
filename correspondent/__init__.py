"""AI Correspondent support for Footy Pick 'Em."""

from .writer import (
    DEFAULT_MODEL,
    PROMPT_VERSION,
    CorrespondentError,
    GeneratedRecap,
    generate_weekly_recap,
)

__all__ = [
    "DEFAULT_MODEL",
    "PROMPT_VERSION",
    "CorrespondentError",
    "GeneratedRecap",
    "generate_weekly_recap",
]
