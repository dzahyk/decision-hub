"""Provider-agnostic extraction interface.

An ExtractionProvider turns untrusted brief text into *suggested* field envelopes.
It must:
  - return every REQUIRED_FIELDS key (status "missing" when not found — never guess silently)
  - quote the source text it relied on (source_quote) so analysts can verify
  - never return prices, approvals or policy changes (A-BR-04)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from app.domain.requirement import FieldValue

PROMPT_VERSION = "extract.v1"


@dataclass
class ExtractionOutput:
    fields: dict[str, FieldValue]
    provider: str
    model_version: str
    prompt_version: str = PROMPT_VERSION
    input_tokens: int | None = None
    output_tokens: int | None = None
    warnings: list[str] = field(default_factory=list)


class ExtractionError(RuntimeError):
    """Provider failed (timeout, quota, malformed output). Caller records it; nothing is guessed."""

    def __init__(self, message: str, *, error_class: str = "provider_error"):
        super().__init__(message)
        self.error_class = error_class


class ExtractionProvider(Protocol):
    name: str

    def extract(self, raw_text: str, *, received_on: date) -> ExtractionOutput: ...


def get_provider(name: str) -> ExtractionProvider:
    """Factory. Real providers are added in Phase 3 and only activate when a key is configured."""
    if name == "mock":
        from app.llm.mock import MockRulesExtractor

        return MockRulesExtractor()
    raise ExtractionError(f"LLM provider '{name}' is not configured", error_class="config")
