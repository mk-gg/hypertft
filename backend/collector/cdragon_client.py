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
        # Units that are normally non-playable but CAN reach a player board via
        # Set 17 mechanics — keep them so comps/items resolve their icons and
        # they're selectable. They have no traits, so the frontend still sorts
        # them after real champions.
        #
        # NOTE: Apex Primordian (TFT17_Enemy_Aatrox) is intentionally NOT here.
        # norm_unit() strips the full "TFT\w+_" prefix, so it collapses to the
        # same key ("aatrox") as the real Aatrox champion — including it
        # overwrites Aatrox's name/icon and merges its stats. Distinguishing
        # them would require changing norm_unit across the whole pipeline.
        playable_specials = {
            "tft17_summon",           # Bia & Bayin — spawned at Shepherd (3+)
            "tft17_pve_elderdragon",  # Cosmic Elder Dragon — via Bard's ability
        }
        if lower in playable_specials:
            return True

        non_playable_substrings = (
            "_enemy_", "_npc_", #"_summon_",
            "_monster_", "_item_", "_minion_",
        )
        if any(s in lower for s in non_playable_substrings):
            return False

        # Specific board props CDragon lists as champions but aren't selectable.
        non_playable_ids = {
            "tft9_slime_crab",  # Rift Scuttler
        }
        if lower in non_playable_ids:
            return False

        # Real champions are cost 1–5. Everything else in the champions list
        # (0-cost tokens; cost 8 anvils/tomes; cost 11 chests, PvE & lobby
        # props like the Mercenary Chest and Timebreaker) is not playable.
        # Note: Golem / Training Dummy / Mini Black Hole are cost 1, so they
        # remain in the roster (the frontend sorts them after real champions).
        cost = champion.get("cost") or 0
        if cost < 1 or cost > 5:
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
        CDragon ships ~3600 entries: augments, consumables, eggs, tactician
        gear, champion tokens, trait effects, market offerings, and internal
        placeholders. We keep the real, unit-equippable items.

        Denylist-primary by design. We accept anything that looks like an item
        — apiName contains ``_item_``, is a trait ``SquadItem`` / ``AnomalyItem``,
        or carries an equippable flag / composition / component id — UNLESS its
        apiName matches a known non-equippable keyword. Erring toward inclusion
        matters: the current set reuses legacy-named items from older sets
        (e.g. Ornn artifacts ``TFT4_Item_Ornn*``, Shimmerscale ``TFT7_Item_*``,
        old radiants ``TFT5_Item_*Radiant``), and those must still resolve to an
        icon. Harmless old-set bloat is the acceptable cost of full coverage.
        """
        api_name: str = (item.get("apiName") or "").lower()
        name: str     = (item.get("name") or "").strip()

        if not api_name or not name or not item.get("icon"):
            return False

        # Drop known non-equippable categories by keyword. Notes:
        #   - NOT "ornn": Ornn-named entries are real artifacts reused in the
        #     current set; their anvils/grants are excluded by "anvil"/"grant".
        #   - NOT "tactician": TFT_Item_Tacticians{Ring,Scepter} are real items;
        #     the Tactician's Crown/Cape/Shield are "_assist_" and excluded.
        skip = (
            "augment", "consumable", "elixir", "placeholder",
            "template", "debug", "_test", "tutorial", "blessing", "egg",
            "event", "anvil", "grant", "marketoffering", "_assist_",
        )
        if any(s in api_name for s in skip):
            return False

        # Equippable signals.
        if item.get("inDefaultItemList") or item.get("composition"):
            return True
        try:
            item_id = item.get("id")
            if item_id is not None and 1 <= int(item_id) <= 9:
                return True
        except (TypeError, ValueError):
            pass

        # Any standard / artifact / radiant / emblem / legacy item.
        if "_item_" in api_name:
            return True

        # Trait items (e.g. Anima Squad) and the Anomaly carry no "_item_".
        if "squaditem" in api_name or "anomalyitem" in api_name:
            return True

        return False

    @staticmethod
    def _parse_items(items: list[dict]) -> list[ItemModel]:
        """
        Filter CDragon's ~3600 entries down to unit-equippable items, including
        the current set's artifacts, radiants, mechanic items, and any legacy-
        named items the set reuses. Errs toward inclusion (see _is_equippable).
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
