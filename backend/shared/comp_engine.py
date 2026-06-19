"""
shared/comp_engine.py
Pure-Python comp analysis engine used by the suggest endpoint.

No I/O — takes a list of comp dicts (from PostgreSQL or any source)
and a board (list of unit names) and returns structured suggestions.
"""

from __future__ import annotations

import re
from collections import defaultdict

from shared.models import (
    AdditionEntry,
    ItemStat,
    MutationEntry,
    SuggestedComp,
    SuggestResponse,
    UnitItemRec,
)


# ── Similarity ─────────────────────────────────────────────────────────────

def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    intersection = len(a & b)
    return intersection / len(a | b)


def auto_threshold(board_size: int) -> float:
    """
    Auto-scale Jaccard threshold based on how many units are selected.
    Smaller boards need a looser threshold to find meaningful matches.
    """
    if board_size <= 1: return 0.20
    if board_size <= 2: return 0.30
    if board_size <= 3: return 0.40
    if board_size <= 5: return 0.50
    return 0.60


# ── Name helpers ───────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    """Lowercase + strip whitespace for case-insensitive comparison."""
    return name.strip().lower()


def _resolve_display(lower: str, name_map: dict[str, str]) -> str:
    """
    Convert a lowercase unit key back to display name.
    Falls back to title-case if not in the map.
    """
    return name_map.get(lower, lower.replace("-", " ").title())


def build_name_map(units: list[dict]) -> dict[str, str]:
    """
    Build {lowercase_name: display_name} from a unit list.
    e.g. {"ahri": "Ahri", "leblanc": "LeBlanc"}
    """
    return {_norm(u["name"]): u["name"] for u in units if u.get("name")}


# ── Core engine ────────────────────────────────────────────────────────────

def _build_item_recs(
    board_set:     set[str],
    similar_comps: list[dict],
    superset_avg:  float | None,
    name_map:      dict[str, str],
    source_key:    str,           # "exact_items" or "super_items"
    min_n:         int = 2,
    top_items:     int = 4,
) -> list[UnitItemRec]:
    """
    Aggregate item stats across similar comps for each unit on the board.
    Returns UnitItemRec list with ItemStat entries sorted by avg placement.

    source_key controls which pre-aggregated item bucket to read from:
      "exact_items" → items from exact comp matches (aggregator computed)
      "super_items" → items from superset matches
    """
    # unit_item_map[norm_unit][item_api] → [avg_placements]
    unit_item_map: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for entry in similar_comps:
        comp     = entry["comp"]
        item_recs: list[dict] = comp.get(source_key, [])
        for unit_rec in item_recs:
            unit_norm = _norm(unit_rec.get("unit", ""))
            if unit_norm not in board_set:
                continue  # only units on the current board
            for item_stat in unit_rec.get("items", []):
                item_api = item_stat.get("item", "")
                item_avg = float(item_stat.get("avg", 0))
                if item_api and item_avg:
                    unit_item_map[unit_norm][item_api].append(item_avg)

    result: list[UnitItemRec] = []
    for norm_unit in board_set:
        display = _resolve_display(norm_unit, name_map)
        items_data = unit_item_map.get(norm_unit, {})
        item_stats: list[ItemStat] = []

        for item_api, avgs in items_data.items():
            if len(avgs) < min_n:
                continue
            entry_avg = sum(avgs) / len(avgs)
            delta     = round(entry_avg - superset_avg, 4) if superset_avg else 0.0
            item_stats.append(
                ItemStat(
                    item  = item_api,
                    avg   = round(entry_avg, 4),
                    delta = delta,
                    n     = len(avgs),
                )
            )

        item_stats.sort(key=lambda x: x.avg)
        result.append(
            UnitItemRec(
                unit  = display,
                items = item_stats[:top_items],
            )
        )

    # Sort by unit name for consistent ordering
    result.sort(key=lambda x: x.unit)
    return result


