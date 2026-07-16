"""POST /comp          — stats for a given unit list on a specific patch.

POST /comp/suggest  — suggestions for a partial/full board
GET  /comp/top      — top comps ranked by avg placement
GET  /comp/patches  — list of all patches that have aggregated data
"""

from __future__ import annotations

import logging

import psycopg
from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from api.dependencies import CacheDep, PoolDep
from shared.comp_engine import analyse, auto_threshold, build_name_map
from shared.models import (
    AdditionEntry,
    CompRequest,
    CompResponse,
    ItemStat,
    MutationEntry,
    PlacementStats,
    SuggestRequest,
    SuggestResponse,
    UnitItemRec,
)

router = APIRouter(prefix="/comp", tags=["comp"])
logger = logging.getLogger(__name__)

# Columns selected when a full comp record is needed (POST /comp, /comp/suggest).
_COMP_COLUMNS = (
    "units, exact_avg, exact_n, super_avg, super_n, "
    "mutations, additions, exact_items, super_items"
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _comp_key(units: list[str]) -> str:
    """Build the stable lookup key for a unit list: ``ahri|akali|amumu``."""
    return "|".join(sorted(u.lower() for u in units))


def _norm_units(units: list[str]) -> list[str]:
    """Lowercase + strip a unit list for matching against ``units_norm``."""
    return [u.strip().lower() for u in units]


def _patch_sort_key(patch: str) -> tuple[int, int]:
    """Sort key turning ``'17.8'`` into ``(17, 8)`` for numeric ordering."""
    try:
        parts = patch.split(".")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return (0, 0)


def _read_stats_summary(pool: ConnectionPool) -> dict:
    """Return the ``stats_summary`` metadata record, or an empty dict."""
    try:
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE meta_key = %s", ("stats_summary",)
            ).fetchone()
    except psycopg.Error as exc:
        logger.error("Database error reading stats_summary: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")
    return row[0] if row else {}


def _resolve_patch(requested: str | None, pool: ConnectionPool) -> str:
    """Return the patch to query.

    Uses ``?patch=`` if supplied, otherwise the newest patch in
    ``available_patches`` (sorted numerically — CDragon's patchLine is
    unreliable, so it is never trusted).
    """
    if requested:
        return requested.strip()
    summary = _read_stats_summary(pool)
    patches = sorted(
        summary.get("available_patches", []),
        key=_patch_sort_key,
        reverse=True,
    )
    return patches[0] if patches else "unknown"


def _patch_has_data(pool: ConnectionPool, patch: str) -> bool:
    """Return whether any comp stats exist for the given patch."""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM comp_stats WHERE patch = %s LIMIT 1", (patch,)
        ).fetchone()
    return row is not None


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/patches")
def list_patches(pool: PoolDep, cache: CacheDep) -> dict:
    """Return all patches that have aggregated stats, newest first."""
    cached = cache.get_json("patches")
    if cached is not None:
        return cached

    summary = _read_stats_summary(pool)
    patches = sorted(
        summary.get("available_patches", []),
        key=_patch_sort_key,
        reverse=True,
    )
    latest = patches[0] if patches else str(summary.get("patch", "unknown"))
    result = {"latest": latest, "patches": patches}
    cache.set_json("patches", result)
    return result


