"""Slim match extraction — the compact per-match shape the pipeline stores.

Raw Riot match payloads are ~20 KB of JSON per match, of which the analytics
pipeline uses well under half. At ingest the collector extracts the fields
below into a "slim" participants array (stored as JSONB in ``match_slim``)
and the raw payload is not retained, keeping storage and egress flat as the
dataset grows.

Slim participant schema (keys kept short deliberately — JSONB repeats keys
per row, and this shape is written tens of thousands of times):

    {
      "pl":  int,          # placement 1-8            ← core stat
      "lvl": int,          # player level at game end
      "u":   [             # units on the final board  ← core stat
        {
          "c": str,        # character_id, e.g. "TFT17_Akali"
          "s": int,        # star tier (1-3)
          "i": [str, ...]  # itemNames (real items only; may be empty)
        }, ...
      ],
      "tr":  [             # active traits (future: trait analysis)
        {
          "n":  str,       # trait name, e.g. "TFT17_Vanguard"
          "nu": int,       # num_units contributing
          "tc": int        # tier_current (0 = inactive style)
        }, ...
      ],
      "aug": [str, ...]    # augments (null when the set has none)
    }

Deliberately discarded (no analytical value for comp stats): companion
cosmetics, gold_left, last_round, missions, players_eliminated, puuid and
riot IDs (PII), time_eliminated, total_damage_to_players, win flag, and all
round-by-round metadata.
"""

from __future__ import annotations

from shared.compute_utils import norm_unit
from shared.constants import is_real_item


def extract_slim_participants(match_data: dict) -> list[dict]:
    """Extract the slim participants array from a raw Riot match payload.

    Args:
        match_data: Full match JSON as returned by the Riot match-v1 API.

    Returns:
        A list of slim participant dicts (see module docstring for the
        schema). Participants without units or a placement are skipped.
    """
    slim: list[dict] = []
    for p in match_data.get("info", {}).get("participants") or []:
        placement = p.get("placement")
        units = [
            {
                "c": u["character_id"],
                "s": int(u.get("tier", 1)),
                "i": [i for i in u.get("itemNames", []) if is_real_item(i)],
            }
            for u in p.get("units", [])
            if u.get("character_id")
        ]
        if placement is None or not units:
            continue
        slim.append(
            {
                "pl":  int(placement),
                "lvl": int(p.get("level", 0)),
                "u":   units,
                "tr":  [
                    {
                        "n":  t.get("name", ""),
                        "nu": int(t.get("num_units", 0)),
                        "tc": int(t.get("tier_current", 0)),
                    }
                    for t in p.get("traits", [])
                    if t.get("name")
                ],
                "aug": p.get("augments"),
            }
        )
    return slim


def slim_to_participants(
    slim_participants: list[dict],
    region: str,
    tft_patch: str,
) -> list[dict]:
    """Convert stored slim participants into aggregation participant dicts.

    Produces the shape :func:`aggregator.compute.aggregate_comps` consumes:
    ``{units, placement, region, tft_patch, items_by_unit}`` with unit names
    normalised (``TFT17_Akali`` → ``akali``).

    Args:
        slim_participants: The ``participants`` JSONB array of one
            ``match_slim`` row.
        region: The row's collection region (e.g. ``na1``).
        tft_patch: The row's TFT display patch (e.g. ``17.6``).

    Returns:
        One participant dict per board, ready for aggregation.
    """
    out: list[dict] = []
    for p in slim_participants:
        units: list[str] = []
        items_by_unit: dict[str, list[str]] = {}
        for u in p.get("u", []):
            name = norm_unit(u.get("c", ""))
            if not name:
                continue
            units.append(name)
            if u.get("i"):
                items_by_unit[name] = u["i"]
        if not units:
            continue
        out.append(
            {
                "units":         units,
                "placement":     int(p.get("pl", 0)),
                "region":        region,
                "tft_patch":     tft_patch,
                "items_by_unit": items_by_unit,
            }
        )
    return out
