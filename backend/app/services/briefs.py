"""Workflow orchestration for briefs, requirement revisions and quotes.

Every function runs inside one DB transaction and writes its audit event in that same
transaction, so a state change and its audit record either both exist or neither does.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from datetime import UTC, datetime
from datetime import time as dtime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.domain.pricing import PricingInputError, price_requirement
from app.domain.requirement import (
    REQUIRED_FIELDS,
    SCHEMA_VERSION,
    FieldValue,
    validate_requirement,
)
from app.llm.base import ExtractionError, ExtractionOutput, ExtractionProvider
from app.llm.redact import redact, restore
from app.models import (
    AuditEvent,
    Brief,
    LlmCall,
    Quote,
    RateCard,
    RequirementRevision,
    User,
    utcnow,
)

# Quote states in which an approval is still "live" and must be invalidated by any change.
OPEN_QUOTE_STATES = ("pending_approval", "approved")


class WorkflowError(Exception):
    """Business-rule violation. `code` is stable and shown to the client."""

    def __init__(self, code: str, message: str, *, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def audit(
    db: Session,
    *,
    actor: str,
    brief_id: uuid.UUID | None,
    aggregate_type: str,
    aggregate_id: Any,
    action: str,
    before: dict | None = None,
    after: dict | None = None,
    request_id: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            brief_id=brief_id,
            actor=actor,
            action=action,
            before=before,
            after=after,
            request_id=request_id,
        )
    )


def _hash(title: str, raw_text: str, source: str) -> str:
    return hashlib.sha256(f"{source}\x1f{title}\x1f{raw_text}".encode()).hexdigest()


def lock_brief(db: Session, brief_id: uuid.UUID) -> Brief:
    """Row lock so concurrent edits / decisions on one brief are serialized."""
    brief = db.scalar(select(Brief).where(Brief.id == brief_id).with_for_update())
    if brief is None:
        raise WorkflowError("not_found", "brief not found", http_status=404)
    return brief


def ensure_not_finalized(brief: Brief) -> None:
    if brief.status == "finalized":
        raise WorkflowError(
            "brief_finalized", "this brief has a final proposal and can no longer change"
        )


# ------------------------------------------------------------------ intake (A-FR-01/02)


def create_brief(
    db: Session,
    *,
    user: User,
    title: str,
    raw_text: str,
    source: str,
    idempotency_key: str | None,
    request_id: str | None = None,
) -> tuple[Brief, bool]:
    """Returns (brief, replayed). Same key + same payload -> original brief, replayed=True.
    Same key + different payload -> conflict, nothing written."""
    payload_hash = _hash(title, raw_text, source)
    if idempotency_key:
        existing = db.scalar(select(Brief).where(Brief.idempotency_key == idempotency_key))
        if existing:
            if existing.payload_hash != payload_hash:
                raise WorkflowError(
                    "idempotency_conflict",
                    "this Idempotency-Key was already used with a different brief",
                )
            return existing, True

    brief = Brief(
        title=title.strip(),
        raw_text=raw_text,
        source=source,
        created_by=user.id,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        status="received",
    )
    db.add(brief)
    try:
        db.flush()
    except IntegrityError:
        # A concurrent request with the same key won the race; return its brief.
        db.rollback()
        existing = db.scalar(select(Brief).where(Brief.idempotency_key == idempotency_key))
        if existing and existing.payload_hash == payload_hash:
            return existing, True
        raise WorkflowError("idempotency_conflict", "Idempotency-Key conflict") from None
    audit(
        db,
        actor=user.id,
        brief_id=brief.id,
        aggregate_type="brief",
        aggregate_id=brief.id,
        action="brief.created",
        after={"status": "received", "source": source, "title": brief.title},
        request_id=request_id,
    )
    db.commit()
    return brief, False


# ------------------------------------------------------------------ status helpers


def latest_revision(db: Session, brief_id: uuid.UUID) -> RequirementRevision | None:
    return db.scalar(
        select(RequirementRevision)
        .where(RequirementRevision.brief_id == brief_id)
        .order_by(RequirementRevision.revision.desc())
        .limit(1)
    )


def latest_quote(db: Session, brief_id: uuid.UUID) -> Quote | None:
    return db.scalar(
        select(Quote).where(Quote.brief_id == brief_id).order_by(Quote.revision.desc()).limit(1)
    )


def _next_revision_no(db: Session, brief_id: uuid.UUID) -> int:
    current = db.scalar(
        select(func.max(RequirementRevision.revision)).where(
            RequirementRevision.brief_id == brief_id
        )
    )
    return (current or 0) + 1


def _status_for(validation_status: str) -> str:
    return "ready_for_pricing" if validation_status == "valid" else "clarification_needed"


def set_brief_status(
    db: Session, brief: Brief, new: str, *, actor: str, reason: str, rid: str | None
) -> None:
    if brief.status == new:
        return
    audit(
        db,
        actor=actor,
        brief_id=brief.id,
        aggregate_type="brief",
        aggregate_id=brief.id,
        action="brief.status_changed",
        before={"status": brief.status},
        after={"status": new, "reason": reason},
        request_id=rid,
    )
    brief.status = new
    brief.updated_at = utcnow()


def supersede_open_quotes(
    db: Session, brief: Brief, *, actor: str, reason: str, rid: str | None
) -> list[Quote]:
    """A-BR-06: any change after pricing/approval invalidates the live quote and its approval."""
    open_quotes = db.scalars(
        select(Quote)
        .where(Quote.brief_id == brief.id, Quote.status.in_(OPEN_QUOTE_STATES))
        .with_for_update()
    ).all()
    for q in open_quotes:
        audit(
            db,
            actor=actor,
            brief_id=brief.id,
            aggregate_type="quote",
            aggregate_id=q.id,
            action="quote.superseded",
            before={"status": q.status, "revision": q.revision},
            after={"status": "superseded", "reason": reason},
            request_id=rid,
        )
        q.status = "superseded"
    return list(open_quotes)


# ------------------------------------------------------------------ LLM guards & records


def _check_daily_budget(db: Session, provider: ExtractionProvider) -> None:
    """A-NFR-09: stop paid extraction once today's spend reaches the configured cap."""
    cap = get_settings().llm_daily_budget_usd
    if provider.name == "mock" or cap <= 0:
        return
    since = datetime.combine(utcnow().date(), dtime.min, tzinfo=UTC)
    spent = db.scalar(
        select(func.coalesce(func.sum(LlmCall.cost_usd), 0)).where(LlmCall.at >= since)
    )
    if Decimal(spent) >= Decimal(str(cap)):
        raise WorkflowError(
            "llm_budget_exhausted",
            f"today's extraction budget (${cap:.2f}) is used up; fill the fields manually or "
            "raise DH_LLM_DAILY_BUDGET_USD",
            http_status=429,
        )