@router.post("", response_model=CompResponse)
def get_comp_stats(
    body: CompRequest,
    pool: PoolDep,
    patch: str | None = Query(
        default=None,
        description="TFT patch to query (e.g. '17.8'). Defaults to latest.",
    ),
):
    """Return stats for an exact unit list on a specific patch.

    Omit ``?patch`` for the latest patch; pass ``?patch=17.8`` for history.
    """
    resolved_patch = _resolve_patch(patch, pool)
    key            = _comp_key(body.units)

    try:
        with pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                f"SELECT {_COMP_COLUMNS} FROM comp_stats "
                "WHERE patch = %s AND comp_key = %s",
                (resolved_patch, key),
            )
            item = cur.fetchone()
    except psycopg.Error as exc:
        logger.error("Database error: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not item:
        raise HTTPException(
            status_code=404,
            detail={
                "error":    "comp not found",
                "patch":    resolved_patch,
                "comp_key": key,
                "hint":     "Try a different patch with ?patch=17.8 or check /comp/patches",
            },
        )

    super_avg = float(item.get("super_avg") or 0.0)
    exact_avg = float(item.get("exact_avg") or 0.0)

    def build_item_recs(raw: list[dict], reference_avg: float) -> list[UnitItemRec]:
        recs = []
        for unit_rec in raw:
            items = [
                ItemStat(
                    item  = s["item"],
                    avg   = float(s["avg"]),
                    delta = round(float(s["avg"]) - reference_avg, 4),
                    n     = int(s["n"]),
                )
                for s in unit_rec.get("items", [])
            ]
            recs.append(UnitItemRec(unit=unit_rec["unit"], items=items))
        return recs

    return CompResponse(
        units=item.get("units", body.units),
        exact=PlacementStats(avg=exact_avg, n=item.get("exact_n", 0)),
        superset=PlacementStats(avg=super_avg, n=item.get("super_n", 0)),
        mutations=[
            MutationEntry(
                unit_out = m["unit_out"],
                unit_in  = m["unit_in"],
                avg      = float(m["avg"]),
                delta    = round(float(m["avg"]) - super_avg, 4),
                n        = int(m["n"]),
            )
            for m in item.get("mutations", [])
        ],
        additions=[
            AdditionEntry(
                unit  = a["unit"],
                avg   = float(a["avg"]),
                delta = round(float(a["avg"]) - super_avg, 4),
                n     = int(a["n"]),
            )
            for a in item.get("additions", [])
        ],
        # Items: delta vs exact avg for exact_items, vs super avg for super_items.
        exact_items = build_item_recs(item.get("exact_items", []), exact_avg),
        super_items = build_item_recs(item.get("super_items", []), super_avg),
    )


@router.get("/top")
def get_top_comps(
    pool: PoolDep,
    cache: CacheDep,
    patch: str | None = Query(
        default=None,
        description="TFT patch (e.g. '17.8'). Defaults to latest.",
    ),
    limit:     int        = Query(default=20, ge=1, le=200),
    min_n:     int        = Query(default=5,  ge=1),
    team_size: int | None = Query(default=None, ge=1, le=10),
):
    """Return the top N comps for a patch, ranked by avg placement.

    The ``min_n``, ``team_size``, ordering, and ``limit`` filters are all
    pushed into SQL so only the rows that matter cross the wire. The full
    response is cached (cache-aside) keyed by the query parameters; the cache
    is flushed whenever the aggregator writes new stats.
    """
    # Key on the requested patch param (not the resolved one) so the default
    # "latest" view recomputes after an invalidation surfaces a new patch.
    cache_key = f"tierlist:{patch or '_latest'}:{limit}:{min_n}:{team_size}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached

    resolved_patch = _resolve_patch(patch, pool)

    try:
        with pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            cur.execute(
                """
                SELECT units, exact_avg, exact_n, super_avg, super_n
                FROM comp_stats
                WHERE patch = %s
                  AND exact_n >= %s
                  AND (%s::int IS NULL OR cardinality(units_norm) = %s)
                ORDER BY exact_avg ASC
                LIMIT %s
                """,
                (resolved_patch, min_n, team_size, team_size, limit),
            )
            comps = cur.fetchall()
    except psycopg.Error as exc:
        logger.error("Database error: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    # Distinguish "patch has no data at all" (404) from "filters excluded
    # everything" (200 with an empty list), matching the prior behaviour.
    if not comps and not _patch_has_data(pool, resolved_patch):
        raise HTTPException(
            status_code=404,
            detail=f"No stats found for patch '{resolved_patch}'. "
                   f"Available patches: GET /comp/patches",
        )

    result = {
        "patch": resolved_patch,
        "total": len(comps),
        "comps": [
            {
                "units":     c["units"],
                "exact_avg": c["exact_avg"],
                "exact_n":   c["exact_n"],
                "super_avg": c.get("super_avg", 0.0),
                "super_n":   c.get("super_n", 0),
            }
            for c in comps
        ],
    }
    cache.set_json(cache_key, result)
    return result


@router.post("/suggest", response_model=SuggestResponse)
def suggest_comp(
    body: SuggestRequest,
    pool: PoolDep,
    patch: str | None = Query(
        default=None,
        description="TFT patch (e.g. 17.8). Defaults to latest.",
    ),
):
    """Smart suggest endpoint for any board size (1–10 units).

    Returns suggested_comps, additions (with delta), and mutations (with delta).
    Delta = entry.avg − superset_avg of the current board (negative = better).

    Only comps that share at least one unit with the board can clear any
    positive Jaccard threshold, so the candidate set is narrowed with a
    GIN-accelerated array-overlap query (``units_norm && board``) before the
    exact similarity is computed in :func:`shared.comp_engine.analyse`.
    """
    resolved_patch = _resolve_patch(patch, pool)
    board_norm     = _norm_units(body.units)

    try:
        with pool.connection() as conn:
            cur = conn.cursor(row_factory=dict_row)
            # exact_n >= 3 mirrors the aggregator's MIN_N_COMP: the incremental
            # fold stores comps below that threshold (running counts need them)
            # but they are too noisy to drive suggestions.
            cur.execute(
                f"SELECT {_COMP_COLUMNS} FROM comp_stats "
                "WHERE patch = %s AND units_norm && %s AND exact_n >= 3",
                (resolved_patch, board_norm),
            )
            comps = cur.fetchall()
    except psycopg.Error as exc:
        logger.error("Database error: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not comps and not _patch_has_data(pool, resolved_patch):
        raise HTTPException(
            status_code=404,
            detail=f"No stats for patch {resolved_patch!r}. See GET /comp/patches.",
        )

    # Display-name map for converting normalised unit keys back to display names.
    name_map: dict[str, str] = {}
    try:
        with pool.connection() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE meta_key = %s", ("patch_data",)
            ).fetchone()
        if row:
            name_map = build_name_map(row[0].get("units", []))
    except psycopg.Error:
        name_map = {}

    threshold = (
        body.similarity_threshold
        if body.similarity_threshold is not None
        else auto_threshold(len(body.units))
    )

    return analyse(
        board     = body.units,
        all_comps = comps,
        threshold = threshold,
        limit     = body.limit,
        patch     = resolved_patch,
        name_map  = name_map,
    )
