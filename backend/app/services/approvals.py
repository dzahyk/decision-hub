"""Human decisions, finalization, replay and rate card versions.

A-FR-08 approve/reject with reason · A-BR-05 no self-approval · A-BR-06 approvals bind to one
quote revision · A-BR-08 operations feasibility before approval · A-FR-09 finalize only an
approved revision · A-BR-07 exactly-once finalize · A-FR-14 export · A-FR-17 rate card versions.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.pricing import price_requirement
from app.domain.requirement import Region
from app.models import (
    Approval,
    Brief,
    FeasibilityNote,
    FinalProposal,
    Quote,
    RateCard,
    RequirementRevision,
    User,
    utcnow,
)
from app.proposal import render_proposal
from app.services import outbox
from app.services.briefs import (
    WorkflowError,
    audit,
    latest_quote,
    lock_brief,
    set_brief_status,
)

MIN_REASON = 5


def _forbidden(message: str) -> WorkflowError:
    return WorkflowError("forbidden", message, http_status=403)


def lock_quote(db: Session, quote_id: uuid.UUID) -> Quote:
    q = db.scalar(select(Quote).where(Quote.id == quote_id).with_for_update())
    if q is None:
        raise WorkflowError("not_found", "quote not found", http_status=404)
    return q


def latest_feasibility(db: Session, quote: Quote) -> FeasibilityNote | None:
    return db.scalar(
        select(FeasibilityNote)
        .where(FeasibilityNote.quote_id == quote.id, FeasibilityNote.quote_revision == quote.revision)
        .order_by(FeasibilityNote.at.desc())
        .limit(1)
    )


def approval_for(db: Session, quote: Quote) -> Approval | None:
    return db.scalar(
        select(Approval)
        .where(Approval.quote_id == quote.id, Approval.quote_revision == quote.revision)
        .order_by(Approval.at.desc())
        .limit(1)
    )


def _ensure_current(db: Session, quote: Quote, expected_revision: int | None) -> None:
    newest = latest_quote(db, quote.brief_id)
    if quote.status == "superseded" or (newest and newest.id != quote.id):
        raise WorkflowError(
            "stale_quote", "this quote was replaced by a newer revision; review the latest one"
        )
    if expected_revision is not None and expected_revision != quote.revision:
        raise WorkflowError(
            "stale_quote",
            f"you reviewed revision r{expected_revision} but the quote is now r{quote.revision}",
        )


# ------------------------------------------------------------------ operations (A-BR-08)


def add_feasibility_note(
    db: Session, *, user: User, quote_id: uuid.UUID, feasible: bool, note: str,
    request_id: str | None = None,
) -> FeasibilityNote:
    if user.role != "operations":
        raise _forbidden("only operations can record a feasibility assessment")
    if len(note.strip()) < MIN_REASON:
        raise WorkflowError("note_required", "explain the assessment", http_status=422)
    quote = lock_quote(db, quote_id)
    _ensure_current(db, quote, None)
    if quote.status != "pending_approval":
        raise WorkflowError("invalid_state", f"quote is {quote.status}")
    fn = FeasibilityNote(
        quote_id=quote.id, quote_revision=quote.revision, actor=user.id,
        feasible=feasible, note=note.strip(),
    )
    db.add(fn)
    audit(
        db, actor=user.id, brief_id=quote.brief_id, aggregate_type="quote", aggregate_id=quote.id,
        action="quote.feasibility_noted",
        after={"revision": quote.revision, "feasible": feasible, "note": fn.note},
        request_id=request_id,
    )
    db.commit()
    return fn


# ------------------------------------------------------------------ manager decision (A-FR-08)


def decide(
    db: Session, *, user: User, quote_id: uuid.UUID,
    decision: Literal["approve", "reject"], reason: str | None, expected_revision: int,
    request_id: str | None = None,
) -> Approval:
    if user.role != "manager":
        raise _forbidden("only a manager can approve or reject quotes")
    quote = lock_quote(db, quote_id)
    brief = lock_brief(db, quote.brief_id)

    # A-BR-05: nobody decides on a proposal they created or requested.
    if user.id in (quote.created_by, brief.created_by):
        audit(
            db, actor=user.id, brief_id=brief.id, aggregate_type="quote", aggregate_id=quote.id,
            action="quote.decision_blocked",
            after={"reason": "self_approval", "attempted": decision, "revision": quote.revision},
            request_id=request_id,
        )
        db.commit()
        raise WorkflowError(
            "self_approval_forbidden", "you cannot decide on a quote you created", http_status=403
        )

    _ensure_current(db, quote, expected_revision)
    if quote.status != "pending_approval":
        raise WorkflowError("invalid_state", f"quote is already {quote.status}")

    reason = (reason or "").strip() or None
    needs_reason = decision == "reject" or quote.pricing.get("requires_manager_review")
    if needs_reason and (reason is None or len(reason) < MIN_REASON):
        raise WorkflowError(
            "reason_required",
            "a reason is required to reject, or to approve a quote with policy flags",
            http_status=422,
        )

    if decision == "approve" and quote.pricing.get("requires_operations_review"):
        note = latest_feasibility(db, quote)
        if note is None:
            raise WorkflowError(
                "feasibility_pending", "operations has not assessed feasibility yet"
            )
        if not note.feasible:
            raise WorkflowError(
                "not_feasible", "operations marked this quote as not feasible; it can only be rejected"
            )

    approval = Approval(
        quote_id=quote.id, quote_revision=quote.revision, actor=user.id,
        decision=decision, reason=reason,
    )
    db.add(approval)
    new_status = "approved" if decision == "approve" else "rejected"
    audit(
        db, actor=user.id, brief_id=brief.id, aggregate_type="quote", aggregate_id=quote.id,
        action=f"quote.{new_status}",
        before={"status": quote.status},
        after={"status": new_status, "revision": quote.revision, "reason": reason},
        request_id=request_id,
    )
    quote.status = new_status
    set_brief_status(
        db, brief, new_status, actor=user.id, reason=f"quote r{quote.revision} {new_status}",
        rid=request_id,
    )
    db.commit()
    return approval


# ------------------------------------------------------------------ finalize (A-FR-09, A-BR-07)


def finalize(
    db: Session, *, user: User, quote_id: uuid.UUID, idempotency_key: str | None,
    request_id: str | None = None,
) -> tuple[FinalProposal, bool]:
    if user.role not in ("analyst", "manager"):
        raise _forbidden("only an analyst or manager can finalize")
    if not idempotency_key:
        raise WorkflowError(
            "idempotency_key_required", "Idempotency-Key header is required", http_status=400
        )

    existing = db.scalar(select(FinalProposal).where(FinalProposal.idempotency_key == idempotency_key))
    if existing:
        if existing.quote_id != quote_id:
            raise WorkflowError("idempotency_conflict", "key already used for another quote")
        return existing, True

    quote = lock_quote(db, quote_id)
    brief = lock_brief(db, quote.brief_id)
    already = db.scalar(select(FinalProposal).where(FinalProposal.quote_id == quote.id))
    if already:
        raise WorkflowError("already_finalized", "this quote already has a final proposal")
    if quote.status != "approved":
        raise WorkflowError("not_approved", f"only approved quotes can be finalized (is {quote.status})")
    approval = approval_for(db, quote)
    if approval is None or approval.decision != "approve":  # defensive: state and record must agree
        raise WorkflowError("not_approved", "no approval recorded for this quote revision")

    requirement = db.get(RequirementRevision, quote.requirement_revision_id)
    now = utcnow()
    html = render_proposal(
        brief=brief, quote=quote, requirement=requirement, approval=approval,
        final=True, finalized_at=now, finalized_by=user.id,
    )
    final = FinalProposal(
        quote_id=quote.id, idempotency_key=idempotency_key, approval_id=approval.id,
        document_html=html, document_sha256=hashlib.sha256(html.encode()).hexdigest(),
        finalized_by=user.id, at=now,
    )
    db.add(final)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()  # lost a race; the winner's row is the single final output
        winner = db.scalar(
            select(FinalProposal).where(FinalProposal.idempotency_key == idempotency_key)
        )
        if winner and winner.quote_id == quote_id:
            return winner, True
        raise WorkflowError("already_finalized", "this quote already has a final proposal") from None

    payload = {
        "brief_id": str(brief.id), "quote_id": str(quote.id), "revision": quote.revision,
        "net_total": str(quote.net_total), "currency": quote.pricing["currency"],
        "document_sha256": final.document_sha256,
    }
    outbox.enqueue(
        db, brief_id=brief.id, channel="email", key=f"final:{final.id}:email",
        payload={**payload, "to": brief.created_by, "subject": f"Proposal ready: {brief.title}"},
    )
    outbox.enqueue(
        db, brief_id=brief.id, channel="crm", key=f"final:{final.id}:crm",
        payload={**payload, "stage": "proposal_sent"},
    )
    audit(
        db, actor=user.id, brief_id=brief.id, aggregate_type="quote", aggregate_id=quote.id,
        action="quote.finalized",
        before={"status": quote.status},
        after={"status": "finalized", "revision": quote.revision,
               "document_sha256": final.document_sha256, "approval_id": str(approval.id)},
        request_id=request_id,
    )
    quote.status = "finalized"
    set_brief_status(db, brief, "finalized", actor=user.id, reason=f"quote r{quote.revision}", rid=request_id)
    db.commit()
    return final, False


def draft_document(db: Session, quote: Quote) -> str:
    brief = db.get(Brief, quote.brief_id)
    requirement = db.get(RequirementRevision, quote.requirement_revision_id)
    approval = approval_for(db, quote)
    return render_proposal(
        brief=brief, quote=quote, requirement=requirement,
        approval=approval if approval and approval.decision == "approve" else None, final=False,
    )


# ------------------------------------------------------------------ replay (A-NFR-05, UAT-A15)


def replay_quote(db: Session, quote: Quote) -> dict[str, Any]:
    """Recompute a stored quote from its own inputs and its own rate card version."""
    brief = db.get(Brief, quote.brief_id)
    requirement = db.get(RequirementRevision, quote.requirement_revision_id)
    card = db.get(RateCard, quote.rate_card_version)
    recomputed = price_requirement(
        requirement.validation["normalized"], card.payload,
        discount_pct=quote.discount_pct, reference_date=brief.received_at.date(),
    ).model_dump()
    return {
        "quote_id": str(quote.id),
        "rate_card_version": card.version,
        "matches": recomputed == quote.pricing,
        "stored": quote.pricing,
        "recomputed": recomputed,
    }


# ------------------------------------------------------------------ rate cards (A-FR-17)


class CatalogEntry(BaseModel):
    label: str = Field(min_length=2, max_length=80)
    unit_price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    unit_cost: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    monthly_capacity: int = Field(gt=0)


class RateCardIn(BaseModel):
    version: str = Field(pattern=r"^[0-9A-Za-z.\-]{3,32}$")
    effective_from: date
    floor_margin_pct: Decimal = Field(ge=0, lt=100, decimal_places=2)
    discount_review_threshold_pct: Decimal = Field(ge=0, le=100, decimal_places=2)
    max_discount_pct: Decimal = Field(ge=0, le=100, decimal_places=2)
    geo_multipliers: dict[str, tuple[Decimal, Decimal]]
    catalog: dict[str, CatalogEntry] = Field(min_length=1)

    @field_validator("geo_multipliers")
    @classmethod
    def _all_regions(cls, v: dict[str, tuple[Decimal, Decimal]]):
        if set(v) != {r.value for r in Region}:
            raise ValueError(f"geo_multipliers must cover exactly {[r.value for r in Region]}")
        if any(p <= 0 or c <= 0 for p, c in v.values()):
            raise ValueError("multipliers must be positive")
        return v

    @field_validator("catalog")
    @classmethod
    def _slugs(cls, v: dict[str, CatalogEntry]):
        for key in v:
            if not key.replace("_", "").isalnum() or key != key.lower():
                raise ValueError(f"catalog key '{key}' must be lower_snake_case")
        return v


def publish_rate_card(
    db: Session, *, user: User, data: dict[str, Any], request_id: str | None = None
) -> RateCard:
    """New version becomes active; old versions stay stored so old quotes replay exactly."""
    if user.role != "admin":
        raise _forbidden("only an admin can publish rate cards")
    try:
        card_in = RateCardIn.model_validate(data)
    except ValidationError as exc:
        raise WorkflowError("invalid_rate_card", str(exc.errors()[0]["msg"]), http_status=422) from exc
    if card_in.discount_review_threshold_pct > card_in.max_discount_pct:
        raise WorkflowError(
            "invalid_rate_card", "review threshold cannot exceed the maximum discount",
            http_status=422,
        )
    if db.get(RateCard, card_in.version):
        raise WorkflowError("version_exists", f"rate card {card_in.version} already exists")

    payload = {
        "version": card_in.version,
        "currency": "USD",
        "floor_margin_pct": str(card_in.floor_margin_pct.quantize(Decimal("0.01"))),
        "discount_review_threshold_pct": str(card_in.discount_review_threshold_pct.quantize(Decimal("0.01"))),
        "max_discount_pct": str(card_in.max_discount_pct.quantize(Decimal("0.01"))),
        "geo_multipliers": {g: [str(p), str(c)] for g, (p, c) in card_in.geo_multipliers.items()},
        "catalog": {
            k: {
                "label": e.label,
                "unit_price": str(e.unit_price.quantize(Decimal("0.01"))),
                "unit_cost": str(e.unit_cost.quantize(Decimal("0.01"))),
                "monthly_capacity": e.monthly_capacity,
            }
            for k, e in card_in.catalog.items()
        },
    }
    previous = db.scalar(select(RateCard).where(RateCard.is_active.is_(True)).with_for_update())
    db.execute(update(RateCard).values(is_active=False))
    card = RateCard(
        version=card_in.version, payload=payload, effective_from=card_in.effective_from,
        is_active=True, created_by=user.id,
    )
    db.add(card)
    audit(
        db, actor=user.id, brief_id=None, aggregate_type="rate_card", aggregate_id=card.version,
        action="rate_card.published",
        before={"active": previous.version if previous else None},
        after={"active": card.version, "effective_from": card_in.effective_from.isoformat()},
        request_id=request_id,
    )
    db.commit()
    return card
