"""In-process SSE fan-out for Weave session events."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# session_id → list of subscriber queues
_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
_lock = asyncio.Lock()


async def subscribe(session_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    async with _lock:
        _subscribers[session_id].append(q)
    return q


async def unsubscribe(session_id: str, q: asyncio.Queue) -> None:
    async with _lock:
        subs = _subscribers.get(session_id) or []
        if q in subs:
            subs.remove(q)
        if not subs:
            _subscribers.pop(session_id, None)


def publish(session_id: str, event: dict[str, Any]) -> None:
    """Best-effort fan-out (sync-safe; drops if queue full)."""
    if not session_id:
        return
    payload = {
        **event,
        "session_id": session_id,
        "at": event.get("at") or time.time(),
    }
    subs = list(_subscribers.get(session_id) or [])
    for q in subs:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            logger.debug("weave SSE queue full session=%s", session_id)


def subscriber_count(session_id: str) -> int:
    return len(_subscribers.get(session_id) or [])
