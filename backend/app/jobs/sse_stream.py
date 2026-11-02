"""Shared SSE streaming helpers for PROMPT-lane job queues.

Refine encodes queue items as JSON dicts.
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
    keep_queue_on_disconnect: bool = False,
) -> AsyncIterator[str]:
    """Yield SSE frames until the queue sends ``None``.

    Client disconnect handling:
    - Proxies / browsers often flap ``is_disconnected()`` during long silent
      LLM stretches.
    - Require the client to stay disconnected for ``disconnect_grace_seconds``
      before ending the stream.
    - When ``cancel_on_disconnect`` is False, never cancel the job
      from the stream — only the explicit Cancel button should abort work.

    ``keep_queue_on_disconnect`` is what lets a browser reload rejoin a job
    instead of losing it (the Showrunner, 2026-11-02: "make it so a browser
    reload does not lose track of the task — that is what the spooler is for").
    With it:

    - the queue is left in ``registry``, so the next request finds it instead
      of a 404 — popping it on disconnect was the thing that closed the door;
    - whatever came off the queue while the client was gone is **put back**
      rather than yielded into a dead socket, where there is no way to know
      whether it arrived. `asyncio.Queue` is unbounded, so the rest of the run
      simply piles up and the client that rejoins reads the backlog — no replay
      buffer needed anywhere.

    The registry is still cleared the moment the queue says ``None``: that is
    the job's real end of life, as opposed to one reader going away.
    """
    key = registry_key if registry_key is not None else job_id
    disconnect_since: float | None = None
    finished = False
    held: list[Any] = []
    try:
        while True:
            gone = await request.is_disconnected()
            if gone:
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
                finished = True
                break
            if gone and keep_queue_on_disconnect:
                # Nobody is listening. Hold it rather than talk to a dead socket.
                held.append(item)
                continue
            if encode == "raw":
                yield item if isinstance(item, str) else f"data: {item}\n\n"
            else:
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
    finally:
        if keep_queue_on_disconnect and not finished and held:
            # Back on, *in front of* what is still queued. `put_nowait` appends,
            # so putting them straight back would hand the next reader the run
            # out of order — the held items came off the queue first.
            rest = []
            while not queue.empty():
                rest.append(queue.get_nowait())
            for item in held + rest:
                queue.put_nowait(item)
        if registry is not None and key is not None:
            if finished or not keep_queue_on_disconnect:
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
    keep_queue_on_disconnect: bool = False,
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
            keep_queue_on_disconnect=keep_queue_on_disconnect,
        )
    )
