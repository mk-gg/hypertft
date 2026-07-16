"""Redis cache-aside helper shared by the API and the data pipeline.

The API uses :meth:`Cache.get_json` / :meth:`Cache.set_json` to lazily cache
read-heavy query results (the tier list, patch list, meta summary, and unit
roster). The collector and aggregator call :meth:`Cache.invalidate` after
writing new data, so the next read repopulates the cache from PostgreSQL.

Redis is optional. If ``REDIS_URL`` is unset or Redis is unreachable, every
operation degrades to a no-op (treated as a cache miss) and callers fall back
to PostgreSQL. Caching must never take the API down.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis

logger = logging.getLogger(__name__)

# All cache entries live under this prefix so the whole namespace can be
# invalidated as a group without disturbing unrelated keys in a shared Redis.
CACHE_PREFIX = "hypertft:cache:"


class Cache:
    """Thin cache-aside wrapper around a Redis client.

    A ``None`` client means caching is disabled; every method becomes a safe
    no-op. All Redis errors are caught and logged so a cache outage never
    propagates to the caller.
    """

    def __init__(self, client: redis.Redis | None, default_ttl: int = 3600) -> None:
        self._client = client
        self._default_ttl = default_ttl

    @property
    def enabled(self) -> bool:
        """Whether a live Redis client is attached."""
        return self._client is not None

    def get_json(self, key: str) -> Any | None:
        """Return the cached JSON value for ``key``, or ``None`` on miss/error."""
        if self._client is None:
            return None
        try:
            raw = self._client.get(CACHE_PREFIX + key)
        except redis.RedisError as exc:
            logger.warning("Cache GET failed (%s) — falling back to DB.", exc)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store ``value`` as JSON under ``key`` with a TTL safety net."""
        if self._client is None:
            return
        try:
            self._client.set(
                CACHE_PREFIX + key,
                json.dumps(value, ensure_ascii=False, default=str),
                ex=ttl if ttl is not None else self._default_ttl,
            )
        except redis.RedisError as exc:
            logger.warning("Cache SET failed (%s) — continuing.", exc)

    def invalidate(self) -> int:
        """Delete every cache entry under :data:`CACHE_PREFIX`.

        Called by the collector and aggregator after writing new data. Uses
        ``SCAN`` + ``UNLINK`` to avoid blocking Redis on large keyspaces.

        Returns:
            The number of keys removed (0 if caching is disabled).
        """
        if self._client is None:
            return 0
        removed = 0
        try:
            for cache_key in self._client.scan_iter(
                match=CACHE_PREFIX + "*", count=500
            ):
                self._client.unlink(cache_key)
                removed += 1
        except redis.RedisError as exc:
            logger.warning("Cache invalidation failed: %s", exc)
            return removed
        if removed:
            logger.info("Invalidated %d cached entries.", removed)
        return removed

    def close(self) -> None:
        """Close the underlying Redis connection pool, if any."""
        if self._client is not None:
            self._client.close()


def create_cache(redis_url: str | None, default_ttl: int = 3600) -> Cache:
    """Build a :class:`Cache` from a ``redis://`` URL.

    Returns a disabled cache (no-op) when ``redis_url`` is empty or the initial
    connectivity check fails, so the application keeps working without Redis.
    """
    if not redis_url:
        logger.info("REDIS_URL not set — caching disabled.")
        return Cache(None, default_ttl)
    try:
        client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        client.ping()
    except redis.RedisError as exc:
        logger.warning("Redis unavailable (%s) — caching disabled.", exc)
        return Cache(None, default_ttl)
    logger.info("Redis cache connected.")
    return Cache(client, default_ttl)
