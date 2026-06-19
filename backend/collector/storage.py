"""
collector/storage.py
PostgreSQL read/write operations used by the collector.
"""

from __future__ import annotations

import logging

from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from shared.patch_map import resolve_tft_patch

logger = logging.getLogger(__name__)


def _resolve_patch(match_data: dict) -> str:
    """Extract the game version from a raw match and map it to a TFT patch."""
    game_version = (
        match_data.get("info", {}).get("game_version", "")
        or match_data.get("game_version", "")
    )
    return resolve_tft_patch(game_version)


class CollectorStorage:
    """Handles all PostgreSQL operations for the collector:

    - Check which match IDs already exist (to skip re-downloading).
    - Write new raw match records.
    - Write/update patch and run metadata.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    # ── Matches ──────────────────────────────────────────────────────────────

    async def get_existing_match_ids(self) -> set[str]:
        """Return the set of all match IDs already stored.

        Kept ``async`` to preserve the collector's call site. The query itself
        is a blocking round-trip, which is acceptable for a batch job; the
        primary key index makes it an index-only scan.
        """
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT match_id FROM matches").fetchall()
        return {row[0] for row in rows}

    def write_match(self, match_id: str, region: str, match_data: dict) -> bool:
        """Write a single raw match record.

        Stores the full match payload as JSONB and the resolved ``tft_patch``
        for later filtering. Existing matches are left untouched.

        Returns:
            ``True`` if a new row was inserted, ``False`` if it already existed.
        """
        with self._pool.connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO matches (match_id, region, tft_patch, data)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (match_id) DO NOTHING
                """,
                (match_id, region, _resolve_patch(match_data), Json(match_data)),
            )
            return cur.rowcount > 0

    def write_matches_batch(self, matches: dict[str, dict], region: str) -> int:
        """Insert many matches in a single transaction.

        Already-stored matches are skipped via ``ON CONFLICT DO NOTHING``.

        Returns:
            The number of rows actually inserted.
        """
        if not matches:
            return 0

        params = [
            (match_id, region, _resolve_patch(match_data), Json(match_data))
            for match_id, match_data in matches.items()
        ]
        with self._pool.connection() as conn:
            cur = conn.cursor()
            cur.executemany(
                """
                INSERT INTO matches (match_id, region, tft_patch, data)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (match_id) DO NOTHING
                """,
                params,
            )
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    # ── Metadata ───────────────────────────────────────────────────────────────

    def upsert_meta(self, key: str, value: dict) -> None:
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

    def upsert_patch_data(self, patch_data) -> None:
        """Store the full patch roster under the ``patch_data`` metadata key.

        Unlike DynamoDB, JSONB has no practical item-size limit, so units,
        traits, and items are stored together in a single record rather than
        split across four rows.
        """
        dump = patch_data.model_dump(by_alias=True)
        self.upsert_meta(
            "patch_data",
            {
                "set_number": dump.get("set", 0),
                "patch":      dump.get("patch", "unknown"),
                "units":      dump.get("units", []),
                "traits":     dump.get("traits", []),
                "items":      dump.get("items", []),
            },
        )
        logger.info("Patch data stored (set %s, patch %s).",
                    dump.get("set", 0), dump.get("patch", "unknown"))

    def read_meta(self, key: str) -> dict | None:
        """Read a metadata record by key, or ``None`` if it does not exist."""
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE meta_key = %s", (key,)
            ).fetchone()
        return row[0] if row else None
