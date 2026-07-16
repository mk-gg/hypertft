"""Computes comp stats from slim matches, in two tiers of work.

Every run performs the cheap **incremental fold**: slim matches newer than the
watermark are folded into each comp's running exact placement sum and count.
Reads are proportional to *new* matches, so daily runs cost seconds and a few
MB of transfer regardless of dataset size.

A **deep pass** additionally recomputes the expensive relational stats
(superset averages, mutations, additions, item recommendations) for every
patch with new data, reading that patch's full slim window. It runs
automatically every ``DEEP_INTERVAL_DAYS`` (default 3), or on demand via
``--deep``. The deep pass also prunes slim matches for patches outside the
retention window (``SLIM_WINDOW_PATCHES``, default 2) — their aggregated
``comp_stats`` remain browsable forever.

Usage:
    python -m aggregator.main          # incremental fold (+ deep when due)
    python -m aggregator.main --deep   # force a deep pass now
    python -m aggregator.main --full   # deep pass over every windowed patch
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

from aggregator.compute import aggregate_comps, norm_unit
from aggregator.config import AggregatorConfig
from aggregator.storage import AggregatorStorage
from shared.cache import create_cache
from shared.db import create_pool, init_schema

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Watermark metadata keys — ISO timestamps of the last successful runs.
# Slim matches newer than _WATERMARK_KEY are the only ones the incremental
# fold reads; _DEEP_WATERMARK_KEY decides when a deep pass is due.
_WATERMARK_KEY = "agg_watermark"
_DEEP_WATERMARK_KEY = "agg_deep_watermark"


def _patch_sort_key(patch: str) -> tuple[int, int]:
    """Sort key turning ``'17.8'`` into ``(17, 8)`` for numeric ordering."""
    try:
        parts = patch.split(".")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return (0, 0)


def _read_watermark(storage: AggregatorStorage, key: str) -> datetime | None:
    """Return the timestamp stored under a watermark meta key, or ``None``."""
    rec = storage.read_meta(key)
    if not rec or not rec.get("ts"):
        return None
    try:
        return datetime.fromisoformat(rec["ts"])
    except ValueError:
        return None


def build_name_map(patch_data: dict | None) -> dict[str, str]:
    """Build {norm_id: display_name} from patch data stored in PostgreSQL.

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


def _run_deep_pass(
    storage: AggregatorStorage,
    config: AggregatorConfig,
    patch_data: dict | None,
    name_map: dict[str, str],
    summaries: dict[str, dict],
    since: datetime | None,
    until: datetime,
    window: list[str],
) -> int:
    """Recompute full comp stats for windowed patches dirty in ``(since, until]``.

    Reads each dirty patch's complete slim data (up to ``until``) and replaces
    its ``comp_stats`` rows wholesale (superset, mutations, additions, and
    item recommendations included), then prunes slim data outside the
    retention window.

    Patches outside ``window`` are never recomputed, even if dirty: their
    aggregates are frozen, and a handful of straggler matches must not
    replace stats built from the full patch (the source rows are pruned).

    Returns:
        The number of patches recomputed.
    """
    dirty_patches = storage.get_dirty_patches(since, until)
    out_of_window = sorted(set(dirty_patches) - set(window))
    if out_of_window:
        logger.info(
            "Skipping out-of-window straggler patches: %s "
            "(their stats are frozen).",
            ", ".join(out_of_window),
        )
    dirty_patches = sorted(set(dirty_patches) & set(window))

    if not dirty_patches:
        logger.info("Deep pass — no windowed patches with new matches since %s.",
                    since)
        storage.prune_slim_window(window)
        return 0

    logger.info(
        "Deep pass — %d patch(es) to recompute: %s",
        len(dirty_patches), ", ".join(dirty_patches),
    )

    for tft_patch in dirty_patches:
        participants, n_matches = storage.load_participants_for_patch(
            tft_patch, until
        )
        logger.info(
            "Aggregating patch %s — %d participants from %d matches "
            "(threshold=%.0f%%) …",
            tft_patch, len(participants), n_matches,
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
            "matches":      n_matches,
            "participants": len(participants),
            "comps":        written,
        }
        logger.info("Patch %s — %d comps written.", tft_patch, written)

    # Retention: keep only the window patches' slim data. Aggregated
    # comp_stats for pruned patches remain browsable forever.
    storage.prune_slim_window(window)
    return len(dirty_patches)


