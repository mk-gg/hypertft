"""
api/routers/meta.py
GET /meta        — current patch info, match counts, last updated
GET /meta/units  — full unit/trait/item roster from latest patch data
"""

from __future__ import annotations

import logging

import psycopg
from fastapi import APIRouter, HTTPException

from api.dependencies import CacheDep, PoolDep
from shared.models import MetaResponse

router = APIRouter(prefix="/meta", tags=["meta"])
logger = logging.getLogger(__name__)


def _read_meta(pool, key: str) -> dict | None:
    """Read a single metadata record by key, or ``None`` if absent."""
    try:
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE meta_key = %s", (key,)
            ).fetchone()
    except psycopg.Error as exc:
        logger.error("Database error: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")
    return row[0] if row else None


@router.get("", response_model=MetaResponse)
def get_meta(pool: PoolDep, cache: CacheDep):
    """Return summary stats about the current dataset."""
    cached = cache.get_json("meta")
    if cached is not None:
        return cached

    item = _read_meta(pool, "stats_summary")
    if not item:
        raise HTTPException(
            status_code=404,
            detail="Stats not yet generated. Run the aggregator first.",
        )

    result = {
        "patch":              item.get("patch", "unknown"),
        "set_number":         int(item.get("set_number", 0)),
        "total_matches":      int(item.get("total_matches", 0)),
        "total_participants": int(item.get("total_participants", 0)),
        "total_comps":        int(item.get("total_comps", 0)),
        "last_updated":       item.get("last_updated", ""),
        "regions":            item.get("regions", []),
        "available_patches":  list(item.get("available_patches", [])),
    }
    cache.set_json("meta", result)
    return result


@router.get("/units")
def get_units(pool: PoolDep, cache: CacheDep):
    """Return the full patch roster (units, traits, items) for the frontend."""
    cached = cache.get_json("units")
    if cached is not None:
        return cached

    info = _read_meta(pool, "patch_data")
    if not info:
        raise HTTPException(
            status_code=404,
            detail="Patch data not yet collected. Run the collector first.",
        )

    result = {
        "set":    int(info.get("set_number", 0)),
        "patch":  str(info.get("patch", "unknown")),
        "units":  info.get("units", []),
        "traits": info.get("traits", []),
        "items":  info.get("items", []),
    }
    cache.set_json("units", result)
    return result
