"""A tiny in-process sliding-window rate limiter.

Used to bound how often the unauthenticated browser-softphone token endpoint
mints Twilio access tokens per client IP, capping the billing blast radius if a
token is leaked or the endpoint is abused. In-process only — it does not span
multiple workers/replicas; a shared store (e.g. Redis) is needed for that.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        max_calls: int,
        window_seconds: float,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_calls
        self._window = window_seconds
        self._now = now
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """Record a call for ``key`` and return whether it is within the limit."""
        t = self._now()
        hits = self._hits[key]
        cutoff = t - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self._max:
            return False
        hits.append(t)
        return True
