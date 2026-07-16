"""Orchestrates a full collection run across all configured regions.

Usage:
    python -m collector.main
    python -m collector.main --platforms na1 euw1 kr
    python -m collector.main --platforms na1 --limit 200
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone

import aiohttp

from collector.cdragon_client import CDragonClient
from collector.config import (
    ALL_PLATFORMS,
    PLATFORM_TO_REGIONAL,
    RANKED_QUEUE_ID,
    CollectorConfig,
)
from collector.rate_limiter import RateLimiter
from collector.riot_client import RiotClient
from collector.storage import CollectorStorage
from shared.cache import create_cache
from shared.db import create_pool, init_schema

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Per-platform collection ────────────────────────────────────────────────

async def collect_platform(
    platform: str,
    riot: RiotClient,
    storage: CollectorStorage,
    config: CollectorConfig,
    target_new: int,
) -> int:
    """Run a full seed → expand → download cycle for one platform.

    Returns the number of new matches written.
    """
    regional = PLATFORM_TO_REGIONAL[platform]
    logger.info("=== Platform: %s (regional: %s) ===", platform, regional)

    # 1. Seed PUUIDs — quota-balanced across all rank groups
    puuids = await riot.collect_seed_puuids(
        platform=platform,
        group_quotas=config.tier_group_quotas,
        seed_pages=config.seed_pages,
    )
    if not puuids:
        logger.warning("[%s] No PUUIDs collected — skipping.", platform)
        return 0
    logger.info(
        "[%s] %d total seed PUUIDs (Chall→Plat IV, quota-balanced).",
        platform, len(puuids),
    )

    # 2. Expand to match IDs — fetch in concurrent batches of 10
    #    Sequential fetching at 5000 PUUIDs = hours of waiting.
    #    Batches of 10 use the rate budget ~10x more efficiently.
    match_ids: set[str] = set()
    BATCH = 10

    for batch_start in range(0, len(puuids), BATCH):
        batch_puuids = puuids[batch_start : batch_start + BATCH]
        results = await asyncio.gather(*[
            riot.get_match_ids(
                regional=regional,
                puuid=puuid,
                queue=RANKED_QUEUE_ID,
                count=config.matches_per_puuid,
            )
            for puuid in batch_puuids
        ], return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                match_ids.update(r)

        # Early exit — once we have enough IDs stop fetching histories
        if len(match_ids) >= config.target_matches_per_run * 3:
            logger.info(
                "[%s] Enough match IDs (%d) — stopping history expansion early.",
                platform, len(match_ids),
            )
            break

    logger.info("[%s] %d unique match IDs discovered.", platform, len(match_ids))

    # 3. Filter already-stored match IDs
    existing = await storage.get_existing_match_ids()
    new_ids  = [mid for mid in match_ids if mid not in existing]
    logger.info(
        "[%s] %d new (skipping %d existing).",
        platform, len(new_ids), len(match_ids) - len(new_ids),
    )

    if not new_ids:
        return 0

    # 4. Download and store
    written   = 0
    to_fetch  = new_ids[:target_new]
    batch: dict[str, dict] = {}

    for i, match_id in enumerate(to_fetch):
        match_data = await riot.get_match(regional=regional, match_id=match_id)
        if not match_data:
            continue

        batch[match_id] = match_data

        # Flush every 25 matches — keeps the run crash-safe (checkpointing)
        if len(batch) >= 25:
            written += storage.write_matches_batch(batch, region=platform)
            logger.info(
                "[%s] Stored %d/%d …",
                platform, written, len(to_fetch),
            )
            batch.clear()

    # Final flush
    if batch:
        written += storage.write_matches_batch(batch, region=platform)

    logger.info("[%s] Done — %d new matches stored.", platform, written)
    return written


# ── Main entry point ───────────────────────────────────────────────────────

async def main(platforms: list[str], limit: int | None) -> None:
    """Run a full collection cycle across the given platforms.

    Args:
        platforms: Platform codes to collect from (e.g. ``["na1", "euw1"]``).
        limit: Max new matches per platform; ``None`` uses the configured
            ``TARGET_MATCHES_PER_RUN``.
    """
    config = CollectorConfig()

    # Bootstrap the PostgreSQL pool and schema
    pool = create_pool(config.database_url)
    init_schema(pool)

    storage = CollectorStorage(pool)
    target_new = limit or config.target_matches_per_run

    try:
        async with aiohttp.ClientSession() as session:
            # Fetch patch data once (CDragon, no rate limit)
            cdragon = CDragonClient(session)
            patch_data = await cdragon.fetch_patch_data()
            if patch_data:
                storage.upsert_patch_data(patch_data)
                logger.info(
                    "Patch data saved — set %d, patch %s.",
                    patch_data.set_number,
                    patch_data.patch,
                )
            else:
                logger.warning("Could not fetch patch data — continuing.")

            # One shared rate limiter across all platforms
            # (they all share the same API key quota)
            rate_limiter = RateLimiter(
                per_second=config.rate_limit_per_second,
                per_2min=config.rate_limit_per_2min,
            )
            riot = RiotClient(
                api_key=config.riot_api_key,
                rate_limiter=rate_limiter,
                session=session,
            )

            # Collect sequentially per platform
            # (parallel would exhaust the shared rate limit immediately)
            total_written = 0
            for platform in platforms:
                if platform not in PLATFORM_TO_REGIONAL:
                    logger.warning("Unknown platform %s — skipping.", platform)
                    continue
                written = await collect_platform(
                    platform=platform,
                    riot=riot,
                    storage=storage,
                    config=config,
                    target_new=target_new,
                )
                total_written += written

        # Update run metadata
        storage.upsert_meta(
            "last_run",
            {
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "platforms":    platforms,
                "matches_added": total_written,
            },
        )

        logger.info("Collection complete — %d total new matches.", total_written)

        # The patch roster may have changed — flush the API read cache so the
        # next /meta/units read repopulates from PostgreSQL.
        cache = create_cache(config.redis_url)
        cache.invalidate()
        cache.close()
    finally:
        pool.close()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="TFT match collector")
    parser.add_argument(
        "--platforms",
        nargs="+",
        default=ALL_PLATFORMS,
        help="Platform(s) to collect from (default: all)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max new matches to add per platform (default: TARGET_MATCHES_PER_RUN)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    args = parse_args()
    asyncio.run(main(platforms=args.platforms, limit=args.limit))
