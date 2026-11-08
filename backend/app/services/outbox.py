"""Transactional outbox for mock email / CRM notifications (A-FR-11, A-FR-12, A-NFR-03).

Business changes and the notifications they cause are written in ONE transaction. A separate
dispatcher delivers them with bounded retries. Every delivery carries an idempotency key and the
receiving adapter de-duplicates on it, so a crash between "sent" and "marked sent" can never
produce a second business effect when the run is replayed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Notification, utcnow


class DeliveryError(RuntimeError):
    pass


class Notifier(Protocol):
    def deliver(self, channel: str, idempotency_key: str, payload: dict[str, Any]) -> None: ...


@dataclass
class MockNotifier:
    """Stands in for an email provider and a CRM. No network. `fail_next` injects faults."""

    delivered: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: int = 0
    fail_next: int = 0

    def deliver(self, channel: str, idempotency_key: str, payload: dict[str, Any]) -> None:
        self.calls += 1
        if self.fail_next > 0:
            self.fail_next -= 1
            raise DeliveryError(f"mock {channel} timeout")
        # Receiver-side de-duplication: same key -> same single effect.
        self.delivered.setdefault(idempotency_key, {"channel": channel, **payload})


_notifier: Notifier = MockNotifier()


def get_notifier() -> Notifier:
    return _notifier


def set_notifier(n: Notifier) -> None:  # tests and future real adapters
    global _notifier
    _notifier = n


def enqueue(
    db: Session, *, brief_id: uuid.UUID, channel: str, key: str, payload: dict[str, Any]
) -> Notification:
    n = Notification(brief_id=brief_id, channel=channel, idempotency_key=key, payload=payload)
    db.add(n)
    return n


@dataclass
class DispatchReport:
    sent: int = 0
    retried: int = 0
    failed: int = 0


def dispatch(db: Session, notifier: Notifier | None = None, *, limit: int = 50) -> DispatchReport:
    """Deliver pending notifications once each. Safe to run repeatedly or concurrently:
    rows are claimed with SKIP LOCKED and delivery is idempotent on the receiver."""
    notifier = notifier or get_notifier()
    max_attempts = get_settings().outbox_max_attempts
    report = DispatchReport()
    rows = db.scalars(
        select(Notification)
        .where(Notification.status == "pending")
        .order_by(Notification.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    for n in rows:
        n.attempts += 1
        try:
            notifier.deliver(n.channel, n.idempotency_key, n.payload)
        except Exception as exc:  # noqa: BLE001 - any adapter failure is recorded, then retried
            n.last_error = f"{type(exc).__name__}: {exc}"[:500]
            if n.attempts >= max_attempts:
                n.status = "failed"
                report.failed += 1
            else:
                report.retried += 1
            continue
        n.status = "sent"
        n.sent_at = utcnow()
        n.last_error = None
        report.sent += 1
    db.commit()
    return report


def requeue(db: Session, notification_id: uuid.UUID) -> Notification:
    """Replay a failed delivery from its checkpoint (A-FR-12). Same idempotency key."""
    n = db.get(Notification, notification_id, with_for_update=True)
    if n is None:
        raise LookupError("notification not found")
    if n.status == "failed":
        n.status = "pending"
        n.attempts = 0
    db.commit()
    return n
