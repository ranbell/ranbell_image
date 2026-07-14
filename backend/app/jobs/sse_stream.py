"""Shared SSE streaming helpers for PROMPT-lane job queues.

Refine / Chronicle encode queue items as JSON dicts.
Inspire / enhance-prompt often put pre-framed ``data: …\\n\\n`` strings on the queue.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, MutableMapping
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import StreamingResponse

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


async def iter_queue_sse(
    request: Request,
    queue: asyncio.Queue,
    *,
    job_id: str | None = None,
    registry: MutableMapping[str, Any] | None = None,
    registry_key: str | None = None,
    ping_seconds: float = 15.0,
    encode: Literal["json", "raw"] = "json",
    cancel_on_disconnect: bool = True,
    disconnect_grace_seconds: float = 45.0,
) -> AsyncIterator[str]:
    """Yield SSE frames until the queue sends ``None``.

    Client disconnect handling:
    - Proxies / browsers often flap ``is_disconnected()`` during long silent
      LLM stretches (Chronicle "Pinning down the action", etc.).
    - Require the client to stay disconnected for ``disconnect_grace_seconds``
      before ending the stream.
    - When ``cancel_on_disconnect`` is False (Chronicle), never cancel the job
      from the stream — only the explicit Cancel button should abort work.
    Always pops ``registry_key`` from ``registry`` in ``finally``.
    """
    key = registry_key if registry_key is not None else job_id
    disconnect_since: float | None = None
    try:
        while True:
            if await request.is_disconnected():
                now = asyncio.get_running_loop().time()
                if disconnect_since is None:
                    disconnect_since = now
                elif (now - disconnect_since) >= disconnect_grace_seconds:
                    if cancel_on_disconnect and job_id:
                        try:
                            await request.app.state.spooler.cancel(job_id)
                        except Exception:
                            pass
                    break
            else:
                disconnect_since = None
            try:
                item = await asyncio.wait_for(queue.get(), timeout=ping_seconds)
            except asyncio.TimeoutError:
                yield "event: ping\ndata: {}\n\n"
                continue
            if item is None:
                break
            if encode == "raw":
                yield item if isinstance(item, str) else f"data: {item}\n\n"
            else:
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
    finally:
        if registry is not None and key is not None:
            registry.pop(key, None)


def sse_response(agen: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        agen,
        media_type="text/event-stream",
        headers=dict(SSE_HEADERS),
    )


def queue_sse_response(
    request: Request,
    queue: asyncio.Queue,
    *,
    job_id: str | None = None,
    registry: MutableMapping[str, Any] | None = None,
    registry_key: str | None = None,
    ping_seconds: float = 15.0,
    encode: Literal["json", "raw"] = "json",
    cancel_on_disconnect: bool = True,
    disconnect_grace_seconds: float = 45.0,
) -> StreamingResponse:
    """Convenience: ``iter_queue_sse`` wrapped in ``StreamingResponse``."""
    return sse_response(
        iter_queue_sse(
            request,
            queue,
            job_id=job_id,
            registry=registry,
            registry_key=registry_key,
            ping_seconds=ping_seconds,
            encode=encode,
            cancel_on_disconnect=cancel_on_disconnect,
            disconnect_grace_seconds=disconnect_grace_seconds,
        )
    )
