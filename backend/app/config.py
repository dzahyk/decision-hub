from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DH_", extra="ignore")

    database_url: str = "postgresql+psycopg:///decision_hub"
    # "mock" needs no credentials. Real providers (Phase 3) read their key from the environment
    # and are never enabled implicitly.
    llm_provider: str = "mock"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
