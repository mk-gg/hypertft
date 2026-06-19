"""
collector/cdragon_client.py
Fetches current patch / unit / trait / item data from Community Dragon.
No rate limiting needed — CDragon is a public CDN.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp

from shared.models import ItemModel, PatchData, TraitModel, UnitModel

logger = logging.getLogger(__name__)

CDRAGON_URL = "https://raw.communitydragon.org/latest/cdragon/tft/en_us.json"


def _strip_prefix(raw: str) -> str:
    """'TFT17_Akali' → 'Akali'"""
    return re.sub(r"^TFT\w+_", "", raw, flags=re.IGNORECASE)


class CDragonClient:
    """Fetches and parses TFT patch data from Community Dragon."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def fetch_patch_data(self) -> PatchData | None:
        """
        Pull the full CDragon blob and return a PatchData model.
        Returns None if the request fails.
        """
        logger.info("Fetching CDragon patch data …")

        try:
            async with self._session.get(
                CDRAGON_URL,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    logger.error("CDragon returned HTTP %d", resp.status)
                    return None
                raw: dict[str, Any] = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.error("CDragon fetch failed: %s", exc)
            return None

        return self._parse(raw)

    # ── Parsing ────────────────────────────────────────────────────────────

    def _parse(self, raw: dict[str, Any]) -> PatchData:
        sets = raw.get("sets", {})
        latest_key = max(
            sets.keys(),
            key=lambda k: int(k) if str(k).isdigit() else 0,
        )
        current_set = sets[latest_key]

        patch = (
            raw.get("gameVariants", [{}])[0].get("patchLine", "unknown")
        )

        units  = self._parse_units(current_set.get("champions", []))
        traits = self._parse_traits(current_set.get("traits", []))
        items  = self._parse_items(raw.get("items", []))

        logger.info(
            "Parsed set %s (patch %s): %d units, %d traits, %d items",
            latest_key, patch, len(units), len(traits), len(items),
        )

        return PatchData.model_validate(
            {
                "set":    int(latest_key),
                "patch":  patch,
                "units":  [u.model_dump() for u in units],
                "traits": [t.model_dump() for t in traits],
                "items":  [i.model_dump() for i in items],
            }
        )

    @staticmethod
    def _is_playable_unit(api_name: str, champion: dict) -> bool:
        """
        Filter out non-playable units CDragon includes in the champions list.

        Known non-playable apiName patterns:
          TFTx_Enemy_   → PvE encounter bosses (Apex Primordian, etc.)
          TFTx_NPC_     → non-player characters
          TFTx_Summon_  → summoned units (Tibbers, Voidspawn, etc.)
          TFTx_Monster_ → PvE monsters
          TFTx_Item_    → item-granted units
          TFTx_Minion_  → minions

        Playable units always have:
          - cost >= 1  (0-cost = token/summon)
          - at least one trait (boss/encounter units have none)
        """
        if not api_name:
            return False

        lower = api_name.lower()
        non_playable_substrings = (
            "_enemy_", "_npc_", #"_summon_",
            "_monster_", "_item_", "_minion_",
        )
        if any(s in lower for s in non_playable_substrings):
            return False

        # 0-cost units are tokens or summons, not playable
        if (champion.get("cost") or 0) < 1:
            return False

        # Encounter/boss units have no traits
        # traits = [t for t in (champion.get("traits") or []) if t]
        # if not traits:
        #     return False

        return True

    @staticmethod
    def _parse_units(champions: list[dict]) -> list[UnitModel]:
        units = []
        for c in champions:
            api_name = c.get("apiName") or ""
            if not CDragonClient._is_playable_unit(api_name, c):
                continue
            units.append(
                UnitModel(
                    id=api_name,
                    name=c.get("name") or _strip_prefix(api_name),
                    cost=c.get("cost") or 0,
                    traits=[t for t in (c.get("traits") or []) if t],
                    icon=c.get("squareIcon") or "",
                )
            )
        return units

    @staticmethod
    def _parse_traits(traits: list[dict]) -> list[TraitModel]:
        return [
            TraitModel(
                id=t.get("apiName") or "",
                name=t.get("name") or _strip_prefix(t.get("apiName") or ""),
                icon=t.get("icon") or "",  # None → ""
            )
            for t in traits
        ]

    @staticmethod
    def _is_equippable(item: dict) -> bool:
        """
        CDragon ships ~3600 items including augments, consumables, eggs,
        tactician gear, event items, and internal placeholders.
        We keep only items players can actually equip on units:
          - Has inDefaultItemList = True, OR
          - Has a non-empty composition list (crafted items), OR
          - Is a base component (integer id 1–9)
        And we drop anything whose apiName contains a known non-equippable
        keyword regardless of the above flags.
        """
        api_name: str = (item.get("apiName") or "").lower()
        name: str     = (item.get("name") or "").strip()

        if not api_name or not name:
            return False

        # Drop known non-equippable categories by keyword
        skip = (
            "augment", "tactician", "ornn", "consumable",
            "elixir", "placeholder", "template", "debug",
            "_test", "tutorial", "event", "blessing", "egg",
        )
        if any(s in api_name for s in skip):
            return False

        # Accept items flagged as default list items
        if item.get("inDefaultItemList"):
            return True

        # Accept crafted items (have a composition list)
        if item.get("composition"):
            return True

        # Accept base components (id is integer 1–9)
        try:
            item_id = item.get("id")
            if item_id is not None and 1 <= int(item_id) <= 9:
                return True
        except (TypeError, ValueError):
            pass

        return False

    @staticmethod
    def _parse_items(items: list[dict]) -> list[ItemModel]:
        """
        Filter down to equippable unit items only (~150–200 items vs ~3600).
        Keeps the stored patch roster lean and relevant to comp analysis.
        """
        parsed = []
        for i in items:
            if not CDragonClient._is_equippable(i):
                continue
            api_name = i.get("apiName") or ""
            parsed.append(
                ItemModel(
                    id=api_name,
                    name=i.get("name") or _strip_prefix(api_name),
                    icon=i.get("icon") or "",
                )
            )
        return parsed
