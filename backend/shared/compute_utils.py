"""Small pure helpers shared by the pipeline and the slim extraction layer."""

from __future__ import annotations

import re

_UNIT_PREFIX_RE = re.compile(r"^TFT\w+_", re.IGNORECASE)


def norm_unit(raw: str) -> str:
    """Normalise a unit id: ``'TFT17_Akali'`` or ``'TFT_Unit_Akali'`` → ``'akali'``."""
    return _UNIT_PREFIX_RE.sub("", raw).lower()


def comp_key(units: list[str]) -> str:
    """Build the stable sorted comp key: ``'ahri|akali|amumu'``."""
    return "|".join(sorted(units))
