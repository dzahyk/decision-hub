"""Structured requirement schema and validation (A-FR-03, A-FR-04, A-BR-01).

Pure module: no I/O. A requirement is a dict of field name -> FieldValue envelope.
The envelope records *where* a value came from so reviewers can trace field -> source.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "requirement.v1"


class Sector(StrEnum):
    technology = "technology"
    financial_services = "financial_services"
    healthcare = "healthcare"
    manufacturing = "manufacturing"
    retail = "retail"
    telecom = "telecom"
    education = "education"
    other = "other"


class Objective(StrEnum):
    lead_generation = "lead_generation"
    demand_generation = "demand_generation"
    brand_awareness = "brand_awareness"
    event_registration = "event_registration"
    pipeline_acceleration = "pipeline_acceleration"


class Region(StrEnum):
    NA = "NA"
    EMEA = "EMEA"
    APAC = "APAC"
    LATAM = "LATAM"


SUPPORTED_CURRENCIES = {"USD"}

# A-BR-01: every brief must carry these before a quote can exist.
REQUIRED_FIELDS: tuple[str, ...] = (
    "client_sector",
    "campaign_objective",
    "audience",
    "geo",
    "deliverables",
    "budget",
    "timeline",
)

FieldStatus = Literal["found", "inferred", "missing", "edited"]


class FieldValue(BaseModel):
    """One extracted field. `missing` means value is None. No confidence scores by design."""

    value: Any = None
    status: FieldStatus = "missing"
    source_quote: str | None = None
    note: str | None = None


class Issue(BaseModel):
    field: str
    code: Literal["missing", "wrong_type", "illegal_enum", "out_of_range", "unsupported"]
    message: str


class ValidationResult(BaseModel):
    status: Literal["valid", "incomplete", "invalid"]
    issues: list[Issue] = Field(default_factory=list)
    normalized: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "valid"


# ---------------------------------------------------------------- field checks
# Each checker returns (normalized_value, issue_or_None).


def _check_enum(name: str, value: Any, enum: type[StrEnum]):
    if not isinstance(value, str):
        return None, Issue(field=name, code="wrong_type", message=f"{name} must be text")
    try:
        return enum(value).value, None
    except ValueError:
        allowed = ", ".join(e.value for e in enum)
        return None, Issue(
            field=name, code="illegal_enum", message=f"'{value}' is not one of: {allowed}"
        )


def _check_audience(name: str, value: Any):
    if not isinstance(value, str):
        return None, Issue(field=name, code="wrong_type", message="audience must be text")
    text = value.strip()
    if len(text) < 3:
        return None, Issue(field=name, code="out_of_range", message="audience is too short")
    return text, None


def _check_geo(name: str, value: Any):
    if not isinstance(value, list) or not value:
        return None, Issue(field=name, code="wrong_type", message="geo must be a non-empty list")
    out: list[str] = []
    for item in value:
        norm, issue = _check_enum(name, item, Region)
        if issue:
            return None, issue
        if norm not in out:
            out.append(norm)
    return out, None


def _check_deliverables(name: str, value: Any):
    if not isinstance(value, list) or not value:
        return None, Issue(
            field=name, code="wrong_type", message="deliverables must be a non-empty list"
        )
    out: list[dict[str, Any]] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict) or "type" not in item or "volume" not in item:
            return None, Issue(
                field=name, code="wrong_type", message=f"item {i + 1} needs type and volume"
            )
        dtype, volume = item["type"], item["volume"]
        if not isinstance(dtype, str) or not dtype.strip():
            return None, Issue(field=name, code="wrong_type", message=f"item {i + 1}: bad type")
        if isinstance(volume, bool) or not isinstance(volume, int):
            return None, Issue(
                field=name, code="wrong_type", message=f"item {i + 1}: volume must be a whole number"
            )
        if volume <= 0:
            return None, Issue(
                field=name, code="out_of_range", message=f"item {i + 1}: volume must be above 0"
            )
        out.append({"type": dtype.strip(), "volume": volume})
    return out, None


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _check_budget(name: str, value: Any):
    if not isinstance(value, dict) or "amount" not in value or "currency" not in value:
        return None, Issue(
            field=name, code="wrong_type", message="budget needs amount and currency"
        )
    amount = _to_decimal(value["amount"])
    if amount is None or not amount.is_finite():
        return None, Issue(field=name, code="wrong_type", message="budget amount must be a number")
    if amount <= 0:
        return None, Issue(field=name, code="out_of_range", message="budget must be above 0")
    currency = str(value["currency"]).upper()
    if currency not in SUPPORTED_CURRENCIES:
        return None, Issue(
            field=name,
            code="unsupported",
            message=f"currency {currency} is not on the rate card (USD only in v1)",
        )
    return {"amount": str(amount.quantize(Decimal("0.01"))), "currency": currency}, None


def _parse_date(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _check_timeline(name: str, value: Any):
    if not isinstance(value, dict) or not value.get("end_date"):
        return None, Issue(field=name, code="wrong_type", message="timeline needs an end_date")
    end = _parse_date(value["end_date"])
    if end is None:
        return None, Issue(field=name, code="wrong_type", message="end_date must be YYYY-MM-DD")
    start = None
    if value.get("start_date"):
        start = _parse_date(value["start_date"])
        if start is None:
            return None, Issue(
                field=name, code="wrong_type", message="start_date must be YYYY-MM-DD"
            )
        if start > end:
            return None, Issue(
                field=name, code="out_of_range", message="start_date is after end_date"
            )
    return {
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat(),
    }, None


_CHECKERS = {
    "client_sector": lambda n, v: _check_enum(n, v, Sector),
    "campaign_objective": lambda n, v: _check_enum(n, v, Objective),
    "audience": _check_audience,
    "geo": _check_geo,
    "deliverables": _check_deliverables,
    "budget": _check_budget,
    "timeline": _check_timeline,
}


def validate_requirement(fields: dict[str, FieldValue | dict]) -> ValidationResult:
    """Check every required field. Never drops a field silently (BO-A3).

    - any required field missing            -> incomplete
    - any present field malformed           -> invalid (takes precedence)
    """
    issues: list[Issue] = []
    normalized: dict[str, Any] = {}
    has_missing = has_invalid = False

    for name in REQUIRED_FIELDS:
        raw = fields.get(name)
        fv = raw if isinstance(raw, FieldValue) else FieldValue.model_validate(raw or {})
        if fv.status == "missing" or fv.value is None or fv.value == "" or fv.value == []:
            has_missing = True
            issues.append(Issue(field=name, code="missing", message=f"{name} is required"))
            continue
        value, issue = _CHECKERS[name](name, fv.value)
        if issue:
            has_invalid = True
            issues.append(issue)
        else:
            normalized[name] = value

    status = "invalid" if has_invalid else "incomplete" if has_missing else "valid"
    return ValidationResult(status=status, issues=issues, normalized=normalized)
