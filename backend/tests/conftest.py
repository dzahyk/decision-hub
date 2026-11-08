import os

# Must be set before any app module creates the engine.
os.environ["DH_DATABASE_URL"] = os.environ.get(
    "DH_TEST_DATABASE_URL", "postgresql+psycopg:///decision_hub_test"
)
os.environ["DH_LLM_PROVIDER"] = "mock"

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth import issue_token
from app.db import SessionLocal, engine
from app.main import app
from app.seed import seed
from app.services import outbox

HERE = os.path.dirname(__file__)


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    """Run the real migrations once, so tests also prove the migration works."""
    cfg = Config(os.path.join(HERE, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(HERE, "..", "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_db(migrated_db):
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE notifications, final_proposals, approvals, feasibility_notes, "
                "audit_events, quotes, requirement_revisions, briefs, rate_cards, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    outbox.set_notifier(outbox.MockNotifier())
    with SessionLocal() as s:
        seed(s)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def as_user(user_id: str, **extra) -> dict[str, str]:
    """Headers carrying a server-signed session token for a seeded demo user."""
    return {"Authorization": f"Bearer {issue_token(user_id)}", **extra}
