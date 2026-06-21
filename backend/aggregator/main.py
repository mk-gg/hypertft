"""
aggregator/main.py
Reads all raw matches from PostgreSQL, computes stats, writes back.

Usage:
    python -m aggregator.main
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aggregator.config import AggregatorConfig
from aggregator.compute import aggregate_comps, extract_participants, norm_unit
from aggregator.storage import AggregatorStorage
from shared.cache import create_cache
from shared.db import create_pool, init_schema

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def build_name_map(patch_data: dict | None) -> dict[str, str]:
    """
    Build {norm_id: display_name} from patch data stored in PostgreSQL.
    e.g. {'akali': 'Akali', 'leblanc': 'LeBlanc'}
    """
    if not patch_data:
        return {}
    return {
        norm_unit(u["id"]): u["name"]
        for u in patch_data.get("units", [])
        if u.get("id") and u.get("name")
    }


def _warn_unknown_items(participants: list[dict], patch_data: dict | None) -> None:
    """Log item ids that appear on units but are missing from the roster.

    Turns a missing-icon gap into a visible pipeline warning (with usage
    counts) instead of an iconless item you have to spot by eye in the UI.
    """
    roster = {
        (it.get("id") or "").lower()
        for it in (patch_data or {}).get("items", [])
    }
    if not roster:
        return

    from collections import Counter
    seen: Counter[str] = Counter()
    for p in participants:
        for items in p.get("items_by_unit", {}).values():
            for item_id in items:
                seen[item_id] += 1

    unknown = {i: n for i, n in seen.items() if i.lower() not in roster}
    if unknown:
        top = sorted(unknown.items(), key=lambda kv: kv[1], reverse=True)
        logger.warning(
            "%d item id(s) in matches missing from roster — add to the item "
            "filter so they get icons (top by usage): %s",
            len(unknown),
            ", ".join(f"{i} ({n})" for i, n in top[:15]),
        )


def _warn_unknown_units(participants: list[dict], patch_data: dict | None) -> None:
    """Log unit ids that appear on boards but are missing from the roster.

    Set mechanics can put non-draftable units on a player's board (e.g. Set 17
    spawns Bia & Bayin, Apex Primordian, the Cosmic Elder Dragon). If the
    roster filter dropped one, this surfaces it — with usage counts — so it can
    be added to the playable-specials allow-list instead of showing as a broken
    icon in comps.
    """
    from aggregator.compute import norm_unit

    roster = {
        norm_unit(u.get("id") or "")
        for u in (patch_data or {}).get("units", [])
    }
    if not roster:
        return

    from collections import Counter
    seen: Counter[str] = Counter()
    for p in participants:
        for unit in p.get("units", []):  # already normalised by extract_participants
            seen[unit] += 1

    unknown = {u: n for u, n in seen.items() if u not in roster}
    if unknown:
        top = sorted(unknown.items(), key=lambda kv: kv[1], reverse=True)
        logger.warning(
            "%d unit id(s) on boards missing from roster — add obtainable ones "
            "to the playable-specials list (top by usage): %s",
            len(unknown),
            ", ".join(f"{u} ({n})" for u, n in top[:15]),
        )


def main() -> None:
    config = AggregatorConfig()

    pool = create_pool(config.database_url)
    init_schema(pool)
    storage = AggregatorStorage(pool)

    # Load raw matches
    logger.info("Loading raw matches …")
    raw_matches = storage.scan_all_matches()
    if not raw_matches:
        logger.error("No matches found. Run the collector first.")
        pool.close()
        return

    # Load patch data for display name mapping
    patch_data = storage.read_patch_data()
    name_map   = build_name_map(patch_data)
    patch      = patch_data.get("patch", "unknown") if patch_data else "unknown"
    logger.info("Patch: %s | name_map: %d entries", patch, len(name_map))

    # Extract participants
    logger.info("Extracting participants …")
    participants = extract_participants(raw_matches)
    logger.info(
        "%d participants from %d matches.", len(participants), len(raw_matches)
    )

    # Surface any items/units that appear in matches but are missing from the
    # roster, so a missing icon shows up as a pipeline warning, not a silent gap.
    _warn_unknown_items(participants, patch_data)
    _warn_unknown_units(participants, patch_data)

    # ── Group participants by TFT patch ───────────────────────────────────
    from collections import defaultdict
    by_patch: dict[str, list[dict]] = defaultdict(list)
    for p in participants:
        by_patch[p["tft_patch"]].append(p)

    all_patches = sorted(by_patch.keys())
    logger.info(
        "Found %d patches: %s",
        len(all_patches),
        ", ".join(all_patches) if all_patches else "none",
    )

    # ── Aggregate per patch ────────────────────────────────────────────────
    total_written = 0
    for tft_patch, patch_participants in by_patch.items():
        logger.info(
            "Aggregating patch %s — %d participants (threshold=%.0f%%) …",
            tft_patch, len(patch_participants), config.super_threshold * 100,
        )
        comps = aggregate_comps(
            participants=patch_participants,
            name_map=name_map,
            super_threshold=config.super_threshold,
            min_n_comp=config.min_n_comp,
            min_n_mutation=config.min_n_mutation,
            min_n_addition=config.min_n_addition,
            top_mutations=config.top_mutations,
            top_additions=config.top_additions,
        )
        written = storage.write_comp_stats(patch=tft_patch, comps=comps)
        total_written += written
        logger.info(
            "Patch %s — %d comps written.", tft_patch, written
        )



    # ── Derive latest patch numerically (CDragon patchLine is unreliable) ───
    def _sort_key(p: str) -> tuple[int, int]:
        try:
            parts = p.split(".")
            return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            return (0, 0)

    latest_patch = sorted(all_patches, key=_sort_key, reverse=True)[0] if all_patches else "unknown"

    # ── Write summary metadata ─────────────────────────────────────────────
    storage.write_meta(
        "stats_summary",
        {
            "patch":              latest_patch,
            "set_number":         patch_data.get("set_number", 0) if patch_data else 0,
            "total_matches":      len(raw_matches),
            "total_participants": len(participants),
            "total_comps":        total_written,
            "last_updated":       datetime.now(timezone.utc).isoformat(),
            "regions":            list({p.get("region", "unknown") for p in participants}),
            "available_patches":  all_patches,
        },
    )

    logger.info(
        "Aggregation complete — %d total comps across %d patches. Latest: %s",
        total_written, len(all_patches), latest_patch,
    )

    # New stats are live — flush the API read cache so the next request
    # repopulates it from the freshly indexed PostgreSQL data.
    cache = create_cache(config.redis_url)
    cache.invalidate()
    cache.close()

    pool.close()


if __name__ == "__main__":
    main()
