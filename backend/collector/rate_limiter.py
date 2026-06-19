"""
collector/rate_limiter.py
Async token-bucket rate limiter for Riot API constraints.
"""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    """
    Enforces two sliding-window limits concurrently:
      - per_second : max requests in any 1-second window
      - per_2min   : max requests in any 120-second window

    Both limits are checked and enforced before each request slot is granted.
    """

    def __init__(self, per_second: int = 18, per_2min: int = 95) -> None:
        self._per_second = per_second
        self._per_2min   = per_2min
        self._ts_1s: list[float] = []
        self._ts_2m: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a request slot is available."""
        async with self._lock:
            while True:
                now = time.monotonic()

                # Prune expired timestamps
                self._ts_1s = [t for t in self._ts_1s if now - t < 1.0]
                self._ts_2m = [t for t in self._ts_2m if now - t < 120.0]

                if len(self._ts_1s) >= self._per_second:
                    wait = 1.0 - (now - self._ts_1s[0]) + 0.01
                    await asyncio.sleep(max(wait, 0.05))
                    continue

                if len(self._ts_2m) >= self._per_2min:
                    wait = 120.0 - (now - self._ts_2m[0]) + 0.1
                    print(
                        f"  ⏳ 2-min cap ({len(self._ts_2m)}/100). "
                        f"Cooling {round(wait, 1)}s …"
                    )
                    await asyncio.sleep(wait)
                    continue

                # Slot granted
                self._ts_1s.append(now)
                self._ts_2m.append(now)
                return