def _record_call(
    db: Session, *, brief: Brief, user: User, provider: ExtractionProvider,
    out: ExtractionOutput | None, error: ExtractionError | None, latency_ms: int,
    pii: dict[str, int], request_id: str | None,
) -> LlmCall:
    meta = error.meta if error else {}
    call = LlmCall(
        brief_id=brief.id,
        provider=provider.name,
        model=out.model_version if out else getattr(provider, "model", None),
        prompt_version=out.prompt_version if out else None,
        status="ok" if out else "error",
        error_class=error.error_class if error else None,
        latency_ms=latency_ms,
        input_tokens=out.input_tokens if out else meta.get("input_tokens"),
        output_tokens=out.output_tokens if out else meta.get("output_tokens"),
        cache_read_tokens=out.cache_read_tokens if out else meta.get("cache_read_tokens"),
        cost_usd=out.cost_usd if out else meta.get("cost_usd"),
        pii_redacted=pii,
        provider_request_id=out.provider_request_id if out else meta.get("provider_request_id"),
        request_id=request_id,
        created_by=user.id,
    )
    db.add(call)
    db.flush()
    return call


def _restore_and_verify(fv: FieldValue, mapping: dict[str, str], raw_text: str) -> FieldValue:
    """Put redacted values back, then re-check provenance against the *raw* brief for every
    provider: a quote that isn't in the brief is dropped and the field marked for review."""
    fv = FieldValue(
        value=restore(fv.value, mapping),
        status=fv.status,
        source_quote=restore(fv.source_quote, mapping),
        note=restore(fv.note, mapping),
    )
    if fv.source_quote and fv.source_quote not in raw_text:
        note = "quote not found in the brief — verify this value"
        return FieldValue(
            value=fv.value,
            status="inferred" if fv.value is not None else "missing",
            source_quote=None,
            note=f"{fv.note} · {note}" if fv.note else note,
        )
    return fv


