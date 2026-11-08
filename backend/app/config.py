import secrets
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DH_", extra="ignore")

    database_url: str = "postgresql+psycopg:///decision_hub"
    # "mock" needs no credentials. Real providers (Phase 3) read their key from the environment
    # and are never enabled implicitly.
    llm_provider: str = "mock"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Sessions. Without DH_SESSION_SECRET a random per-process secret is used, so sessions end
    # on restart — fine for local dev, and deployments must set it.
    session_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(32))
    session_ttl_minutes: int = 8 * 60
    cookie_secure: bool = False
    # Demo mode lets anyone pick a synthetic persona without a password (portfolio demo).
    # Turn off for anything holding real data.
    demo_mode: bool = True

    # Outbox
    outbox_max_attempts: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
