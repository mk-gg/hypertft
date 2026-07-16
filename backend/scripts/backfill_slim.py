"""One-time backfill: populate match_slim + processed_matches from raw matches.

The slimming runs **server-side** as a single ``INSERT … SELECT`` with JSONB
transformations, so the ~20 KB raw payloads never cross the wire (a Python
round-trip would cost ~700 MB of egress at 36k matches). A random sample is
then re-slimmed locally with :func:`shared.slim.extract_slim_participants` and
compared field-by-field to prove the SQL produces identical output.

Idempotent — both inserts use ``ON CONFLICT DO NOTHING``.

Usage:
    python -m scripts.backfill_slim
"""

from __future__ import annotations

import logging

from collector.config import CollectorConfig
from shared.db import create_pool, init_schema
from shared.slim import extract_slim_participants

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Server-side equivalent of shared.slim.extract_slim_participants. The fields
# that drive comp stats (placement, character_id, itemNames) must match the
# Python extractor exactly; run_sample_check() verifies that they do.
_BACKFILL_SLIM_SQL = """
INSERT INTO match_slim (match_id, region, tft_patch, participants, created_at)
SELECT
    m.match_id,
    m.region,
    m.tft_patch,
    s.participants,
    m.created_at
FROM matches m
CROSS JOIN LATERAL (
    SELECT (
        SELECT jsonb_agg(
            jsonb_build_object(
                'pl',  (p->>'placement')::int,
                'lvl', COALESCE((p->>'level')::int, 0),
                'u',   (
                    SELECT jsonb_agg(
                        jsonb_build_object(
                            'c', u->>'character_id',
                            's', COALESCE((u->>'tier')::int, 1),
                            'i', COALESCE(
                                (
                                    SELECT jsonb_agg(i)
                                    FROM jsonb_array_elements_text(u->'itemNames') AS i
                                    WHERE i <> '' AND lower(i) <> 'tft_item_emptybag'
                                ),
                                '[]'::jsonb
                            )
                        )
                    )
                    FROM jsonb_array_elements(p->'units') AS u
                    WHERE COALESCE(u->>'character_id', '') <> ''
                ),
                'tr',  COALESCE(
                    (
                        SELECT jsonb_agg(
                            jsonb_build_object(
                                'n',  t->>'name',
                                'nu', COALESCE((t->>'num_units')::int, 0),
                                'tc', COALESCE((t->>'tier_current')::int, 0)
                            )
                        )
                        FROM jsonb_array_elements(p->'traits') AS t
                        WHERE COALESCE(t->>'name', '') <> ''
                    ),
                    '[]'::jsonb
                ),
                'aug', p->'augments'
            )
        )
        FROM jsonb_array_elements(m.data->'info'->'participants') AS p
        WHERE p->>'placement' IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM jsonb_array_elements(p->'units') AS u
              WHERE COALESCE(u->>'character_id', '') <> ''
          )
    ) AS participants
) AS s
WHERE (m.data->'info'->>'queue_id')::int = 1100
  AND m.tft_patch = %s
  AND s.participants IS NOT NULL
ON CONFLICT (match_id) DO NOTHING
"""

_BACKFILL_LEDGER_SQL = """
INSERT INTO processed_matches (match_id, region, tft_patch, processed_at)
SELECT match_id, region, tft_patch, created_at
FROM matches
WHERE (data->'info'->>'queue_id')::int = 1100
ON CONFLICT (match_id) DO NOTHING
"""


def run_sample_check(pool, sample_size: int = 100) -> None:
    """Compare SQL-slimmed rows against the Python extractor on a sample.

    Raises:
        AssertionError: if any sampled match's slim participants differ.
    """
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT m.match_id, m.data, s.participants
            FROM matches m
            JOIN match_slim s USING (match_id)
            ORDER BY random()
            LIMIT %s
            """,
            (sample_size,),
        ).fetchall()

    for match_id, raw, sql_slim in rows:
        py_slim = extract_slim_participants(raw)
        assert py_slim == sql_slim, (
            f"slim mismatch for {match_id}:\n"
            f"  python: {py_slim[:1]}\n"
            f"  sql:    {sql_slim[:1]}"
        )
    logger.info("Sample check passed — %d matches identical (SQL vs Python).",
                len(rows))


def main() -> None:
    """Run the backfill and report per-patch counts."""
    config = CollectorConfig()
    pool = create_pool(config.database_url)
    try:
        init_schema(pool)

        with pool.connection() as conn:
            # The per-patch slimming statements are heavy; lift the pooler's
            # default statement timeout for this session only.
            conn.execute("SET statement_timeout = '30min'")
            # One-shot migration: seed running sums for comp rows written
            # before exact_sum existed (no live row legitimately has a zero
            # sum with a non-zero count — placements are always >= 1).
            conn.execute(
                "UPDATE comp_stats SET exact_sum = exact_avg * exact_n "
                "WHERE exact_sum = 0 AND exact_n > 0"
            )
            patches = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT tft_patch FROM matches ORDER BY tft_patch"
                ).fetchall()
            ]
            slim_inserted = 0
            for patch in patches:
                n = conn.execute(_BACKFILL_SLIM_SQL, (patch,)).rowcount
                conn.commit()
                slim_inserted += n
                logger.info("  patch %s: +%d slim rows", patch, n)
            ledger_inserted = conn.execute(_BACKFILL_LEDGER_SQL).rowcount
        logger.info(
            "Backfilled %d slim rows, %d ledger rows.",
            slim_inserted, ledger_inserted,
        )

        run_sample_check(pool)

        with pool.connection() as conn:
            counts = conn.execute(
                "SELECT tft_patch, count(*) FROM match_slim GROUP BY tft_patch "
                "ORDER BY tft_patch"
            ).fetchall()
            size = conn.execute(
                "SELECT pg_size_pretty(pg_total_relation_size('match_slim')), "
                "pg_size_pretty(pg_total_relation_size('processed_matches'))"
            ).fetchone()
        for patch, n in counts:
            logger.info("  %s: %d slim matches", patch, n)
        logger.info("match_slim: %s | processed_matches: %s", size[0], size[1])
    finally:
        pool.close()


if __name__ == "__main__":
    main()
