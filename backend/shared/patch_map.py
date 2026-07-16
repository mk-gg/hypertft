"""Translates internal Riot game_version strings → TFT display patch strings.

The game_version field in match data looks like:
    "Version 16.15.629.7318 (Apr 28 2025/11:22:27) [PUBLIC] <Releases/16.15>"

We extract the "16.15" part and look it up in TFT_PATCH_MAP to get "17.8".

Mapping source:
    clientconfig.rpg.riotgames.com/api/v1/config/public
    key: lol.client_settings.internal_to_external_patch_mapping → tft
"""

from __future__ import annotations

import re

# internal version → TFT display patch
TFT_PATCH_MAP: dict[str, str] = {
    "16.1":  "16.2",
    "16.2":  "16.3",
    "16.3":  "16.4",
    "16.4":  "16.5",
    "16.5":  "16.6",
    "16.6":  "16.7",
    "16.7":  "16.8",
    "16.8":  "17.1",
    "16.9":  "17.2",
    "16.10": "17.3",
    "16.11": "17.4",
    "16.12": "17.5",
    "16.13": "17.6",
    "16.14": "17.7",
    "16.15": "17.8",
    "16.16": "18.1",
    "16.17": "18.2",
    "16.18": "18.3",
    "16.19": "18.4",
    "16.20": "18.5",
    "16.21": "18.6",
    "16.22": "18.7",
    "16.23": "18.8",
    "16.24": "19.1",
}

_VERSION_RE = re.compile(r"Version\s+(\d+\.\d+)", re.IGNORECASE)


def parse_internal_version(game_version: str) -> str | None:
    """Extract the internal version number from a raw game_version string.

    Args:
        game_version: Raw version string from the Riot match API, e.g.
            ``"Version 16.15.629.7318 (...)"`` or a plain ``"16.15"``.

    Returns:
        The internal version (``"16.15"``), or ``None`` if parsing fails.
    """
    if not game_version:
        return None

    m = _VERSION_RE.search(game_version)
    if m:
        return m.group(1)

    # Already a plain version string like "16.15"
    if re.fullmatch(r"\d+\.\d+", game_version.strip()):
        return game_version.strip()

    return None


def resolve_tft_patch(game_version: str) -> str:
    """Map a raw game_version string to the TFT display patch.

    Args:
        game_version: Raw version string from the Riot match API.

    Returns:
        The TFT display patch (e.g. ``"17.8"``). Falls back to the internal
        version if it is not in the map (e.g. ``"16.99"``), or ``"unknown"``
        if parsing fails entirely.
    """
    internal = parse_internal_version(game_version)
    if internal is None:
        return "unknown"
    return TFT_PATCH_MAP.get(internal, internal)


def available_patches() -> list[str]:
    """Return all known TFT display patches, newest first."""
    # Sort by (major, minor) numerically
    def key(p: str) -> tuple[int, int]:
        parts = p.split(".")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0

    return sorted(set(TFT_PATCH_MAP.values()), key=key, reverse=True)
