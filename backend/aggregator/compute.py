"""Pure aggregation math — no I/O.

Takes a list of participant dicts and returns aggregated comp stats ready to
write to PostgreSQL.
"""

from __future__ import annotations

from collections import defaultdict

from shared.compute_utils import comp_key, norm_unit  # noqa: F401  (re-export)


def jaccard(a: set, b: set) -> float:
    """Return the Jaccard similarity of two sets (1.0 for two empty sets)."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def safe_avg(values: list[float]) -> float:
    """Return the mean of ``values``, or 0.0 for an empty list."""
    return sum(values) / len(values) if values else 0.0


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
    """Aggregate participants into comp stats including per-unit item recommendations.

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
        sel_set = set(exact_unit_sets[key])

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
        TOP_ITEMS  = 10  # max items stored per unit (API trims to its own top-N)

        def build_unit_items(
            bucket: dict[str, dict[str, list[int]]],
            unit_set: set[str],
        ) -> list[dict]:
            """Find each comp unit's top items, sorted by avg placement."""
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
            "exact_sum":   float(sum(exact_places)),
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

