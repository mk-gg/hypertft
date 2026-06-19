"""
collector/riot_client.py
All calls to the Riot Games TFT API.
Each method returns parsed JSON or None on failure.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from collector.config import APEX_TIERS, TIER_GROUPS, PLATFORM_TO_REGIONAL
from collector.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class RiotClient:
    """
    Wraps all Riot API endpoints used by the collector.

    Parameters
    ----------
    api_key     : Riot Games personal/dev API key
    rate_limiter: Shared RateLimiter instance
    session     : aiohttp.ClientSession (caller owns lifecycle)
    """

    def __init__(
        self,
        api_key: str,
        rate_limiter: RateLimiter,
        session: aiohttp.ClientSession,
    ) -> None:
        self._api_key      = api_key
        self._rate_limiter = rate_limiter
        self._session      = session

    # ── Internal HTTP ──────────────────────────────────────────────────────

    async def _get(self, url: str) -> Any | None:
        """
        Rate-limited GET with automatic 429 back-off (one retry).
        Returns parsed JSON or None on any non-200 response.
        """
        await self._rate_limiter.acquire()
        headers = {"X-Riot-Token": self._api_key}

        try:
            async with self._session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    retry_after = int(resp.headers.get("Retry-After", 5))
                    logger.warning("429 received — backing off %ds", retry_after)
                    import asyncio
                    await asyncio.sleep(retry_after + 0.5)
                    return await self._get(url)

                if resp.status == 404:
                    return None

                if resp.status != 200:
                    logger.error("HTTP %d for %s", resp.status, url)
                    return None

                return await resp.json(content_type=None)

        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.error("Request error for %s: %s", url, exc)
            return None

    # ── Seed endpoints ─────────────────────────────────────────────────────

    async def get_apex_entries(
        self,
        platform: str,
        tier: str,
    ) -> list[dict]:
        """
        Fetch all entries for Challenger / Grandmaster / Master.
        Returns a flat list of entry dicts (each has a 'puuid' field).
        """
        url = f"https://{platform}.api.riotgames.com/tft/league/v1/{tier.lower()}"
        data = await self._get(url)
        if not data:
            return []
        return data.get("entries", [])

    async def get_paginated_entries(
        self,
        platform: str,
        tier: str,
        division: str,
        page: int = 1,
    ) -> list[dict]:
        """
        Fetch one page of league entries for a tier/division.
        Returns a list of entry dicts (each has a 'puuid' field).
        """
        url = (
            f"https://{platform}.api.riotgames.com"
            f"/tft/league/v1/entries/{tier}/{division}?page={page}"
        )
        data = await self._get(url)
        return data if isinstance(data, list) else []

    # ── Match history ──────────────────────────────────────────────────────

    async def get_match_ids(
        self,
        regional: str,
        puuid: str,
        queue: int,
        count: int = 20,
    ) -> list[str]:
        """Return up to `count` recent ranked match IDs for a PUUID."""
        url = (
            f"https://{regional}.api.riotgames.com"
            f"/tft/match/v1/matches/by-puuid/{puuid}/ids"
            f"?queue={queue}&count={count}"
        )
        data = await self._get(url)
        return data if isinstance(data, list) else []

    async def get_match(
        self,
        regional: str,
        match_id: str,
    ) -> dict | None:
        """Fetch full match detail JSON."""
        url = (
            f"https://{regional}.api.riotgames.com"
            f"/tft/match/v1/matches/{match_id}"
        )
        return await self._get(url)

    # ── Seed collection ────────────────────────────────────────────────────

    async def collect_seed_puuids(
        self,
        platform: str,
        group_quotas: dict[str, int],
        seed_pages: int,
    ) -> list[str]:
        """
        Collect PUUIDs respecting per-tier-group quotas so every rank
        (Challenger → Platinum) is represented in the seed pool.

        group_quotas maps group name → max PUUIDs for that group, e.g.:
          { "Challenger": 50, "Diamond": 75, "Platinum I-II": 75, ... }

        Within each group we walk divisions top-down until the quota is met.
        """
        seen:   set[str]  = set()
        all_puuids: list[str] = []

        for group in TIER_GROUPS:
            name  = group["name"]
            quota = group_quotas.get(name, group["quota"])
            tiers = group["tiers"]

            group_puuids: list[str] = []

            for tier, division in tiers:
                if len(group_puuids) >= quota:
                    break

                if tier in APEX_TIERS:
                    entries = await self.get_apex_entries(platform, tier)
                    added = 0
                    for e in entries:
                        if len(group_puuids) >= quota:
                            break
                        puuid = e.get("puuid")
                        if puuid and puuid not in seen:
                            seen.add(puuid)
                            group_puuids.append(puuid)
                            added += 1
                    if added:
                        logger.info(
                            "[%s] %s: +%d PUUIDs (%d/%d quota)",
                            platform, tier, added, len(group_puuids), quota,
                        )
                    continue

                # Paginated tiers
                for page in range(1, seed_pages + 1):
                    if len(group_puuids) >= quota:
                        break
                    entries = await self.get_paginated_entries(
                        platform, tier, division, page  # type: ignore[arg-type]
                    )
                    if not entries:
                        break
                    added = 0
                    for e in entries:
                        if len(group_puuids) >= quota:
                            break
                        puuid = e.get("puuid")
                        if puuid and puuid not in seen:
                            seen.add(puuid)
                            group_puuids.append(puuid)
                            added += 1
                    logger.info(
                        "[%s] %s %s page %d: +%d new (%d/%d quota)",
                        platform, tier, division, page,
                        added, len(group_puuids), quota,
                    )

            logger.info(
                "[%s] Group %-16s → %d PUUIDs collected (quota %d)",
                platform, f'"{name}"', len(group_puuids), quota,
            )
            all_puuids.extend(group_puuids)

        logger.info(
            "[%s] Total seed PUUIDs: %d across %d groups",
            platform, len(all_puuids), len(TIER_GROUPS),
        )
        return all_puuids

