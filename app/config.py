from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "llm-eval-harness"
    environment: str = "development"
    debug: bool = False
    database_url: str = "postgresql+asyncpg://eval:eval@localhost:5432/eval"
    redis_url: str = "redis://localhost:6379/0"
    eval_provider: Literal["fake", "openai", "anthropic"] = "fake"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    provider_request_timeout_ms: int = 60_000
    retry_max_attempts: int = 5
    retry_base_delay_ms: int = 100
    retry_max_delay_ms: int = 10_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
