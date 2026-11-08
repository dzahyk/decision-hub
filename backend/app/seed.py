"""Seed demo users and rate card v1. Idempotent: safe to run repeatedly.

Usage: uv run python -m app.seed
"""

from datetime import date

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.domain.pricing import RATE_CARD_V1
from app.models import RateCard, User

# Synthetic personas from BRD §A4. No real people.
DEMO_USERS = [
    ("ana.sales", "Ana Pratama", "sales"),
    ("bima.sales", "Bima Santoso", "sales"),
    ("budi.analyst", "Budi Hartono", "analyst"),
    ("citra.manager", "Citra Lestari", "manager"),
    ("dewi.ops", "Dewi Anggraini", "operations"),
    ("eko.admin", "Eko Wijaya", "admin"),
    ("fajar.auditor", "Fajar Nugroho", "auditor"),
]


def seed(db: Session) -> None:
    for uid, name, role in DEMO_USERS:
        if not db.get(User, uid):
            db.add(User(id=uid, display_name=name, role=role))
    db.flush()
    if not db.get(RateCard, RATE_CARD_V1["version"]):
        db.add(
            RateCard(
                version=RATE_CARD_V1["version"],
                payload=RATE_CARD_V1,
                effective_from=date(2026, 11, 1),
                is_active=True,
                created_by="eko.admin",
            )
        )
    db.commit()


if __name__ == "__main__":
    with SessionLocal() as s:
        seed(s)
    print("seeded users + rate card", RATE_CARD_V1["version"])
