"""A TTL cache for the endpoints that scroll the whole collection.

Several read endpoints (the tag histogram, the name index, model facets, the
date range) answer from a scroll over every point in Qdrant. Each one used to
hand-roll the same TTL check, and each carried the same two faults.

Nothing held a lock, so the instant a TTL lapsed every in-flight request started
its own full scroll — the load spike arrived exactly when the cache was cold and
Qdrant was least able to absorb it.

And a rebuild that raised took the cached value down with it. During a scan the
write path saturates Qdrant, the scroll hits the client read timeout, and a page
that had perfectly good tags a second earlier answers 500 instead. Stale data is
not a failure here: none of it is fresh enough for a minute to matter, and a
scan calls the invalidation hook when it finishes anyway.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# How long a failed rebuild is left alone before the next request tries again.
# Without it, a Qdrant that is timing out gets a fresh full-collection scroll
# from every request, and each one waits out the whole client timeout.
STALE_RETRY = 30.0


class Cached:
    """One cached value that would rather be stale than missing."""

    def __init__(self, ttl: float, *, what: str = "cache"):
        self.ttl = ttl
        self.what = what
        self.data: Any = None
        # When the next rebuild is allowed. A failure pushes this out by
        # STALE_RETRY rather than by the full TTL.
        self.due = 0.0
        # When the data actually came from Qdrant, so the log tells the truth
        # about how stale "stale" is.
        self.built_at = 0.0
        self._lock = asyncio.Lock()

    def _fresh(self) -> bool:
        return self.data is not None and time.monotonic() < self.due

    def clear(self) -> None:
        self.data = None
        self.due = 0.0
        self.built_at = 0.0

    async def get(self, build: Callable[[], Awaitable[Any]]) -> Any:
        """Cached value, rebuilding through ``build`` at most once at a time.

        Raises whatever ``build`` raised only when there is nothing to fall
        back on — an empty page is worse than an old one, but a wrong empty
        page presented as correct is worse than both.
        """
        if self._fresh():
            return self.data
        async with self._lock:
            # Someone may have rebuilt it while we queued for the lock.
            if self._fresh():
                return self.data
            try:
                self.data = await build()
                self.built_at = time.monotonic()
                self.due = self.built_at + self.ttl
            except Exception:
                if self.data is None:
                    raise
                logger.warning(
                    "%s rebuild failed; serving data %.0fs old",
                    self.what, time.monotonic() - self.built_at, exc_info=True,
                )
                self.due = time.monotonic() + STALE_RETRY
        return self.data
