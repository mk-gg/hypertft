"""API settings loaded from environment variables via pydantic-settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class APIConfig(BaseSettings):
    """API settings loaded from .env or environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # PostgreSQL connection string, e.g.
    # postgresql://user:password@host:5432/dbname
    database_url: str

    # Redis connection string (optional). Caching is disabled if unset, e.g.
    # redis://localhost:6379/0  or  rediss://default:pass@host:6379
    redis_url: str | None = None
    cache_ttl_seconds: int = 3600

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # CORS — set to your frontend domain in production
    cors_origins: list[str] = ["*"]