# ------------------------------------------------------------------ revisions (A-FR-03/04/05)


def run_extraction(
    db: Session,
    *,
    user: User,
    brief: Brief,
    provider: ExtractionProvider,
    request_id: str | None = None,
) -> RequirementRevision:
    """Ask the provider for field suggestions, validate them, store a new revision.

    Steps: spend guard -> PII redaction -> provider call -> restore placeholders locally ->
    provenance re-check against the raw brief -> validation. Every call, failed or not, is
    recorded in llm_calls. On failure nothing is invented: the error is audited and raised."""
    ensure_not_finalized(brief)
    _check_daily_budget(db, provider)
    red = redact(brief.raw_text)
    started = time.perf_counter()
    try:
        out = provider.extract(red.text, received_on=brief.received_at.date())
    except ExtractionError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        _record_call(db, brief=brief, user=user, provider=provider, out=None, error=exc,
                     latency_ms=latency_ms, pii=red.counts, request_id=request_id)
        audit(
            db,
            actor=user.id,
            brief_id=brief.id,
            aggregate_type="brief",
            aggregate_id=brief.id,
            action="extraction.failed",
            after={"error_class": exc.error_class, "message": str(exc)},
            request_id=request_id,
        )
        db.commit()
        status = 503 if exc.error_class in ("circuit_open", "rate_limited") else 502
        raise WorkflowError("extraction_failed", str(exc), http_status=status) from exc
    latency_ms = int((time.perf_counter() - started) * 1000)

    brief = lock_brief(db, brief.id)
    ensure_not_finalized(brief)
    fields = {
        name: _restore_and_verify(out.fields.get(name, FieldValue()), red.mapping, brief.raw_text)
        for name in REQUIRED_FIELDS
    }
    call = _record_call(db, brief=brief, user=user, provider=provider, out=out, error=None,
                        latency_ms=latency_ms, pii=red.counts, request_id=request_id)
    validation = validate_requirement(fields)
    rev = RequirementRevision(
        brief_id=brief.id,
        revision=_next_revision_no(db, brief.id),
        origin="extraction",
        schema_version=SCHEMA_VERSION,
        fields={k: v.model_dump() for k, v in fields.items()},
        validation=validation.model_dump(),
        provider=out.provider,
        model_version=out.model_version,
        prompt_version=out.prompt_version,
        latency_ms=latency_ms,
        llm_call_id=call.id,
        created_by=user.id,
    )
    db.add(rev)
    db.flush()
    audit(
        db,
        actor=user.id,
        brief_id=brief.id,
        aggregate_type="brief",
        aggregate_id=brief.id,
        action="requirements.extracted",
        after={
            "revision": rev.revision,
            "provider": out.provider,
            "model_version": out.model_version,
            "validation": validation.status,
            "missing": [i.field for i in validation.issues if i.code == "missing"],
            "pii_redacted": red.counts,
            "cost_usd": str(out.cost_usd) if out.cost_usd is not None else None,
        },
        request_id=request_id,
    )
    supersede_open_quotes(
        db, brief, actor=user.id, reason=f"requirements r{rev.revision}", rid=request_id
    )
    set_brief_status(
        db, brief, _status_for(validation.status), actor=user.id,
        reason=f"extraction r{rev.revision}: {validation.status}", rid=request_id,
    )
    db.commit()
    return rev


