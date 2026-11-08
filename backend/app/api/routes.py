"""HTTP layer: request/response models, authentication, role checks, error mapping.
Business rules live in app/services; this module only translates HTTP <-> service calls."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import COOKIE_NAME, issue_token, read_token, verify_password
from app.config import get_settings
from app.db import get_session
from app.domain.requirement import REQUIRED_FIELDS, Objective, Region, Sector
from app.llm.base import ExtractionError, get_provider
from app.models import (
    AuditEvent,
    Brief,
    FinalProposal,
    Notification,
    Quote,
    RateCard,
    RequirementRevision,
    User,
)
from app.services import approvals as appr
from app.services import briefs as svc
from app.services import outbox

log = logging.getLogger("decision_hub")
router = APIRouter(prefix="/v1")
DB = Annotated[Session, Depends(get_session)]


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def _wf(e: svc.WorkflowError) -> HTTPException:
    return _err(e.http_status, e.code, e.message)


# ------------------------------------------------------------------ identity (A-NFR-01)


def current_user(
    db: DB,
    authorization: Annotated[str | None, Header()] = None,
    dh_session: Annotated[str | None, Cookie()] = None,
) -> User:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif dh_session:
        token = dh_session
    if not token:
        raise _err(401, "unauthenticated", "sign in required")
    user_id = read_token(token)
    user = db.get(User, user_id) if user_id else None
    if not user:
        raise _err(401, "invalid_session", "session expired or invalid; sign in again")
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


def load_quote(db: Session, quote_id: uuid.UUID, user: User) -> Quote:
    quote = db.get(Quote, quote_id)
    if not quote:
        raise _err(404, "not_found", "quote not found")
    load_brief(db, quote.brief_id, user)  # same visibility rule as the brief
    return quote


# ------------------------------------------------------------------ schemas


class UserOut(BaseModel):
    id: str
    display_name: str
    role: str


class SessionOut(BaseModel):
    user: UserOut
    token: str
    demo_mode: bool


class DemoLoginIn(BaseModel):
    user_id: str = Field(max_length=64)


class LoginIn(BaseModel):
    user_id: str = Field(max_length=64)
    password: str = Field(max_length=256)


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


class FeasibilityOut(BaseModel):
    actor: str
    feasible: bool
    note: str
    at: datetime


class ApprovalOut(BaseModel):
    id: uuid.UUID
    actor: str
    decision: str
    reason: str | None
    quote_revision: int
    at: datetime


class FinalOut(BaseModel):
    id: uuid.UUID
    finalized_by: str
    document_sha256: str
    at: datetime


class ReviewOut(BaseModel):
    feasibility: FeasibilityOut | None
    approval: ApprovalOut | None
    final: FinalOut | None


class QuoteOut(BaseModel):
    id: uuid.UUID
    brief_id: uuid.UUID
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
    review: ReviewOut


class QueueItem(QuoteOut):
    brief_title: str
    brief_created_by: str


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
    quote_history: list[dict[str, Any]]


class RequirementPatch(BaseModel):
    fields: dict[str, Any] = Field(min_length=1)


class QuoteIn(BaseModel):
    discount_pct: Decimal = Field(default=Decimal(0), ge=0, le=100, decimal_places=2)


class FeasibilityIn(BaseModel):
    feasible: bool
    note: str = Field(max_length=2000)


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)
    expected_revision: int = Field(ge=1)


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


class NotificationOut(BaseModel):
    id: uuid.UUID
    channel: str
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None


def quote_out(db: Session, q: Quote) -> QuoteOut:
    fn = appr.latest_feasibility(db, q)
    ap = appr.approval_for(db, q)
    fin = db.scalar(select(FinalProposal).where(FinalProposal.quote_id == q.id))
    review = ReviewOut(
        feasibility=FeasibilityOut.model_validate(fn, from_attributes=True) if fn else None,
        approval=ApprovalOut.model_validate(ap, from_attributes=True) if ap else None,
        final=FinalOut.model_validate(fin, from_attributes=True) if fin else None,
    )
    base = {k: getattr(q, k) for k in QuoteOut.model_fields if k != "review"}
    return QuoteOut(**base, review=review)


def _detail(db: Session, brief: Brief) -> BriefDetail:
    revs = db.scalars(
        select(RequirementRevision)
        .where(RequirementRevision.brief_id == brief.id)
        .order_by(RequirementRevision.revision.desc())
    ).all()
    quotes = db.scalars(
        select(Quote).where(Quote.brief_id == brief.id).order_by(Quote.revision.desc())
    ).all()
    rev_out = [RevisionOut.model_validate(r, from_attributes=True) for r in revs]
    return BriefDetail(
        **BriefSummary.model_validate(brief, from_attributes=True).model_dump(),
        raw_text=brief.raw_text,
        latest_revision=rev_out[0] if rev_out else None,
        revisions=rev_out,
        latest_quote=quote_out(db, quotes[0]) if quotes else None,
        quote_history=[
            {"id": str(q.id), "revision": q.revision, "status": q.status,
             "net_total": str(q.net_total), "created_at": q.created_at.isoformat()}
            for q in quotes
        ],
    )


# ------------------------------------------------------------------ meta + auth


@router.get("/health")
def health() -> dict[str, Any]:
    s = get_settings()
    return {"status": "ok", "llm_provider": s.llm_provider, "demo_mode": s.demo_mode}


def _start_session(response: Response, user: User) -> SessionOut:
    s = get_settings()
    token = issue_token(user.id)
    response.set_cookie(
        COOKIE_NAME, token, max_age=s.session_ttl_minutes * 60, httponly=True,
        samesite="lax", secure=s.cookie_secure, path="/",
    )
    return SessionOut(user=UserOut.model_validate(user, from_attributes=True), token=token,
                      demo_mode=s.demo_mode)


@router.post("/auth/demo-login", response_model=SessionOut)
def demo_login(body: DemoLoginIn, db: DB, response: Response):
    """Portfolio demo only: sign in as a synthetic persona without a password."""
    if not get_settings().demo_mode:
        raise _err(404, "not_found", "demo login is disabled")
    user = db.get(User, body.user_id)
    if not user:
        raise _err(404, "unknown_user", "unknown demo user")
    return _start_session(response, user)


@router.post("/auth/login", response_model=SessionOut)
def login(body: LoginIn, db: DB, response: Response):
    user = db.get(User, body.user_id)
    if not user or not verify_password(body.password, user.password_hash):
        raise _err(401, "bad_credentials", "wrong user or password")
    return _start_session(response, user)


@router.post("/auth/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/users", response_model=list[UserOut])
def list_users(db: DB):
    """Persona picker for the demo. Disabled when demo mode is off."""
    if not get_settings().demo_mode:
        raise _err(404, "not_found", "not available")
    return db.scalars(select(User).order_by(User.role, User.id)).all()


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


# ------------------------------------------------------------------ briefs


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
        raise _wf(e) from e
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
        raise _wf(e) from e
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
        raise _wf(e) from e
    return _detail(db, brief)


@router.post("/briefs/{brief_id}/quotes", response_model=QuoteOut, status_code=201)
def create_quote(brief_id: uuid.UUID, body: QuoteIn, db: DB, user: CurrentUser, rid: RequestId):
    require(user, "analyst")
    brief = load_brief(db, brief_id, user)
    try:
        quote = svc.create_quote(
            db, user=user, brief=brief, discount_pct=str(body.discount_pct), request_id=rid
        )
    except svc.WorkflowError as e:
        raise _wf(e) from e
    return quote_out(db, quote)


@router.get("/briefs/{brief_id}/audit", response_model=list[AuditOut])
def brief_audit(brief_id: uuid.UUID, db: DB, user: CurrentUser):
    """Full history of a brief and all its quotes, in order (UAT-A09)."""
    require(user, "auditor", "admin", "analyst", "manager")
    load_brief(db, brief_id, user)
    return db.scalars(
        select(AuditEvent).where(AuditEvent.brief_id == brief_id).order_by(AuditEvent.id)
    ).all()


@router.get("/briefs/{brief_id}/notifications", response_model=list[NotificationOut])
def brief_notifications(brief_id: uuid.UUID, db: DB, user: CurrentUser):
    require(user, "auditor", "admin", "analyst", "manager")
    load_brief(db, brief_id, user)
    return db.scalars(
        select(Notification).where(Notification.brief_id == brief_id).order_by(Notification.created_at)
    ).all()


# ------------------------------------------------------------------ quotes & decisions


@router.get("/quotes", response_model=list[QueueItem])
def list_quotes(db: DB, user: CurrentUser, status: str = "pending_approval"):
    """Approval queue. Sales never sees other people's quotes, so it is excluded here."""
    require(user, "manager", "operations", "analyst", "admin", "auditor")
    rows = db.execute(
        select(Quote, Brief)
        .join(Brief, Brief.id == Quote.brief_id)
        .where(Quote.status == status)
        .order_by(Quote.created_at)
        .limit(200)
    ).all()
    return [
        QueueItem(**quote_out(db, q).model_dump(), brief_title=b.title, brief_created_by=b.created_by)
        for q, b in rows
    ]


