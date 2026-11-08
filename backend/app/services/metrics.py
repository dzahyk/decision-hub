"""Operational metrics (A-FR-16) — every number is derived from the event and call logs so it
can be reconciled with the audit trail (UAT-A14). Read-only.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Approval, AuditEvent, Brief, LlmCall, Notification, Quote, utcnow

REPORTS = Path(__file__).resolve().parents[2] / "eval" / "reports"


def _hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600


def _dist(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0, "median_h": None, "p90_h": None}
    s = sorted(xs)
    return {
        "n": len(s),
        "median_h": round(statistics.median(s), 2),
        "p90_h": round(s[min(len(s) - 1, round(0.9 * (len(s) - 1)))], 2),
    }


def lead_times(db: Session) -> dict[str, Any]:
    """Received -> first priced quote, and received -> final proposal, from audit events."""
    received = dict(db.execute(select(Brief.id, Brief.received_at)).all())
    first_event: dict[str, dict[Any, datetime]] = {"priced": {}, "finalized": {}}
    rows = db.execute(
        select(AuditEvent.brief_id, AuditEvent.action, AuditEvent.at)
        .where(AuditEvent.action.in_(("quote.priced", "quote.finalized")))
        .order_by(AuditEvent.id)
    ).all()
    for brief_id, action, at in rows:
        key = "priced" if action == "quote.priced" else "finalized"
        first_event[key].setdefault(brief_id, at)
    return {
        "to_first_quote": _dist([_hours(received[b], t) for b, t in first_event["priced"].items() if b in received]),
        "to_final_proposal": _dist([_hours(received[b], t) for b, t in first_event["finalized"].items() if b in received]),
    }


def latest_eval() -> dict[str, Any] | None:
    if not REPORTS.exists():
        return None
    files = sorted(REPORTS.glob("*.json"), reverse=True)
    if not files:
        return None
    rep = json.loads(files[0].read_text())
    m, s = rep["meta"], rep["summary"]
    return {
        "file": files[0].name,
        "provider": m["provider"], "model": m["model"], "started_at": m["started_at"],
        "cases": s["cases"], "field_exact_match": s["field_exact_match"],
        "pricing_mismatch_rate": s["pricing_mismatch_rate"], "pii_leaks": len(s["pii_leaks"]),
    }


def overview(db: Session, *, days: int = 14) -> dict[str, Any]:
    now = utcnow()
    today = now.date()
    start_day = today - timedelta(days=days - 1)
    since = datetime.combine(start_day, datetime.min.time(), tzinfo=UTC)

    funnel = dict(db.execute(select(Brief.status, func.count()).group_by(Brief.status)).all())

    decisions = Counter(d for (d,) in db.execute(select(Approval.decision)).all())
    decided = decisions["approve"] + decisions["reject"]
    blocked = db.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.action == "quote.decision_blocked")
    )

    quotes = db.execute(select(Quote.pricing)).scalars().all()
    flagged_mgr = sum(1 for p in quotes if p.get("requires_manager_review"))
    flagged_ops = sum(1 for p in quotes if p.get("requires_operations_review"))

    calls = db.execute(
        select(LlmCall.status, LlmCall.error_class, LlmCall.cost_usd, LlmCall.latency_ms,
               LlmCall.input_tokens, LlmCall.output_tokens, LlmCall.pii_redacted, LlmCall.at,
               LlmCall.provider)
    ).all()
    errors = Counter(c.error_class for c in calls if c.status == "error")
    cost_total = sum((c.cost_usd or Decimal(0) for c in calls), Decimal(0))
    cost_today = sum((c.cost_usd or Decimal(0) for c in calls if c.at.date() == today), Decimal(0))
    pii_total = sum(sum((c.pii_redacted or {}).values()) for c in calls)

    notes = Counter(s for (s,) in db.execute(select(Notification.status)).all())

    # Daily series (UTC days): briefs received and extraction cost.
    received_by_day = Counter(
        r.date() for (r,) in db.execute(select(Brief.received_at).where(Brief.received_at >= since)).all()
    )
    cost_by_day: dict[date, Decimal] = {}
    for c in calls:
        if c.at >= since:
            cost_by_day[c.at.date()] = cost_by_day.get(c.at.date(), Decimal(0)) + (c.cost_usd or Decimal(0))
    series = [
        {
            "day": (start_day + timedelta(days=i)).isoformat(),
            "briefs": received_by_day.get(start_day + timedelta(days=i), 0),
            "cost_usd": str(cost_by_day.get(start_day + timedelta(days=i), Decimal(0)).quantize(Decimal("0.0001"))),
        }
        for i in range(days)
    ]

    latencies = [c.latency_ms for c in calls if c.status == "ok"]
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "briefs": {"total": sum(funnel.values()), "by_status": funnel},
        "lead_time": lead_times(db),
        "decisions": {
            "approved": decisions["approve"], "rejected": decisions["reject"],
            "rejection_rate": round(decisions["reject"] / decided, 4) if decided else None,
            "self_approval_blocked": blocked,
        },
        "quotes": {
            "total": len(quotes),
            "manager_review_share": round(flagged_mgr / len(quotes), 4) if quotes else None,
            "operations_review_share": round(flagged_ops / len(quotes), 4) if quotes else None,
        },
        "extraction": {
            "calls": len(calls),
            "errors": dict(errors),
            "error_rate": round(sum(errors.values()) / len(calls), 4) if calls else None,
            "providers": dict(Counter(c.provider for c in calls)),
            "cost_usd_total": str(cost_total.quantize(Decimal("0.0001"))),
            "cost_usd_today": str(cost_today.quantize(Decimal("0.0001"))),
            "tokens": {
                "input": sum(c.input_tokens or 0 for c in calls),
                "output": sum(c.output_tokens or 0 for c in calls),
            },
            "median_latency_ms": round(statistics.median(latencies), 1) if latencies else None,
            "pii_items_redacted": pii_total,
        },
        "notifications": {"sent": notes["sent"], "pending": notes["pending"], "failed": notes["failed"]},
        "eval": latest_eval(),
        "daily": series,
    }


def monitor(db: Session, *, limit: int = 50) -> dict[str, Any]:
    titles = dict(db.execute(select(Brief.id, Brief.title)).all())
    calls = db.scalars(select(LlmCall).order_by(LlmCall.at.desc()).limit(limit)).all()
    notes = db.scalars(select(Notification).order_by(Notification.created_at.desc()).limit(limit)).all()
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)).all()
    return {
        "llm_calls": [
            {
                "id": str(c.id), "brief_id": str(c.brief_id), "brief_title": titles.get(c.brief_id),
                "provider": c.provider, "model": c.model, "status": c.status,
                "error_class": c.error_class, "latency_ms": c.latency_ms,
                "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
                "cost_usd": str(c.cost_usd) if c.cost_usd is not None else None,
                "pii_redacted": c.pii_redacted, "request_id": c.request_id, "at": c.at.isoformat(),
            }
            for c in calls
        ],
        "notifications": [
            {
                "id": str(n.id), "brief_id": str(n.brief_id), "brief_title": titles.get(n.brief_id),
                "channel": n.channel, "status": n.status, "attempts": n.attempts,
                "last_error": n.last_error, "created_at": n.created_at.isoformat(),
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
            }
            for n in notes
        ],
        "events": [
            {
                "id": e.id, "brief_id": str(e.brief_id) if e.brief_id else None,
                "brief_title": titles.get(e.brief_id), "actor": e.actor, "action": e.action,
                "request_id": e.request_id, "at": e.at.isoformat(),
            }
            for e in events
        ],
    }
