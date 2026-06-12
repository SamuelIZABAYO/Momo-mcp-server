"""Client-side token-bucket rate limiter.

MTN's sandbox throttles aggressively; we self-limit to stay under it. Simple,
async, single-process, refills continuously at ``rate`` tokens/sec up to a
burst of ``rate``.
"""

from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be > 0")
        self._rate = rate_per_sec
        self._capacity = rate_per_sec
        self._tokens = rate_per_sec
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._last) * self._rate
                )
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                deficit = (1 - self._tokens) / self._rate
                await asyncio.sleep(deficit)