@router.get("/quotes/{quote_id}", response_model=QuoteOut)
def get_quote(quote_id: uuid.UUID, db: DB, user: CurrentUser):
    return quote_out(db, load_quote(db, quote_id, user))


@router.post("/quotes/{quote_id}/feasibility", response_model=QuoteOut)
def feasibility(
    quote_id: uuid.UUID, body: FeasibilityIn, db: DB, user: CurrentUser, rid: RequestId
):
    require(user, "operations")
    load_quote(db, quote_id, user)
    try:
        appr.add_feasibility_note(
            db, user=user, quote_id=quote_id, feasible=body.feasible, note=body.note, request_id=rid
        )
    except svc.WorkflowError as e:
        raise _wf(e) from e
    return quote_out(db, db.get(Quote, quote_id))


@router.post("/quotes/{quote_id}/decision", response_model=QuoteOut)
def decision(quote_id: uuid.UUID, body: DecisionIn, db: DB, user: CurrentUser, rid: RequestId):
    require(user, "manager")
    load_quote(db, quote_id, user)
    try:
        appr.decide(
            db, user=user, quote_id=quote_id, decision=body.decision, reason=body.reason,
            expected_revision=body.expected_revision, request_id=rid,
        )
    except svc.WorkflowError as e:
        raise _wf(e) from e
    return quote_out(db, db.get(Quote, quote_id))


