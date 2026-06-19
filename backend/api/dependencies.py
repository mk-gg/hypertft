"""
api/dependencies.py
FastAPI dependency injection — shared PostgreSQL connection pool.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from psycopg_pool import ConnectionPool

from api.config import APIConfig
from shared.cache import Cache, create_cache
from shared.db import create_pool


@lru_cache
def get_config() -> APIConfig:
    """Return a cached settings instance loaded from the environment."""
    return APIConfig()


@lru_cache
def get_pool() -> ConnectionPool:
    """Return the process-wide connection pool, created on first use."""
    config = get_config()
    return create_pool(config.database_url)


@lru_cache
def get_cache() -> Cache:
    """Return the process-wide Redis cache, created on first use.

    Falls back to a disabled (no-op) cache if ``REDIS_URL`` is unset or Redis
    is unreachable, so the API keeps serving from PostgreSQL.
    """
    config = get_config()
    return create_cache(config.redis_url, default_ttl=config.cache_ttl_seconds)


# Type aliases for cleaner router signatures.
PoolDep  = Annotated[ConnectionPool, Depends(get_pool)]
CacheDep = Annotated[Cache, Depends(get_cache)]
