"""
aggregator/main.py
Computes comp stats from raw matches and writes them back.

Only patches that received new matches since the last run are re-aggregated
(frozen patches are left untouched), which avoids needless rewrites of the
whole table every run. Pass --full to force a rebuild of every patch (e.g.
after changing aggregation parameters).

Usage:
    python -m aggregator.main
    python -m aggregator.main --full
"""

from __future__ import annotations

import argparse
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

# Watermark metadata key — ISO timestamp of the last successful aggregation.
# Patches with matches newer than this are the only ones that need rework.
_WATERMARK_KEY = "agg_watermark"


def _patch_sort_key(patch: str) -> tuple[int, int]:
    """Sort key turning ``'17.8'`` into ``(17, 8)`` for numeric ordering."""
    try:
        parts = patch.split(".")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return (0, 0)


def _read_watermark(storage: AggregatorStorage) -> datetime | None:
    """Return the timestamp of the last successful run, or ``None``."""
    rec = storage.read_meta(_WATERMARK_KEY)
    if not rec or not rec.get("ts"):
        return None
    try:
        return datetime.fromisoformat(rec["ts"])
    except ValueError:
        return None


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


def main(full: bool = False) -> None:
    config = AggregatorConfig()

    pool = create_pool(config.database_url)
    init_schema(pool)
    storage = AggregatorStorage(pool)

    try:
        # Patch roster, for display-name mapping and warnings.
        patch_data = storage.read_patch_data()
        name_map   = build_name_map(patch_data)

        # Incremental watermark — only patches with new matches need rework.
        # Capture the start time *before* reading so matches inserted during
        # this run are picked up next time rather than silently skipped.
        watermark   = None if full else _read_watermark(storage)
        run_started = datetime.now(timezone.utc)

        dirty_patches = storage.get_dirty_patches(watermark)
        if not dirty_patches:
            logger.info(
                "No new matches since %s — nothing to aggregate.", watermark
            )
            return

        logger.info(
            "%s aggregation — %d patch(es) to process: %s",
            "Full" if full else "Incremental",
            len(dirty_patches),
            ", ".join(sorted(dirty_patches)),
        )

        # Per-patch counts, so the global summary can be assembled from frozen
        # + freshly computed patches without re-scanning the whole table.
        summaries: dict[str, dict] = storage.read_meta("patch_summaries") or {}

        for tft_patch in sorted(dirty_patches):
            matches      = storage.load_ranked_matches_for_patch(tft_patch)
            participants = extract_participants(matches)
            logger.info(
                "Aggregating patch %s — %d participants from %d matches "
                "(threshold=%.0f%%) …",
                tft_patch, len(participants), len(matches),
                config.super_threshold * 100,
            )

            # Surface items/units present in matches but missing from the
            # roster, so a missing icon shows up as a warning, not a silent gap.
            _warn_unknown_items(participants, patch_data)
            _warn_unknown_units(participants, patch_data)

            comps = aggregate_comps(
                participants=participants,
                name_map=name_map,
                super_threshold=config.super_threshold,
                min_n_comp=config.min_n_comp,
                min_n_mutation=config.min_n_mutation,
                min_n_addition=config.min_n_addition,
                top_mutations=config.top_mutations,
                top_additions=config.top_additions,
            )
            written = storage.write_comp_stats(patch=tft_patch, comps=comps)
            summaries[tft_patch] = {
                "matches":      len(matches),
                "participants": len(participants),
                "comps":        written,
            }
            logger.info("Patch %s — %d comps written.", tft_patch, written)

        storage.write_meta("patch_summaries", summaries)

        # ── Build summary metadata ─────────────────────────────────────────
        # comp_stats is the source of truth for which patches are browsable;
        # the per-patch summaries supply the match/participant totals.
        comp_counts  = storage.comp_patch_counts()
        available    = sorted(comp_counts, key=_patch_sort_key, reverse=True)
        latest_patch = available[0] if available else "unknown"

        storage.write_meta(
            "stats_summary",
            {
                "patch":              latest_patch,
                "set_number":         patch_data.get("set_number", 0) if patch_data else 0,
                "total_matches":      sum(s.get("matches", 0) for s in summaries.values()),
                "total_participants": sum(s.get("participants", 0) for s in summaries.values()),
                "total_comps":        sum(comp_counts.values()),
                "last_updated":       datetime.now(timezone.utc).isoformat(),
                "regions":            storage.distinct_regions(),
                "available_patches":  available,
            },
        )

        # Advance the watermark only after a fully successful run.
        storage.write_meta(_WATERMARK_KEY, {"ts": run_started.isoformat()})

        logger.info(
            "Aggregation complete — %d patch(es) updated, %d total comps. Latest: %s",
            len(dirty_patches), sum(comp_counts.values()), latest_patch,
        )

        # New stats are live — flush the API read cache so the next request
        # repopulates it from the freshly indexed PostgreSQL data.
        cache = create_cache(config.redis_url)
        cache.invalidate()
        cache.close()
    finally:
        pool.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HyperTFT stats aggregator")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Re-aggregate every patch, ignoring the incremental watermark "
             "(use after changing aggregation parameters).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(full=args.full)
