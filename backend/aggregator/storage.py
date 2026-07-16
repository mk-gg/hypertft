"""Reads slim matches from PostgreSQL, writes aggregated stats back."""

from __future__ import annotations

import logging
from datetime import datetime

from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

from shared.compute_utils import comp_key
from shared.slim import slim_to_participants

logger = logging.getLogger(__name__)


class AggregatorStorage:
    """PostgreSQL read/write operations for the aggregator."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    # ── Read ─────────────────────────────────────────────────────────────────

    def watermark_cutoff(self) -> datetime:
        """Return the upper read boundary for this run, from the DB clock.

        All watermark comparisons use ``match_slim.created_at``, which the
        database assigns — so the cutoff must come from the database clock
        too, not the application host's. The 5-minute lag keeps rows written
        by collector transactions that were still in flight at read time from
        slipping *under* the new watermark unseen (the classic timestamp
        watermark race): anything newer than the lag simply waits for the
        next run.
        """
        with self._pool.connection() as conn:
            return conn.execute(
                "SELECT now() - interval '5 minutes'"
            ).fetchone()[0]

    def get_dirty_patches(
        self, since: datetime | None, until: datetime | None = None
    ) -> list[str]:
        """Return the patches that received slim matches in ``(since, until]``.

        These are the only patches whose stats can have changed, so they are
        the only ones the deep pass needs to re-process. ``since=None`` (first
        run, or a forced full rebuild) returns every patch in the window.
        """
        query = "SELECT DISTINCT tft_patch FROM match_slim WHERE TRUE"
        params: list = []
        if since is not None:
            query += " AND created_at > %s"
            params.append(since)
        if until is not None:
            query += " AND created_at <= %s"
            params.append(until)
        with self._pool.connection() as conn:
            rows = conn.execute(query, params or None).fetchall()
        return [row[0] for row in rows]

    def load_participants_for_patch(
        self, patch: str, until: datetime
    ) -> tuple[list[dict], int]:
        """Load a patch's participants from slim rows created up to ``until``.

        The upper bound keeps the deep pass's coverage aligned with the
        watermark it advances to: rows newer than ``until`` are excluded here
        and folded by a later run instead — never both.

        Returns:
            A ``(participants, match_count)`` tuple, where participants are in
            the shape :func:`aggregator.compute.aggregate_comps` consumes.
        """
        participants: list[dict] = []
        match_count = 0
        with self._pool.connection() as conn:
            with conn.cursor(name="slim_patch_scan") as cur:
                cur.itersize = 2000
                cur.execute(
                    "SELECT region, participants FROM match_slim "
                    "WHERE tft_patch = %s AND created_at <= %s",
                    (patch, until),
                )
                for region, slim in cur:
                    match_count += 1
                    participants.extend(slim_to_participants(slim, region, patch))
        return participants, match_count

    def load_new_participants(
        self, since: datetime | None, until: datetime
    ) -> tuple[list[dict], int]:
        """Load participants from slim matches created in ``(since, until]``.

        Feeds the cheap incremental fold: only rows the aggregator has not
        seen yet cross the wire. The closed upper bound is what makes the
        non-idempotent running-sum fold exactly-once — the next run starts
        strictly after ``until``.

        Returns:
            A ``(participants, match_count)`` tuple.
        """
        participants: list[dict] = []
        match_count = 0
        with self._pool.connection() as conn:
            with conn.cursor(name="slim_new_scan") as cur:
                cur.itersize = 2000
                if since is None:
                    cur.execute(
                        "SELECT region, tft_patch, participants FROM match_slim "
                        "WHERE created_at <= %s",
                        (until,),
                    )
                else:
                    cur.execute(
                        "SELECT region, tft_patch, participants FROM match_slim "
                        "WHERE created_at > %s AND created_at <= %s",
                        (since, until),
                    )
                for region, patch, slim in cur:
                    match_count += 1
                    participants.extend(slim_to_participants(slim, region, patch))
        return participants, match_count

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

    def processed_match_counts(self) -> dict[str, int]:
        """Return ``{patch: matches_processed}`` from the permanent ledger."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT tft_patch, count(*) FROM processed_matches "
                "GROUP BY tft_patch"
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def distinct_regions(self) -> list[str]:
        """Return the distinct collection regions ever processed."""
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT region FROM processed_matches"
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
        """Replace aggregated comp stats for a single patch (deep pass).

        Deletes the patch's existing comps and inserts the freshly computed set
        in one transaction, so comps that no longer appear (fell out of the
        meta, or came from now-removed matches) don't linger as stale rows —
        the row plus its ``exact_items`` / ``super_items`` are dropped together.

        ``units_norm`` (the lowercased unit list backing the GIN index) is
        derived from ``comp_key``, which the aggregator builds by joining the
        sorted, normalised unit names with ``|``.

        An empty ``comps`` list is a no-op rather than a wipe: it means the
        patch had no qualifying data this pass, and existing stats must not be
        destroyed (e.g. a pruned patch accidentally passed in).

        Returns:
            The number of comps written.
        """
        if not comps:
            logger.warning(
                "No comps computed for patch %s — leaving existing rows.", patch
            )
            return 0

        params = [
            (
                patch,
                comp["comp_key"],
                comp["units"],
                comp["comp_key"].split("|"),
                comp["exact_sum"],
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
            cur.executemany(
                """
                INSERT INTO comp_stats (
                    patch, comp_key, units, units_norm,
                    exact_sum, exact_avg, exact_n, super_avg, super_n,
                    mutations, additions, exact_items, super_items
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                params,
            )

        logger.info("Wrote %d comp stats to PostgreSQL.", len(comps))
        return len(comps)

    def fold_exact_stats(
        self,
        participants: list[dict],
        name_map: dict[str, str],
        advance_watermark_to: str | None = None,
    ) -> int:
        """Fold new participants into the running exact stats (cheap pass).

        For every board among ``participants``, upserts its comp's running
        placement sum and count, and recomputes ``exact_avg`` from them. New
        comps are inserted with empty superset/mutation/item fields — the next
        deep pass fills those in.

        This is what keeps day-to-day aggregation reads proportional to *new*
        matches rather than the whole patch.

        Args:
            participants: New participant dicts to fold.
            name_map: ``{normalised_id: display_name}`` for unit display names.
            advance_watermark_to: If given, the ``agg_watermark`` meta record
                is set to this ISO timestamp **in the same transaction** as the
                fold. The running sum is not idempotent, so the fold and the
                watermark that marks it done must commit or fail together.

        Returns:
            The number of distinct ``(patch, comp)`` rows touched.
        """
        # Accumulate per (patch, comp_key): sum, n, display units.
        folds: dict[tuple[str, str], list] = {}
        for p in participants:
            key = comp_key(p["units"])
            bucket = folds.setdefault(
                (p["tft_patch"], key),
                [0.0, 0, sorted(p["units"])],
            )
            bucket[0] += p["placement"]
            bucket[1] += 1

        params = [
            (
                patch,
                key,
                [name_map.get(u, u) for u in units],
                key.split("|"),
                total,
                n,
            )
            for (patch, key), (total, n, units) in folds.items()
        ]
        if not params:
            return 0

        with self._pool.connection() as conn:
            cur = conn.cursor()
            cur.executemany(
                """
                INSERT INTO comp_stats (
                    patch, comp_key, units, units_norm,
                    exact_sum, exact_n, exact_avg
                )
                VALUES (%s, %s, %s, %s, %s, %s,
                        ROUND((%s / %s)::numeric, 4))
                ON CONFLICT (patch, comp_key) DO UPDATE SET
                    exact_sum = comp_stats.exact_sum + EXCLUDED.exact_sum,
                    exact_n   = comp_stats.exact_n   + EXCLUDED.exact_n,
                    exact_avg = ROUND(((comp_stats.exact_sum + EXCLUDED.exact_sum)
                                / (comp_stats.exact_n + EXCLUDED.exact_n))::numeric, 4)
                """,
                [(*p, p[4], p[5]) for p in params],
            )
            if advance_watermark_to is not None:
                cur.execute(
                    """
                    INSERT INTO meta (meta_key, value)
                    VALUES (%s, %s)
                    ON CONFLICT (meta_key) DO UPDATE SET value = EXCLUDED.value
                    """,
                    ("agg_watermark", Json({"ts": advance_watermark_to})),
                )
        return len(params)

    def write_meta_many(self, records: dict[str, dict]) -> None:
        """Insert or overwrite several metadata records in one transaction."""
        with self._pool.connection() as conn:
            cur = conn.cursor()
            for key, value in records.items():
                cur.execute(
                    """
                    INSERT INTO meta (meta_key, value)
                    VALUES (%s, %s)
                    ON CONFLICT (meta_key) DO UPDATE SET value = EXCLUDED.value
                    """,
                    (key, Json(value)),
                )

    def prune_slim_window(self, keep_patches: list[str]) -> int:
        """Delete slim matches for patches outside the retention window.

        ``comp_stats`` rows for pruned patches are untouched — they stay
        browsable forever; only the per-match slim data is dropped.

        Returns:
            The number of slim rows deleted.
        """
        if not keep_patches:
            return 0
        with self._pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM match_slim WHERE tft_patch != ALL(%s)",
                (keep_patches,),
            )
            removed = cur.rowcount or 0
        if removed:
            logger.info(
                "Pruned %d slim matches outside window %s.",
                removed, keep_patches,
            )
        return removed

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
