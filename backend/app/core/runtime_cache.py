import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class RuntimeConfigCache:
    """TTL cache that suppresses repeated get_config() calls to Qdrant."""

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        self._ttl = ttl_seconds
        self._cache: dict | None = None
        self._fetched_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get(self, db) -> dict:
        now = time.monotonic()
        if self._cache is not None and (now - self._fetched_at) < self._ttl:
            return self._cache
        async with self._lock:
            now = time.monotonic()
            if self._cache is not None and (now - self._fetched_at) < self._ttl:
                return self._cache
            from ..runtime_config import _defaults
            doc = await db.get_config()
            self._cache = {k: doc.get(k, v) for k, v in _defaults.items()}
            self._fetched_at = now
            logger.debug("RuntimeConfig cache refreshed")
            return self._cache

    def invalidate(self) -> None:
        self._cache = None
