"""Small pure helpers shared by the pipeline and the slim extraction layer."""

from __future__ import annotations

import re

_UNIT_PREFIX_RE = re.compile(r"^TFT\w+_", re.IGNORECASE)

_LEGACY_SET_RE = re.compile(r"^TFT(\d+)_", re.IGNORECASE)
_DA_DIGIT_RE = re.compile(r"(\d+)")

def unit_set_number(raw: str) -> int | None:
    """Extract the set number from a character id.

    Legacy ids (pre-Set 18): 'TFT17_Akali' -> 17.
    New-engine ids (Set 18+, 'DA_' prefix): 'DA_18_Xayah' -> 18,
    'DA_Vi18' -> 18, 'DA_KogMaw18_AD' -> 18.
    Generic neutral-monster ids with no set marker ('TFT_Krug') -> None,
    excluded from the majority vote.
    """
    m = _LEGACY_SET_RE.match(raw)
    if m:
        return int(m.group(1))
    if raw.upper().startswith("DA_"):
        m2 = _DA_DIGIT_RE.search(raw)
        if m2:
            return int(m2.group(1))
    return None


def norm_unit(raw: str) -> str:
    """Normalise a unit id: ``'TFT17_Akali'`` or ``'TFT_Unit_Akali'`` → ``'akali'``."""
    return _UNIT_PREFIX_RE.sub("", raw).lower()


def comp_key(units: list[str]) -> str:
    """Build the stable sorted comp key: ``'ahri|akali|amumu'``."""
    return "|".join(sorted(units))