def analyse(
    board:      list[str],
    all_comps:  list[dict],
    threshold:  float,
    limit:      int,
    patch:      str,
    name_map:   dict[str, str],
) -> SuggestResponse:
    """
    Given a board (partial or full) and all aggregated comp stats for a patch,
    return:
      - suggested_comps : best comps that contain the board's units
      - additions       : units to add (with avg placement + delta)
      - mutations       : unit swaps (with avg placement + delta)

    Delta is computed relative to the superset average of all similar comps,
    so the frontend can show "this swap improves your avg by -0.4 placements".
    """
    board_set = {_norm(u) for u in board}

    # ── 1. Scan all comps, bucket by similarity ───────────────────────────
    similar_comps:  list[dict]  = []
    super_total     = 0.0
    super_n         = 0

    # Live aggregation maps for additions and mutations
    # keyed by display name
    add_map:  dict[str, list[float]] = defaultdict(list)
    swap_map: dict[str, dict]        = {}   # "Out|||In" → {out, in, placements}

    for comp in all_comps:
        comp_units = comp.get("units", [])
        comp_set   = {_norm(u) for u in comp_units}
        j          = jaccard(board_set, comp_set)

        if j < threshold:
            continue

        exact_avg = float(comp.get("exact_avg", 0))
        exact_n   = int(comp.get("exact_n", 0))

        # Superset bucket — all similar comps (including exact)
        super_total += exact_avg * exact_n
        super_n     += exact_n

        # Collect for suggested comps list
        missing = [
            _resolve_display(_norm(u), name_map)
            for u in comp_units
            if _norm(u) not in board_set
        ]
        similar_comps.append({
            "comp":       comp,
            "units":      comp_units,
            "missing":    missing,
            "exact_avg":  exact_avg,
            "exact_n":    exact_n,
            "similarity": round(j, 3),
        })

        # Skip exact matches for mutation/addition analysis
        if j == 1.0:
            continue

        removed = board_set - comp_set     # on board, not in comp
        added   = comp_set - board_set     # in comp, not on board

        # Additions — units in this comp that aren't on the board
        for u in added:
            display = _resolve_display(u, name_map)
            add_map[display].append(exact_avg)

        # Mutations — 1-for-1 swaps relative to the current board
        # Only surface swaps where the out-unit IS on the board
        for out_u in removed:
            for in_u in added:
                out_d = _resolve_display(out_u, name_map)
                in_d  = _resolve_display(in_u,  name_map)
                key   = f"{out_d}|||{in_d}"
                if key not in swap_map:
                    swap_map[key] = {
                        "out":        out_d,
                        "in":         in_d,
                        "placements": [],
                    }
                swap_map[key]["placements"].append(exact_avg)

    # ── 2. Compute superset reference avg ─────────────────────────────────
    superset_avg: float | None = (super_total / super_n) if super_n else None

    # ── 3. Build suggested comps — sorted by avg placement ────────────────
    similar_comps.sort(key=lambda x: x["exact_avg"])
    suggested = [
        SuggestedComp(
            units      = c["units"],
            missing    = c["missing"],
            exact_avg  = round(c["exact_avg"], 4),
            exact_n    = c["exact_n"],
            similarity = c["similarity"],
        )
        for c in similar_comps[:limit]
    ]

    # ── 4. Build additions with delta ─────────────────────────────────────
    MIN_N = 2
    additions: list[AdditionEntry] = []
    for unit, placements in add_map.items():
        if len(placements) < MIN_N:
            continue
        # Skip units already on the board
        if _norm(unit) in board_set:
            continue
        entry_avg = sum(placements) / len(placements)
        delta     = round(entry_avg - superset_avg, 4) if superset_avg is not None else 0.0
        additions.append(
            AdditionEntry(
                unit  = unit,
                avg   = round(entry_avg, 4),
                delta = delta,
                n     = len(placements),
            )
        )
    # Sort by avg placement (ascending — lower is better in TFT)
    additions.sort(key=lambda x: x.avg)
    additions = additions[:8]

    # ── 5. Build mutations with delta ─────────────────────────────────────
    mutations: list[MutationEntry] = []
    for swap in swap_map.values():
        pls = swap["placements"]
        if len(pls) < MIN_N:
            continue
        # Only valid if out-unit is actually on the board
        if _norm(swap["out"]) not in board_set:
            continue
        # Skip if in-unit is already on the board
        if _norm(swap["in"]) in board_set:
            continue
        entry_avg = sum(pls) / len(pls)
        delta     = round(entry_avg - superset_avg, 4) if superset_avg is not None else 0.0
        mutations.append(
            MutationEntry(
                unit_out = swap["out"],
                unit_in  = swap["in"],
                avg      = round(entry_avg, 4),
                delta    = delta,
                n        = len(pls),
            )
        )
    mutations.sort(key=lambda x: x.avg)
    mutations = mutations[:8]

    # ── 6. Build item recommendations ────────────────────────────────────
    exact_items = _build_item_recs(
        board_set     = board_set,
        similar_comps = similar_comps,
        superset_avg  = superset_avg,
        name_map      = name_map,
        source_key    = "exact_items",
    )
    super_items = _build_item_recs(
        board_set     = board_set,
        similar_comps = similar_comps,
        superset_avg  = superset_avg,
        name_map      = name_map,
        source_key    = "super_items",
    )

    return SuggestResponse(
        board           = board,
        patch           = patch,
        threshold_used  = threshold,
        superset_avg    = round(superset_avg, 4) if superset_avg is not None else None,
        superset_n      = super_n,
        suggested_comps = suggested,
        additions       = additions,
        mutations       = mutations,
        exact_items     = exact_items,
        super_items     = super_items,
    )
