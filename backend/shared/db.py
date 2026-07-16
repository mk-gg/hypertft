"""PostgreSQL connection pool factory and schema bootstrap.

All three services (collector, aggregator, API) import from here. Each process
creates a single :class:`psycopg_pool.ConnectionPool` from the ``DATABASE_URL``
connection string and reuses it for the lifetime of that process.

This module replaces the former DynamoDB resource factory. The DDL below is
idempotent (``IF NOT EXISTS`` everywhere), mirroring the old ``ensure_tables``
bootstrap so the schema is created automatically on first run.
"""

from __future__ import annotations

import logging

from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)


# ── Schema ──────────────────────────────────────────────────────────────────
#
# Storage is tiered so it stays flat as total match volume grows:
#
#   processed_matches ← permanent dedup ledger (one tiny row per match, forever)
#   match_slim        ← slim per-match extract (rolling window: recent patches)
#   comp_stats        ← aggregated stats the API serves (kept per patch, forever)
#   meta              ← key/value metadata (watermarks, summaries, patch roster)
#   matches           ← legacy raw payloads (no longer written; prune after
#                       verifying the slim pipeline)
#
# ``comp_stats.units_norm`` is the lowercased, sorted unit list (identical to
# the components of ``comp_key``). The GIN index on it powers containment
# (``@>``) and overlap (``&&``) queries, letting the API filter comps by unit
# in SQL instead of scanning every row in Python.
#
# psycopg's extended query protocol sends one statement per ``execute`` call,
# so the schema is expressed as a list of individual statements.
_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS matches (
        match_id   TEXT        PRIMARY KEY,
        region     TEXT        NOT NULL,
        tft_patch  TEXT        NOT NULL,
        data       JSONB       NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_matches_tft_patch ON matches (tft_patch)",
    # Permanent dedup ledger — ~60 bytes/row, so it can grow forever.
    """
    CREATE TABLE IF NOT EXISTS processed_matches (
        match_id     TEXT        PRIMARY KEY,
        region       TEXT        NOT NULL,
        tft_patch    TEXT        NOT NULL,
        processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_processed_tft_patch "
    "ON processed_matches (tft_patch)",
    # Slim per-match extract (see shared/slim.py for the participants schema).
    # Rolling window: the aggregator prunes patches older than the last two.
    """
    CREATE TABLE IF NOT EXISTS match_slim (
        match_id     TEXT        PRIMARY KEY,
        region       TEXT        NOT NULL,
        tft_patch    TEXT        NOT NULL,
        participants JSONB       NOT NULL,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_match_slim_tft_patch "
    "ON match_slim (tft_patch)",
    "CREATE INDEX IF NOT EXISTS idx_match_slim_created_at "
    "ON match_slim (created_at)",
    """
    CREATE TABLE IF NOT EXISTS comp_stats (
        patch       TEXT             NOT NULL,
        comp_key    TEXT             NOT NULL,
        units       TEXT[]           NOT NULL,
        units_norm  TEXT[]           NOT NULL,
        exact_sum   DOUBLE PRECISION NOT NULL DEFAULT 0,
        exact_avg   DOUBLE PRECISION NOT NULL DEFAULT 0,
        exact_n     INTEGER          NOT NULL DEFAULT 0,
        super_avg   DOUBLE PRECISION NOT NULL DEFAULT 0,
        super_n     INTEGER          NOT NULL DEFAULT 0,
        mutations   JSONB            NOT NULL DEFAULT '[]'::jsonb,
        additions   JSONB            NOT NULL DEFAULT '[]'::jsonb,
        exact_items JSONB            NOT NULL DEFAULT '[]'::jsonb,
        super_items JSONB            NOT NULL DEFAULT '[]'::jsonb,
        PRIMARY KEY (patch, comp_key)
    )
    """,
    # Upgrade path for databases created before exact_sum existed. The running
    # sum is what lets the incremental fold update averages without re-reading
    # historical matches. (Legacy rows were backfilled once via
    # scripts/backfill_slim.py; fresh databases never need it.)
    "ALTER TABLE comp_stats ADD COLUMN IF NOT EXISTS "
    "exact_sum DOUBLE PRECISION NOT NULL DEFAULT 0",
    # GIN index on the normalised unit array — the core of this migration.
    "CREATE INDEX IF NOT EXISTS idx_comp_stats_units_gin "
    "ON comp_stats USING GIN (units_norm)",
    "CREATE INDEX IF NOT EXISTS idx_comp_stats_patch ON comp_stats (patch)",
    """
    CREATE TABLE IF NOT EXISTS meta (
        meta_key TEXT  PRIMARY KEY,
        value    JSONB NOT NULL
    )
    """,
)


def create_pool(
    database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 10,
) -> ConnectionPool:
    """Create and open a psycopg connection pool for ``database_url``.

    Args:
        database_url: A libpq connection string, e.g.
            ``postgresql://user:pass@host:5432/dbname``.
        min_size: Minimum number of connections kept open in the pool.
        max_size: Maximum number of connections the pool may open.

    Returns:
        An opened :class:`~psycopg_pool.ConnectionPool`. The caller owns the
        pool and is responsible for closing it (``pool.close()``).
    """
    pool = ConnectionPool(
        conninfo=database_url,
        min_size=min_size,
        max_size=max_size,
        open=False,
    )
    pool.open()
    return pool


def init_schema(pool: ConnectionPool) -> None:
    """Create all tables and indexes if they do not already exist.

    Safe to call on every startup — every statement uses ``IF NOT EXISTS``.
    """
    with pool.connection() as conn:
        for statement in _SCHEMA_STATEMENTS:
            conn.execute(statement)
    logger.info("Database schema ready (matches, comp_stats, meta).")