@router.post("/quotes/{quote_id}/finalize", response_model=QuoteOut)
def finalize(
    quote_id: uuid.UUID,
    db: DB,
    user: CurrentUser,
    response: Response,
    rid: RequestId,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
):
    require(user, "analyst", "manager")
    load_quote(db, quote_id, user)
    try:
        _, replayed = appr.finalize(
            db, user=user, quote_id=quote_id, idempotency_key=idempotency_key, request_id=rid
        )
    except svc.WorkflowError as e:
        raise _wf(e) from e
    if replayed:
        response.headers["Idempotent-Replay"] = "true"
    else:
        # Best-effort immediate delivery; failures stay queued with their error recorded.
        try:
            outbox.dispatch(db)
        except Exception:  # pragma: no cover - logged, never hides the committed finalize
            db.rollback()
            log.exception("outbox dispatch after finalize failed")
    return quote_out(db, db.get(Quote, quote_id))


@router.get("/quotes/{quote_id}/proposal", response_class=HTMLResponse)
def proposal(quote_id: uuid.UUID, db: DB, user: CurrentUser):
    """Final document when finalized (byte-identical to what was hashed), else a DRAFT export."""
    quote = load_quote(db, quote_id, user)
    final = db.scalar(select(FinalProposal).where(FinalProposal.quote_id == quote.id))
    html = final.document_html if final else appr.draft_document(db, quote)
    return HTMLResponse(
        html,
        headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/quotes/{quote_id}/replay")
def replay(quote_id: uuid.UUID, db: DB, user: CurrentUser):
    require(user, "auditor", "admin", "analyst", "manager")
    return appr.replay_quote(db, load_quote(db, quote_id, user))


# ------------------------------------------------------------------ rate cards


@router.get("/rate-cards/active")
def get_active_rate_card(db: DB, user: CurrentUser):
    try:
        card = svc.active_rate_card(db)
    except svc.WorkflowError as e:
        raise _wf(e) from e
    return {"version": card.version, "effective_from": card.effective_from, **card.payload}


@router.get("/rate-cards")
def list_rate_cards(db: DB, user: CurrentUser) -> list[dict[str, Any]]:
    cards = db.scalars(select(RateCard).order_by(RateCard.created_at.desc())).all()
    return [
        {"version": c.version, "effective_from": c.effective_from, "is_active": c.is_active,
         "created_by": c.created_by, "created_at": c.created_at}
        for c in cards
    ]


class RateCardBody(BaseModel):
    version: str
    effective_from: date
    floor_margin_pct: Decimal
    discount_review_threshold_pct: Decimal
    max_discount_pct: Decimal
    geo_multipliers: dict[str, tuple[Decimal, Decimal]]
    catalog: dict[str, dict[str, Any]]


@router.post("/rate-cards", status_code=201)
def publish_rate_card(body: RateCardBody, db: DB, user: CurrentUser, rid: RequestId):
    require(user, "admin")
    try:
        card = appr.publish_rate_card(db, user=user, data=body.model_dump(), request_id=rid)
    except svc.WorkflowError as e:
        raise _wf(e) from e
    return {"version": card.version, "is_active": card.is_active}


@router.get("/audit/{aggregate_id}", response_model=list[AuditOut])
def audit_trail(aggregate_id: str, db: DB, user: CurrentUser):
    require(user, "auditor", "admin")
    return db.scalars(
        select(AuditEvent).where(AuditEvent.aggregate_id == aggregate_id).order_by(AuditEvent.id)
    ).all()


# ------------------------------------------------------------------ outbox admin (A-FR-12)


@router.post("/admin/outbox/dispatch")
def dispatch_outbox(db: DB, user: CurrentUser):
    require(user, "admin")
    r = outbox.dispatch(db)
    return {"sent": r.sent, "retried": r.retried, "failed": r.failed}


@router.post("/admin/outbox/{notification_id}/requeue", response_model=NotificationOut)
def requeue_notification(notification_id: uuid.UUID, db: DB, user: CurrentUser):
    require(user, "admin")
    try:
        return outbox.requeue(db, notification_id)
    except LookupError as e:
        raise _err(404, "not_found", "notification not found") from e


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
