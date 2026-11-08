"""PII redaction before any text leaves the server for an LLM (A-NFR-02, UU PDP caution).

Deterministic and reversible *locally only*: `redact()` swaps personal data for placeholders
like [EMAIL_1]; the mapping never leaves this process. `restore()` puts the originals back into
quotes the model returns, so analysts still see exact text from the raw brief.

Scope (documented, tested): email addresses, phone numbers, Indonesian NIK (16 digits),
payment-card-like numbers, and names introduced by contact cues ("contact: Jane Doe",
"PIC: Budi Santoso", "cc Rina"). Free-floating names without a cue are NOT detected — a known
limit recorded in docs/TRACEABILITY.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_CUE = r"(?:contact|PIC|cc|kontak|attn|hubungi|contact person|narahubung)"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("NIK", re.compile(r"(?<!\d)\d{16}(?!\d)")),  # Indonesian national ID
    ("CARD", re.compile(r"(?<!\d)\d{4}(?:[ -]\d{4}){2,3}(?:[ -]\d{1,3})?(?!\d)")),
    # Phone candidates; kept only if 9-15 digits and no thousands comma (see _is_phone)
    ("PHONE", re.compile(r"(?<![\w$€.,])\+?\(?\d[\d\s().-]{7,18}\d(?!\w|[.,]\d)")),
    # Names after a contact cue: up to three capitalised words
    ("PERSON", re.compile(rf"(?i:\b{_CUE})\s*[:\-]?\s*([A-Z][a-z]+(?:\s[A-Z][a-z]+){{0,2}})")),
]


def _is_phone(s: str) -> bool:
    digits = re.sub(r"\D", "", s)
    if not 9 <= len(digits) <= 15 or "," in s:
        return False
    if re.fullmatch(r"\s*\d{1,3}(?:\.\d{3}){2,}\s*", s):  # Indonesian amount: 1.500.000.000
        return False
    # ISO dates or date ranges (2026-12-01) are not phones
    return not re.fullmatch(r"\s*\d{4}-\d{2}-\d{2}.*", s)


def _is_card(s: str) -> bool:
    return 13 <= len(re.sub(r"\D", "", s)) <= 19


@dataclass
class Redaction:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)  # placeholder -> original

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for ph in self.mapping:
            kind = ph.strip("[]").rsplit("_", 1)[0]
            out[kind] = out.get(kind, 0) + 1
        return out


def redact(text: str) -> Redaction:
    mapping: dict[str, str] = {}
    reverse: dict[str, str] = {}
    counters: dict[str, int] = {}

    def placeholder(kind: str, original: str) -> str:
        if original in reverse:
            return reverse[original]
        counters[kind] = counters.get(kind, 0) + 1
        ph = f"[{kind}_{counters[kind]}]"
        mapping[ph] = original
        reverse[original] = ph
        return ph

    out = text
    for kind, rx in _PATTERNS:
        if kind == "PERSON":
            def sub_person(m: re.Match[str]) -> str:
                name = m.group(1)
                return m.group(0)[: m.start(1) - m.start(0)] + placeholder("PERSON", name)

            out = rx.sub(sub_person, out)
        else:
            def sub(m: re.Match[str], kind: str = kind) -> str:
                if kind == "PHONE" and not _is_phone(m.group(0)):
                    return m.group(0)
                if kind == "CARD" and not _is_card(m.group(0)):
                    return m.group(0)
                return placeholder(kind, m.group(0))

            out = rx.sub(sub, out)
    return Redaction(text=out, mapping=mapping)


def restore(value: Any, mapping: dict[str, str]) -> Any:
    """Replace placeholders with originals inside strings, lists and dicts (recursively)."""
    if not mapping:
        return value
    if isinstance(value, str):
        for ph, original in mapping.items():
            value = value.replace(ph, original)
        return value
    if isinstance(value, list):
        return [restore(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: restore(v, mapping) for k, v in value.items()}
    return value