def edit_requirements(
    db: Session,
    *,
    user: User,
    brief: Brief,
    changes: dict[str, Any],
    request_id: str | None = None,
) -> RequirementRevision:
    """Analyst correction: copies the latest revision, overrides the given fields with
    status 'edited', re-validates. The raw brief and earlier revisions are untouched."""
    unknown = set(changes) - set(REQUIRED_FIELDS)
    if unknown:
        raise WorkflowError(
            "unknown_field", f"unknown field(s): {', '.join(sorted(unknown))}", http_status=422
        )
    brief = lock_brief(db, brief.id)
    ensure_not_finalized(brief)
    prev = latest_revision(db, brief.id)
    base = {k: FieldValue() for k in REQUIRED_FIELDS}
    if prev:
        base.update({k: FieldValue.model_validate(v) for k, v in prev.fields.items()})
    for name, value in changes.items():
        old = base[name]
        base[name] = FieldValue(
            value=value,
            status="edited" if value not in (None, "", []) else "missing",
            source_quote=old.source_quote,
            note=f"set by {user.id}",
        )
    validation = validate_requirement(base)
    rev = RequirementRevision(
        brief_id=brief.id,
        revision=_next_revision_no(db, brief.id),
        origin="analyst",
        schema_version=SCHEMA_VERSION,
        fields={k: v.model_dump() for k, v in base.items()},
        validation=validation.model_dump(),
        created_by=user.id,
    )
    db.add(rev)
    db.flush()
    audit(
        db,
        actor=user.id,
        brief_id=brief.id,
        aggregate_type="brief",
        aggregate_id=brief.id,
        action="requirements.edited",
        before={k: (prev.fields.get(k) or {}).get("value") for k in changes} if prev else None,
        after={"revision": rev.revision, "changes": changes, "validation": validation.status},
        request_id=request_id,
    )
    supersede_open_quotes(
        db, brief, actor=user.id, reason=f"requirements r{rev.revision}", rid=request_id
    )
    set_brief_status(
        db, brief, _status_for(validation.status), actor=user.id,
        reason=f"analyst edit r{rev.revision}: {validation.status}", rid=request_id,
    )
    db.commit()
    return rev


# ------------------------------------------------------------------ pricing (A-FR-06/07)


def active_rate_card(db: Session) -> RateCard:
    card = db.scalar(select(RateCard).where(RateCard.is_active.is_(True)))
    if not card:
        raise WorkflowError("no_active_rate_card", "no active rate card", http_status=503)
    return card


def create_quote(
    db: Session,
    *,
    user: User,
    brief: Brief,
    discount_pct: str,
    request_id: str | None = None,
) -> Quote:
    """Price the latest *valid* revision with the active rate card. Incomplete briefs are
    refused (A-BR-01): there is no path to a quote without a valid requirement.
    Every new quote supersedes the previous open one and needs its own approval."""
    brief = lock_brief(db, brief.id)
    ensure_not_finalized(brief)
    rev = latest_revision(db, brief.id)
    if rev is None or rev.validation.get("status") != "valid":
        raise WorkflowError(
            "requirements_not_valid",
            "brief needs clarification before it can be priced",
        )
    card = active_rate_card(db)
    try:
        result = price_requirement(
            rev.validation["normalized"],
            card.payload,
            discount_pct=discount_pct,
            reference_date=brief.received_at.date(),
        )
    except PricingInputError as exc:
        raise WorkflowError("invalid_pricing_input", str(exc), http_status=422) from exc

    revision = (
        db.scalar(select(func.max(Quote.revision)).where(Quote.brief_id == brief.id)) or 0
    ) + 1
    supersede_open_quotes(
        db, brief, actor=user.id, reason=f"re-priced as r{revision}", rid=request_id
    )
    quote = Quote(
        brief_id=brief.id,
        requirement_revision_id=rev.id,
        revision=revision,
        rate_card_version=card.version,
        discount_pct=result.discount_pct,
        list_total=result.list_total,
        net_total=result.net_total,
        cost_total=result.cost_total,
        margin_pct=result.margin_pct,
        pricing=result.model_dump(),
        status="pending_approval",
        created_by=user.id,
    )
    db.add(quote)
    db.flush()
    audit(
        db,
        actor=user.id,
        brief_id=brief.id,
        aggregate_type="quote",
        aggregate_id=quote.id,
        action="quote.priced",
        after={
            "revision": revision,
            "requirement_revision": rev.revision,
            "rate_card_version": card.version,
            "net_total": result.net_total,
            "margin_pct": result.margin_pct,
            "flags": [f.code for f in result.flags],
            "status": quote.status,
        },
        request_id=request_id,
    )
    set_brief_status(
        db, brief, "priced", actor=user.id, reason=f"quote r{revision}", rid=request_id
    )
    db.commit()
    return quote
