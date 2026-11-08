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
from decimal import Decimal
from typing import Any, Protocol

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
    cache_read_tokens: int | None = None
    cost_usd: Decimal | None = None
    provider_request_id: str | None = None
    warnings: list[str] = field(default_factory=list)


class ExtractionError(RuntimeError):
    """Provider failed (timeout, quota, malformed output, refusal). Caller records it; nothing
    is guessed. `meta` carries usage for calls that were billed before failing."""

    def __init__(self, message: str, *, error_class: str = "provider_error",
                 meta: dict[str, Any] | None = None):
        super().__init__(message)
        self.error_class = error_class
        self.meta = meta or {}


class ExtractionProvider(Protocol):
    name: str

    def extract(self, raw_text: str, *, received_on: date) -> ExtractionOutput: ...


def get_provider(name: str) -> ExtractionProvider:
    """Factory. A real provider is only used when explicitly configured (DH_LLM_PROVIDER)."""
    if name == "mock":
        from app.llm.mock import MockRulesExtractor

        return MockRulesExtractor()
    if name == "anthropic":
        from app.llm.claude import ClaudeExtractor

        return ClaudeExtractor()
    raise ExtractionError(f"LLM provider '{name}' is not configured", error_class="config")
