"""Aggregator settings loaded from environment variables via pydantic-settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class AggregatorConfig(BaseSettings):
    """Aggregator settings loaded from .env or environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # PostgreSQL connection string, e.g.
    # postgresql://user:password@host:5432/dbname
    database_url: str

    # Redis connection string (optional). The cache is flushed after each run
    # so fresh stats are served on the next read. No-op if unset.
    redis_url: str | None = None

    # Days between deep passes (full superset/mutation/item recompute over the
    # slim window). Between deep passes, runs do the cheap incremental fold of
    # exact stats only.
    deep_interval_days: int = 3

    # How many recent patches of slim match data to retain. Older patches keep
    # their aggregated comp_stats forever; only per-match data is pruned.
    slim_window_patches: int = 2

    super_threshold: float = 0.60
    min_n_comp: int        = 3
    min_n_mutation: int    = 2
    min_n_addition: int    = 2
    top_mutations: int     = 8
    top_additions: int     = 8
