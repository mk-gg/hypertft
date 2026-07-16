"""All configuration loaded from environment variables via pydantic-settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

# Platform → regional routing map
PLATFORM_TO_REGIONAL: dict[str, str] = {
    "na1":  "americas",
    "br1":  "americas",
    "la1":  "americas",
    "la2":  "americas",
    "euw1": "europe",
    "eun1": "europe",
    "tr1":  "europe",
    "ru":   "europe",
    "kr":   "asia",
    "jp1":  "asia",
    "oc1":  "sea",
    "sg2":  "sea",
    "tw2":  "sea",
    "vn2":  "sea",
}

ALL_PLATFORMS: list[str] = list(PLATFORM_TO_REGIONAL.keys())

APEX_TIERS: frozenset[str] = frozenset({"CHALLENGER", "GRANDMASTER", "MASTER"})

# ── Tier groups with per-group PUUID quotas ────────────────────────────────
#
# Without quotas, Challenger (250) + Grandmaster (500) + Master (4000) fills
# the whole budget before touching Diamond or Platinum at all.
#
# Each group entry: (quota, [(tier, division), ...])
# quota = max PUUIDs to collect from that entire group.
#
# Default total: 50+50+50+75+75+75+75+75+75 = 600 PUUIDs
# Adjust quotas in .env via SEED_QUOTA_* variables.
#
TIER_GROUPS: list[dict] = [
    {
        "name":   "Challenger",
        "quota":  50,
        "tiers":  [("CHALLENGER",  None)],
    },
    {
        "name":   "Grandmaster",
        "quota":  50,
        "tiers":  [("GRANDMASTER", None)],
    },
    {
        "name":   "Master",
        "quota":  50,
        "tiers":  [("MASTER",      None)],
    },
    {
        "name":   "Diamond",
        "quota":  75,
        "tiers":  [
            ("DIAMOND", "I"),
            ("DIAMOND", "II"),
            ("DIAMOND", "III"),
            ("DIAMOND", "IV"),
        ],
    },
    {
        "name":   "Emerald",
        "quota":  75,
        "tiers":  [
            ("EMERALD", "I"),
            ("EMERALD", "II"),
            ("EMERALD", "III"),
            ("EMERALD", "IV"),
        ],
    },
    {
        "name":   "Platinum I-II",
        "quota":  75,
        "tiers":  [
            ("PLATINUM", "I"),
            ("PLATINUM", "II"),
        ],
    },
    {
        "name":   "Platinum III-IV",
        "quota":  75,
        "tiers":  [
            ("PLATINUM", "III"),
            ("PLATINUM", "IV"),
        ],
    },
]

# Single source of truth lives in shared/constants.py; re-exported here so the
# collector's existing imports keep working.
from shared.constants import RANKED_QUEUE_ID  # noqa: E402,F401

CDRAGON_URL: str = "https://raw.communitydragon.org/latest/cdragon/tft/en_us.json"


class CollectorConfig(BaseSettings):
    """Loaded from .env or environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Riot
    riot_api_key: str

    # PostgreSQL connection string, e.g.
    # postgresql://user:password@host:5432/dbname
    database_url: str

    # Redis connection string (optional). The cache is flushed after each run
    # so the refreshed patch roster is served on the next read. No-op if unset.
    redis_url: str | None = None

    # Rate limits
    rate_limit_per_second: int = 18
    rate_limit_per_2min: int   = 95

    # Collection
    target_matches_per_run: int = 500
    matches_per_puuid: int      = 20
    seed_pages: int             = 2

    # Per-group PUUID quotas (override TIER_GROUPS defaults via .env)
    # These map 1:1 to the group names above.
    quota_challenger:    int = 50
    quota_grandmaster:   int = 50
    quota_master:        int = 50
    quota_diamond:       int = 75
    quota_emerald:       int = 75
    quota_platinum_high: int = 75   # Plat I–II
    quota_platinum_low:  int = 75   # Plat III–IV

    @property
    def tier_group_quotas(self) -> dict[str, int]:
        """Map group name → quota from env-overridable config."""
        return {
            "Challenger":    self.quota_challenger,
            "Grandmaster":   self.quota_grandmaster,
            "Master":        self.quota_master,
            "Diamond":       self.quota_diamond,
            "Emerald":       self.quota_emerald,
            "Platinum I-II": self.quota_platinum_high,
            "Platinum III-IV": self.quota_platinum_low,
        }

    @property
    def total_seed_puuids(self) -> int:
        """Total PUUIDs collected per platform across all tier groups."""
        return sum(self.tier_group_quotas.values())
