"""
shared/models.py
Pydantic models used across collector, aggregator, and API.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# ── Patch / unit data ──────────────────────────────────────────────────────

class UnitModel(BaseModel):
    id: str
    name: str
    cost: int
    traits: list[str] = Field(default_factory=list)
    icon: str = ""

    @field_validator("icon", "name", "id", mode="before")
    @classmethod
    def coerce_none_to_str(cls, v: object) -> str:
        return v if isinstance(v, str) else ""


class TraitModel(BaseModel):
    id: str
    name: str
    icon: str = ""

    @field_validator("icon", "name", "id", mode="before")
    @classmethod
    def coerce_none_to_str(cls, v: object) -> str:
        return v if isinstance(v, str) else ""


class ItemModel(BaseModel):
    id: str
    name: str
    icon: str = ""

    @field_validator("icon", "name", "id", mode="before")
    @classmethod
    def coerce_none_to_str(cls, v: object) -> str:
        return v if isinstance(v, str) else ""


class PatchData(BaseModel):
    set_number: int = Field(alias="set")
    patch: str
    units: list[UnitModel] = Field(default_factory=list)
    traits: list[TraitModel] = Field(default_factory=list)
    items: list[ItemModel] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


# ── Placement stats ────────────────────────────────────────────────────────

class PlacementStats(BaseModel):
    avg: float
    n: int


# ── Mutation / Addition with delta ─────────────────────────────────────────
#
# delta = entry.avg - reference_avg  (superset avg of the current board)
#
# Negative delta → better placement than current board  (good, show green)
# Positive delta → worse  placement than current board  (bad,  show red)
# Zero           → no change
#

class MutationEntry(BaseModel):
    unit_out: str
    unit_in:  str
    avg:      float                    # avg placement with this swap applied
    delta:    float                    # avg - superset_avg of current board
    n:        int                      # how many times this swap was observed


class AdditionEntry(BaseModel):
    unit:  str
    avg:   float                       # avg placement with this unit added
    delta: float                       # avg - superset_avg of current board
    n:     int


# ── Item recommendations ──────────────────────────────────────────────────

class ItemStat(BaseModel):
    """A single item's performance stats on a specific unit."""
    item:  str     # CDragon apiName e.g. "TFT_Item_RabadonsDeathcap"
    avg:   float   # avg placement when this item is on this unit
    delta: float   # avg - reference_avg (negative = improvement)
    n:     int     # how many times observed


class UnitItemRec(BaseModel):
    """Item recommendations for one unit on the board."""
    unit:  str            # display name e.g. "Ahri"
    items: list[ItemStat] # sorted best → worst by avg placement


# ── Existing comp stats (POST /comp) ──────────────────────────────────────

class CompStats(BaseModel):
    units: list[str]
    exact: PlacementStats
    superset: PlacementStats
    mutations: list[MutationEntry] = Field(default_factory=list)
    additions: list[AdditionEntry] = Field(default_factory=list)


class CompRequest(BaseModel):
    units: list[str] = Field(..., min_length=1, max_length=10)
    similarity_threshold: float = Field(default=0.60, ge=0.0, le=1.0)


class CompResponse(BaseModel):
    units:    list[str]
    exact:    PlacementStats
    superset: PlacementStats
    mutations:   list[MutationEntry]
    additions:   list[AdditionEntry]
    exact_items: list[UnitItemRec] = []   # items from exact comp matches
    super_items: list[UnitItemRec] = []   # items from superset matches


# ── Suggest endpoint models (POST /comp/suggest) ──────────────────────────

class SuggestRequest(BaseModel):
    units: list[str] = Field(
        ..., min_length=1, max_length=10,
        description="Current board — can be partial (1–10 units)",
    )
    similarity_threshold: float | None = Field(
        default=None,
        ge=0.0, le=1.0,
        description=(
            "Jaccard threshold. If omitted, auto-scales by board size: "
            "1 unit=0.20, 2=0.30, 3=0.40, 4-5=0.50, 6+=0.60"
        ),
    )
    limit: int = Field(default=6, ge=1, le=20)


class SuggestedComp(BaseModel):
    units:     list[str]    # full comp units
    missing:   list[str]    # units not yet on the board
    exact_avg: float
    exact_n:   int
    similarity: float       # Jaccard score vs current board


class SuggestResponse(BaseModel):
    board:             list[str]
    patch:             str
    threshold_used:    float
    superset_avg:      float | None
    superset_n:        int
    suggested_comps:   list[SuggestedComp]
    additions:         list[AdditionEntry]
    mutations:         list[MutationEntry]
    exact_items:       list[UnitItemRec] = []   # items from exact comp matches
    super_items:       list[UnitItemRec] = []   # items from superset matches


# ── Meta ───────────────────────────────────────────────────────────────────

class MetaResponse(BaseModel):
    patch: str
    set_number: int
    total_matches: int
    total_participants: int
    total_comps: int
    last_updated: str
    regions: list[str]
    available_patches: list[str] = []
