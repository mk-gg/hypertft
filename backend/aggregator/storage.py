"""
aggregator/storage.py
Reads raw matches from PostgreSQL, writes aggregated stats back.
"""

from __future__ import annotations

import logging

from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from shared.constants import RANKED_QUEUE_ID

logger = logging.getLogger(__name__)


class AggregatorStorage:
    """PostgreSQL read/write operations for the aggregator."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    # ── Read ─────────────────────────────────────────────────────────────────

    def scan_all_matches(self) -> list[dict]:
        """Stream every raw match payload from the database.

        A server-side (named) cursor is used so the full table is not buffered
        in client memory at once. JSONB columns are decoded to Python dicts by
        psycopg automatically, so no manual JSON parsing is required.

        Only Ranked games are returned. Older rows collected before queue
        filtering may include non-ranked modes, so the queue is filtered here
        as a safety net regardless of what is physically stored.
        """
        raw_matches: list[dict] = []
        with self._pool.connection() as conn:
            with conn.cursor(name="matches_scan") as cur:
                cur.itersize = 1000
                cur.execute(
                    "SELECT data FROM matches "
                    "WHERE (data->'info'->>'queue_id')::int = %s",
                    (RANKED_QUEUE_ID,),
                )
                for (data,) in cur:
                    raw_matches.append(data)

        logger.info("Loaded %d ranked matches from PostgreSQL.", len(raw_matches))
        return raw_matches

    def read_patch_data(self) -> dict | None:
        """Read the patch roster stored by the collector under ``patch_data``.

        Returns a dict with ``set_number``, ``patch``, ``units``, ``traits``,
        and ``items``, or ``None`` if the collector has not run yet.
        """
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE meta_key = %s", ("patch_data",)
            ).fetchone()

        if not row:
            logger.warning("patch_data not found — run collector first.")
            return None

        value = row[0]
        return {
            "set_number": int(value.get("set_number", 0)),
            "patch":      str(value.get("patch", "unknown")),
            "units":      value.get("units", []),
            "traits":     value.get("traits", []),
            "items":      value.get("items", []),
        }

    # ── Write ──────────────────────────────────────────────────────────────────

    def write_comp_stats(self, patch: str, comps: list[dict]) -> int:
        """Upsert aggregated comp stats for a single patch.

        ``units_norm`` (the lowercased unit list backing the GIN index) is
        derived from ``comp_key``, which the aggregator builds by joining the
        sorted, normalised unit names with ``|``.

        Returns:
            The number of comps written.
        """
        if not comps:
            return 0

        params = [
            (
                patch,
                comp["comp_key"],
                comp["units"],
                comp["comp_key"].split("|"),
                comp["exact_avg"],
                comp["exact_n"],
                comp["super_avg"],
                comp["super_n"],
                Json(comp["mutations"]),
                Json(comp["additions"]),
                Json(comp["exact_items"]),
                Json(comp["super_items"]),
            )
            for comp in comps
        ]

        with self._pool.connection() as conn:
            cur = conn.cursor()
            cur.executemany(
                """
                INSERT INTO comp_stats (
                    patch, comp_key, units, units_norm,
                    exact_avg, exact_n, super_avg, super_n,
                    mutations, additions, exact_items, super_items
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (patch, comp_key) DO UPDATE SET
                    units       = EXCLUDED.units,
                    units_norm  = EXCLUDED.units_norm,
                    exact_avg   = EXCLUDED.exact_avg,
                    exact_n     = EXCLUDED.exact_n,
                    super_avg   = EXCLUDED.super_avg,
                    super_n     = EXCLUDED.super_n,
                    mutations   = EXCLUDED.mutations,
                    additions   = EXCLUDED.additions,
                    exact_items = EXCLUDED.exact_items,
                    super_items = EXCLUDED.super_items
                """,
                params,
            )

        logger.info("Wrote %d comp stats to PostgreSQL.", len(comps))
        return len(comps)

    def write_meta(self, key: str, value: dict) -> None:
        """Insert or overwrite a metadata record stored as JSONB."""
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO meta (meta_key, value)
                VALUES (%s, %s)
                ON CONFLICT (meta_key) DO UPDATE SET value = EXCLUDED.value
                """,
                (key, Json(value)),
            )
