"""
aggregator/compute.py
Pure computation — no I/O.  Takes a list of participant dicts,
returns aggregated comp stats ready for PostgreSQL.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from shared.patch_map import resolve_tft_patch


def norm_unit(raw: str) -> str:
    """'TFT17_Akali' or 'TFT_Unit_Akali' → 'akali'"""
    return re.sub(r"^TFT\w+_", "", raw, flags=re.IGNORECASE).lower()


def comp_key(units: list[str]) -> str:
    """Stable sorted key: 'ahri|akali|amumu'"""
    return "|".join(sorted(units))


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def safe_avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def extract_participants(raw_matches: list[dict]) -> list[dict]:
    """
    Flatten raw match JSONs into a list of participant dicts.
    Each dict: { units: [str], placement: int, region: str, tft_patch: str }
    """
    participants = []
    for match in raw_matches:
        region = match.get("region", "unknown")

        # Resolve TFT patch from stored field first, then parse game_version
        tft_patch = match.get("tft_patch") or resolve_tft_patch(
            match.get("info", {}).get("game_version", "")
            or match.get("game_version", "")
        )

        pp = (
            match.get("info", {}).get("participants")
            or match.get("participants")
            or []
        )
        for p in pp:
            raw_units = p.get("units", [])
            units = [
                norm_unit(u["character_id"])
                for u in raw_units
                if u.get("character_id")
            ]
            placement = p.get("placement")
            if units and placement is not None:
                # items_by_unit: { norm_unit_name: [item_api_name, ...] }
                items_by_unit: dict[str, list[str]] = {}
                for u in raw_units:
                    cid = u.get("character_id")
                    if not cid:
                        continue
                    norm_name = norm_unit(cid)
                    item_names = [
                        i for i in u.get("itemNames", [])
                        if i  # filter empty strings
                    ]
                    if item_names:
                        items_by_unit[norm_name] = item_names

                participants.append(
                    {
                        "units":         units,
                        "placement":     int(placement),
                        "region":        region,
                        "tft_patch":     tft_patch,
                        "items_by_unit": items_by_unit,
                    }
                )
    return participants


def aggregate_comps(
    participants: list[dict],
    name_map: dict[str, str],
    super_threshold: float,
    min_n_comp: int,
    min_n_mutation: int,
    min_n_addition: int,
    top_mutations: int,
    top_additions: int,
) -> list[dict]:
    """
    Aggregate participants into comp stats including per-unit item recommendations.
    Returns a list of comp dicts ready to write to PostgreSQL.
    """
    # ── 1. Bucket exact comps ──────────────────────────────────────────────
    exact_buckets:   dict[str, list[int]] = defaultdict(list)
    exact_unit_sets: dict[str, list[str]] = {}

    # item_buckets[comp_key][norm_unit][item_name] → [placements]
    item_buckets: dict[str, dict[str, dict[str, list[int]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for p in participants:
        key = comp_key(p["units"])
        pl  = p["placement"]
        exact_buckets[key].append(pl)
        exact_unit_sets[key] = sorted(p["units"])

        # Track item → placement for each unit in this exact comp
        for norm_name, items in p.get("items_by_unit", {}).items():
            for item in items:
                item_buckets[key][norm_name][item].append(pl)

    qualifying = {
        k for k, v in exact_buckets.items()
        if len(v) >= min_n_comp
    }

    # ── 2. Compute superset + mutations + additions + items ────────────────
    comps_out: list[dict] = []

    for key in qualifying:
        sel_set  = set(exact_unit_sets[key])
        exact_pl = exact_buckets[key]

        super_placements: list[float] = []
        swap_map: dict[str, list[int]] = defaultdict(list)
        add_map:  dict[str, list[int]] = defaultdict(list)

        # Superset item tracking: unit → item → [placements]
        super_item_buckets: dict[str, dict[str, list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )

        for p in participants:
            p_set = set(p["units"])
            if p_set == sel_set:
                continue
            j = jaccard(sel_set, p_set)
            if j < super_threshold:
                continue

            pl = p["placement"]
            super_placements.append(pl)

            removed = sel_set - p_set
            added   = p_set - sel_set

            if len(removed) == 1 and len(added) == 1:
                out_u = list(removed)[0]
                in_u  = list(added)[0]
                swap_map[f"{out_u}|||{in_u}"].append(pl)

            for u in added:
                add_map[u].append(pl)

            # Collect superset item data for units shared with our comp
            for norm_name, items in p.get("items_by_unit", {}).items():
                if norm_name in sel_set:  # only units in our comp
                    for item in items:
                        super_item_buckets[norm_name][item].append(pl)

        # ── Build mutations ────────────────────────────────────────────────
        mutations = []
        for sk, places in swap_map.items():
            if len(places) < min_n_mutation:
                continue
            out_u, in_u = sk.split("|||")
            mutations.append({
                "unit_out": name_map.get(out_u, out_u),
                "unit_in":  name_map.get(in_u, in_u),
                "avg":      round(safe_avg(places), 4),
                "n":        len(places),
            })
        mutations.sort(key=lambda x: x["avg"])
        mutations = mutations[:top_mutations]

        # ── Build additions ────────────────────────────────────────────────
        additions = []
        for u, places in add_map.items():
            if len(places) < min_n_addition:
                continue
            additions.append({
                "unit": name_map.get(u, u),
                "avg":  round(safe_avg(places), 4),
                "n":    len(places),
            })
        additions.sort(key=lambda x: x["avg"])
        additions = additions[:top_additions]

        # ── Build per-unit item recommendations ───────────────────────────
        # exact_items: items seen on each unit in exact comp matches
        # super_items: items seen on each unit in superset matches
        MIN_ITEM_N = 2
        TOP_ITEMS  = 4  # max items shown per unit

        def build_unit_items(
            bucket: dict[str, dict[str, list[int]]],
            unit_set: set[str],
        ) -> list[dict]:
            """
            For each unit in the comp, find the top-performing items
            sorted by avg placement.
            """
            result = []
            for norm_name in unit_set:
                display_name = name_map.get(norm_name, norm_name)
                item_stats   = []
                for item_api, places in bucket.get(norm_name, {}).items():
                    if len(places) < MIN_ITEM_N:
                        continue
                    item_stats.append({
                        "item": item_api,
                        "avg":  round(safe_avg(places), 4),
                        "n":    len(places),
                    })
                item_stats.sort(key=lambda x: x["avg"])
                result.append({
                    "unit":  display_name,
                    "items": item_stats[:TOP_ITEMS],
                })
            return result

        exact_items = build_unit_items(item_buckets[key], sel_set)
        super_items = build_unit_items(super_item_buckets,  sel_set)

        exact_places = exact_buckets[key]
        comps_out.append({
            "comp_key":    key,
            "units":       [name_map.get(u, u) for u in exact_unit_sets[key]],
            "exact_avg":   round(safe_avg(exact_places), 4),
            "exact_n":     len(exact_places),
            "super_avg":   round(safe_avg(super_placements), 4),
            "super_n":     len(super_placements),
            "mutations":   mutations,
            "additions":   additions,
            "exact_items": exact_items,   # item recs from exact comp matches
            "super_items": super_items,   # item recs from superset matches
        })

    return comps_out