def main(deep: bool = False, full: bool = False) -> None:
    """Fold new matches into comp stats; run a deep recompute when due.

    Args:
        deep: Force a deep pass (full recompute of dirty patches) now.
        full: Deep pass over every patch in the slim window, ignoring
            watermarks (use after changing aggregation parameters).
    """
    config = AggregatorConfig()

    pool = create_pool(config.database_url)
    init_schema(pool)
    storage = AggregatorStorage(pool)

    try:
        # Patch roster, for display-name mapping and warnings.
        patch_data = storage.read_patch_data()
        name_map   = build_name_map(patch_data)

        # All watermark math uses the DATABASE clock (created_at is assigned
        # by Postgres), bounded above by a lagged cutoff. Every read in this
        # run covers (watermark, until] and the new watermark becomes `until`,
        # so each slim row is counted exactly once even if the collector is
        # writing concurrently.
        until          = storage.watermark_cutoff()
        watermark      = _read_watermark(storage, _WATERMARK_KEY)
        deep_watermark = _read_watermark(storage, _DEEP_WATERMARK_KEY)

        deep_due = (
            deep
            or full
            or deep_watermark is None
            or (datetime.now(timezone.utc) - deep_watermark)
            >= timedelta(days=config.deep_interval_days)
        )

        # Retention window: the newest N patches present in slim data. Both
        # tiers restrict themselves to it — straggler matches for pruned
        # patches must never touch frozen stats.
        window = sorted(
            storage.get_dirty_patches(None, until),
            key=_patch_sort_key,
            reverse=True,
        )[: config.slim_window_patches]

        # Per-patch counts, so the global summary can be assembled from frozen
        # + freshly computed patches without re-scanning the whole table.
        summaries: dict[str, dict] = storage.read_meta("patch_summaries") or {}

        if deep_due:
            # The deep pass covers everything the incremental fold would:
            # dirty patches are replaced wholesale from rows up to `until`.
            since = None if full else deep_watermark
            processed = _run_deep_pass(
                storage, config, patch_data, name_map, summaries,
                since, until, window,
            )
            # Both watermarks advance to the read cutoff together, before the
            # (rebuildable) summaries: a crash after this point costs nothing.
            storage.write_meta_many({
                _DEEP_WATERMARK_KEY: {"ts": until.isoformat()},
                _WATERMARK_KEY:      {"ts": until.isoformat()},
            })
            if processed == 0 and not full:
                return
        else:
            participants, n_matches = storage.load_new_participants(
                watermark, until
            )
            in_window = [p for p in participants if p["tft_patch"] in window]
            skipped = len(participants) - len(in_window)
            if skipped:
                logger.info(
                    "Skipping %d straggler participants for out-of-window "
                    "patches (stats frozen).", skipped,
                )
            if not in_window:
                logger.info(
                    "No new windowed matches in (%s, %s] — nothing to fold.",
                    watermark, until,
                )
                # Advance past stragglers so they aren't re-read every run.
                storage.write_meta_many(
                    {_WATERMARK_KEY: {"ts": until.isoformat()}}
                )
                return

            _warn_unknown_items(in_window, patch_data)
            _warn_unknown_units(in_window, patch_data)

            # The running-sum fold is not idempotent, so the watermark that
            # marks these rows as consumed commits in the same transaction.
            touched = storage.fold_exact_stats(
                in_window, name_map,
                advance_watermark_to=until.isoformat(),
            )
            for p in in_window:
                s = summaries.setdefault(
                    p["tft_patch"], {"matches": 0, "participants": 0, "comps": 0}
                )
                s["participants"] = s.get("participants", 0) + 1
            logger.info(
                "Incremental fold — %d participants from %d new matches "
                "folded into %d comps.",
                len(in_window), n_matches, touched,
            )

        # ── Build summary metadata ─────────────────────────────────────────
        # comp_stats is the source of truth for which patches are browsable;
        # the permanent ledger supplies true per-patch match totals.
        comp_counts  = storage.comp_patch_counts()
        match_counts = storage.processed_match_counts()
        for patch, count in match_counts.items():
            summaries.setdefault(
                patch, {"matches": 0, "participants": 0, "comps": 0}
            )["matches"] = count
        storage.write_meta("patch_summaries", summaries)

        available    = sorted(comp_counts, key=_patch_sort_key, reverse=True)
        latest_patch = available[0] if available else "unknown"

        storage.write_meta(
            "stats_summary",
            {
                "patch":              latest_patch,
                "set_number":         patch_data.get("set_number", 0) if patch_data else 0,
                "total_matches":      sum(match_counts.values()),
                "total_participants": sum(s.get("participants", 0) for s in summaries.values()),
                "total_comps":        sum(comp_counts.values()),
                "last_updated":       datetime.now(timezone.utc).isoformat(),
                "regions":            storage.distinct_regions(),
                "available_patches":  available,
            },
        )

        logger.info(
            "Aggregation complete — %d total comps across %d patches. Latest: %s",
            sum(comp_counts.values()), len(available), latest_patch,
        )

        # New stats are live — flush the API read cache so the next request
        # repopulates it from the freshly indexed PostgreSQL data.
        cache = create_cache(config.redis_url)
        cache.invalidate()
        cache.close()
    finally:
        pool.close()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="HyperTFT stats aggregator")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Force a deep pass now (full recompute of patches with new "
             "matches, plus retention pruning).",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Deep pass over every patch in the slim window, ignoring "
             "watermarks (use after changing aggregation parameters).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(deep=args.deep, full=args.full)
