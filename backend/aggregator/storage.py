"""
aggregator/storage.py
Reads raw matches from PostgreSQL, writes aggregated stats back.
"""

from __future__ import annotations

import logging
from datetime import datetime

from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from shared.constants import RANKED_QUEUE_ID

logger = logging.getLogger(__name__)


class AggregatorStorage:
    """PostgreSQL read/write operations for the aggregator."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    # ── Read ─────────────────────────────────────────────────────────────────

    def get_dirty_patches(self, since: datetime | None) -> list[str]:
        """Return the patches that received matches after ``since``.

        These are the only patches whose stats can have changed, so they are
        the only ones the aggregator needs to re-process. ``since=None`` (first
        run, or a forced full rebuild) returns every patch present.
        """
        with self._pool.connection() as conn:
            if since is None:
                rows = conn.execute(
                    "SELECT DISTINCT tft_patch FROM matches"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT DISTINCT tft_patch FROM matches WHERE created_at > %s",
                    (since,),
                ).fetchall()
        return [row[0] for row in rows]

    def load_ranked_matches_for_patch(self, patch: str) -> list[dict]:
        """Stream the raw Ranked match payloads for a single patch.

        A server-side (named) cursor keeps memory bounded; JSONB is decoded to
        Python dicts automatically. The queue filter is a safety net for any
        legacy rows collected before queue filtering existed at ingest.
        """
        raw_matches: list[dict] = []
        with self._pool.connection() as conn:
            with conn.cursor(name="patch_scan") as cur:
                cur.itersize = 1000
                cur.execute(
                    "SELECT data FROM matches "
                    "WHERE tft_patch = %s "
                    "AND (data->'info'->>'queue_id')::int = %s",
                    (patch, RANKED_QUEUE_ID),
                )
                for (data,) in cur:
                    raw_matches.append(data)
        return raw_matches

    def comp_patch_counts(self) -> dict[str, int]:
        """Return ``{patch: comp_count}`` straight from ``comp_stats``.

        The authoritative source for which patches have browsable stats and how
        many comps each holds. Cheap — counts the indexed ``patch`` column
        without touching the JSONB blobs.
        """
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT patch, count(*) FROM comp_stats GROUP BY patch"
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def distinct_regions(self) -> list[str]:
        """Return the distinct collection regions present in ``matches``."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT region FROM matches"
            ).fetchall()
        return [row[0] for row in rows]

    def read_meta(self, key: str) -> dict | None:
        """Read a metadata record by key, or ``None`` if it does not exist."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE meta_key = %s", (key,)
            ).fetchone()
        return row[0] if row else None

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
        """Replace aggregated comp stats for a single patch.

        Deletes the patch's existing comps and inserts the freshly computed set
        in one transaction, so comps that no longer appear (fell out of the
        meta, or came from now-removed matches) don't linger as stale rows —
        the row plus its ``exact_items`` / ``super_items`` are dropped together.

        ``units_norm`` (the lowercased unit list backing the GIN index) is
        derived from ``comp_key``, which the aggregator builds by joining the
        sorted, normalised unit names with ``|``.

        Returns:
            The number of comps written.
        """
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

        # Atomic per-patch replace: clear then re-insert in one transaction.
        with self._pool.connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM comp_stats WHERE patch = %s", (patch,))
            if params:
                cur.executemany(
                    """
                    INSERT INTO comp_stats (
                        patch, comp_key, units, units_norm,
                        exact_avg, exact_n, super_avg, super_n,
                        mutations, additions, exact_items, super_items
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
