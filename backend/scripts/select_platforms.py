"""Pick the platforms to collect for one data-pipeline run.

A manual ``workflow_dispatch`` may pin platforms via ``MANUAL_PLATFORMS``;
otherwise one group from ``PLATFORM_ROTATION`` is chosen, rotating by UTC
day-of-year so every group is used across the cycle.

Groups in ``PLATFORM_ROTATION`` are separated by ``|`` and platforms within a
group by spaces, e.g. ``'na1 euw1|kr sg2|vn2 oc1'``.

The chosen platforms are appended to ``$GITHUB_OUTPUT`` as
``platforms=<space-separated>`` (falling back to stdout when run locally), so a
later step can read ``${{ steps.<id>.outputs.platforms }}``. This replaces the
previous inline bash rotation, which was fragile under ``set -e`` and could leak
an unsplit ``|`` into the shell command line.
"""

from __future__ import annotations

import datetime as dt
import os
import sys


def select_platforms(rotation: str, manual: str, day_of_year: int) -> str:
    """Return the space-separated platforms for this run.

    Args:
        rotation: ``PLATFORM_ROTATION`` value (``|``-separated groups).
        manual: Explicit override (wins if non-empty).
        day_of_year: 1–366, used to index the rotation groups.

    Raises:
        ValueError: if there is no override and the rotation is empty.
    """
    manual = manual.strip()
    if manual:
        return manual

    groups = [g.strip() for g in rotation.split("|") if g.strip()]
    if not groups:
        raise ValueError("PLATFORM_ROTATION is empty — set it in the workflow env.")

    index = day_of_year % len(groups)
    return groups[index]


def main() -> None:
    """Resolve the platforms for this run and emit them for the workflow."""
    rotation = os.environ.get("PLATFORM_ROTATION", "")
    manual   = os.environ.get("MANUAL_PLATFORMS", "")
    doy      = dt.datetime.now(dt.timezone.utc).timetuple().tm_yday

    platforms = select_platforms(rotation, manual, doy)
    print(f"Selected platforms: {platforms!r} (day-of-year {doy})", file=sys.stderr)

    line = f"platforms={platforms}\n"
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(line)
    else:
        sys.stdout.write(line)


if __name__ == "__main__":
    main()
