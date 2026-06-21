"""
shared/constants.py
Constants shared across the collector, aggregator, and API.
"""

from __future__ import annotations

# TFT Ranked queue ID.
#
# IMPORTANT: the Riot `tft/match/v1/matches/by-puuid/{puuid}/ids` endpoint does
# NOT support a `queue` query parameter (it silently ignores it), so the match
# history for a ranked player still includes Normal, Hyper Roll, and Double Up
# games. Matches must therefore be filtered by this value, read from each
# match's `info.queue_id`, at ingest (collector) and when aggregating stats.
RANKED_QUEUE_ID: int = 1100


def is_ranked(match_data: dict) -> bool:
    """Return True if a raw TFT match payload is a Ranked (standard) game."""
    return match_data.get("info", {}).get("queue_id") == RANKED_QUEUE_ID
