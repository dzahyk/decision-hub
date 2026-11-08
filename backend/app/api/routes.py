"""HTTP layer: request/response models, role checks, error mapping. No business rules here."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.domain.requirement import REQUIRED_FIELDS, Objective, Region, Sector
from app.llm.base import ExtractionError, get_provider
from app.models import AuditEvent, Brief, Quote, RequirementRevision, User
from app.services import briefs as svc

router = APIRouter(prefix="/v1")
DB = Annotated[Session, Depends(get_session)]


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


# ------------------------------------------------------------------ demo identity
# Slice 0 only: the X-User header names a seeded user. Replaced by real sessions in Phase 4.


def current_user(db: DB, x_user: Annotated[str | None, Header()] = None) -> User:
    if not x_user:
        raise _err(401, "unauthenticated", "X-User header required")
    user = db.get(User, x_user)
    if not user:
        raise _err(401, "unknown_user", "unknown user")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require(user: User, *roles: str) -> None:
    if user.role not in roles:
        raise _err(403, "forbidden", f"role '{user.role}' cannot do this (needs {' or '.join(roles)})")


def request_id(request: Request) -> str:
    return request.state.request_id


RequestId = Annotated[str, Depends(request_id)]


def load_brief(db: Session, brief_id: uuid.UUID, user: User) -> Brief:
    brief = db.get(Brief, brief_id)
    # Sales only sees their own briefs; 404 so other ids are not confirmed to exist.
    if not brief or (user.role == "sales" and brief.created_by != user.id):
        raise _err(404, "not_found", "brief not found")
    return brief


# ------------------------------------------------------------------ schemas


class UserOut(BaseModel):
    id: str
    display_name: str
    role: str


class BriefIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    raw_text: str = Field(min_length=20, max_length=20_000)
    source: Literal["form", "api"] = "form"


class RevisionOut(BaseModel):
    id: uuid.UUID
    revision: int
    origin: str
    schema_version: str
    fields: dict[str, Any]
    validation: dict[str, Any]
    provider: str | None
    model_version: str | None
    prompt_version: str | None
    latency_ms: int | None
    created_by: str
    created_at: datetime


class QuoteOut(BaseModel):
    id: uuid.UUID
    revision: int
    requirement_revision_id: uuid.UUID
    rate_card_version: str
    discount_pct: Decimal
    list_total: Decimal
    net_total: Decimal
    cost_total: Decimal
    margin_pct: Decimal | None
    pricing: dict[str, Any]
    status: str
    created_by: str
    created_at: datetime


class BriefSummary(BaseModel):
    id: uuid.UUID
    title: str
    source: str
    status: str
    created_by: str
    received_at: datetime
    updated_at: datetime


class BriefDetail(BriefSummary):
    raw_text: str
    latest_revision: RevisionOut | None
    revisions: list[RevisionOut]
    latest_quote: QuoteOut | None


class RequirementPatch(BaseModel):
    fields: dict[str, Any] = Field(min_length=1)


class QuoteIn(BaseModel):
    discount_pct: Decimal = Field(default=Decimal(0), ge=0, le=100, decimal_places=2)


class AuditOut(BaseModel):
    id: int
    aggregate_type: str
    aggregate_id: str
    actor: str
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str | None
    at: datetime


def _detail(db: Session, brief: Brief) -> BriefDetail:
    revs = db.scalars(
        select(RequirementRevision)
        .where(RequirementRevision.brief_id == brief.id)
        .order_by(RequirementRevision.revision.desc())
    ).all()
    quote = db.scalar(
        select(Quote).where(Quote.brief_id == brief.id).order_by(Quote.revision.desc()).limit(1)
    )
    rev_out = [RevisionOut.model_validate(r, from_attributes=True) for r in revs]
    return BriefDetail(
        **BriefSummary.model_validate(brief, from_attributes=True).model_dump(),
        raw_text=brief.raw_text,
        latest_revision=rev_out[0] if rev_out else None,
        revisions=rev_out,
        latest_quote=QuoteOut.model_validate(quote, from_attributes=True) if quote else None,
    )


# ------------------------------------------------------------------ routes


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "llm_provider": get_settings().llm_provider}


@router.get("/users", response_model=list[UserOut])
def list_users(db: DB):
    """Demo user switcher. Exposes ids/roles only; removed with real auth in Phase 4."""
    return db.scalars(select(User).order_by(User.role, User.id)).all()


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.post("/briefs", response_model=BriefDetail, status_code=201)
def create_brief(
    body: BriefIn,
    db: DB,
    user: CurrentUser,
    response: Response,
    rid: RequestId,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
):
    require(user, "sales", "analyst")
    try:
        brief, replayed = svc.create_brief(
            db, user=user, title=body.title, raw_text=body.raw_text, source=body.source,
            idempotency_key=idempotency_key, request_id=rid,
        )
    except svc.WorkflowError as e:
        raise _err(e.http_status, e.code, e.message) from e
    if replayed:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    return _detail(db, brief)


@router.get("/briefs", response_model=list[BriefSummary])
def list_briefs(db: DB, user: CurrentUser, status: str | None = None):
    q = select(Brief).order_by(Brief.received_at.desc())
    if user.role == "sales":
        q = q.where(Brief.created_by == user.id)
    if status:
        q = q.where(Brief.status == status)
    return db.scalars(q.limit(200)).all()


@router.get("/briefs/{brief_id}", response_model=BriefDetail)
def get_brief(brief_id: uuid.UUID, db: DB, user: CurrentUser):
    return _detail(db, load_brief(db, brief_id, user))


@router.post("/briefs/{brief_id}/extract", response_model=BriefDetail)
def extract(brief_id: uuid.UUID, db: DB, user: CurrentUser, rid: RequestId):
    require(user, "analyst")
    brief = load_brief(db, brief_id, user)
    try:
        provider = get_provider(get_settings().llm_provider)
        svc.run_extraction(db, user=user, brief=brief, provider=provider, request_id=rid)
    except ExtractionError as e:  # provider misconfigured: say so, never guess
        raise _err(502, "extraction_failed", str(e)) from e
    except svc.WorkflowError as e:
        raise _err(e.http_status, e.code, e.message) from e
    return _detail(db, brief)


@router.patch("/briefs/{brief_id}/requirements", response_model=BriefDetail)
def patch_requirements(
    brief_id: uuid.UUID, body: RequirementPatch, db: DB, user: CurrentUser, rid: RequestId
):
    require(user, "analyst")
    brief = load_brief(db, brief_id, user)
    try:
        svc.edit_requirements(db, user=user, brief=brief, changes=body.fields, request_id=rid)
    except svc.WorkflowError as e:
        raise _err(e.http_status, e.code, e.message) from e
    return _detail(db, brief)


@router.post("/briefs/{brief_id}/quotes", response_model=QuoteOut, status_code=201)
def create_quote(brief_id: uuid.UUID, body: QuoteIn, db: DB, user: CurrentUser, rid: RequestId):
    require(user, "analyst")
    brief = load_brief(db, brief_id, user)
    try:
        return svc.create_quote(
            db, user=user, brief=brief, discount_pct=str(body.discount_pct), request_id=rid
        )
    except svc.WorkflowError as e:
        raise _err(e.http_status, e.code, e.message) from e


@router.get("/rate-cards/active")
def get_active_rate_card(db: DB, user: CurrentUser):
    try:
        card = svc.active_rate_card(db)
    except svc.WorkflowError as e:
        raise _err(e.http_status, e.code, e.message) from e
    return {"version": card.version, "effective_from": card.effective_from, **card.payload}


@router.get("/audit/{aggregate_id}", response_model=list[AuditOut])
def audit_trail(aggregate_id: str, db: DB, user: CurrentUser):
    # Spec lists auditor/admin; analyst + manager added so reviewers can trace decisions (UX-A02).
    require(user, "auditor", "admin", "analyst", "manager")
    return db.scalars(
        select(AuditEvent).where(AuditEvent.aggregate_id == aggregate_id).order_by(AuditEvent.id)
    ).all()


@router.get("/schema/requirement")
def requirement_schema() -> dict[str, Any]:
    return {
        "required": list(REQUIRED_FIELDS),
        "enums": {
            "client_sector": [e.value for e in Sector],
            "campaign_objective": [e.value for e in Objective],
            "geo": [e.value for e in Region],
        },
        "currencies": ["USD"],
    }
