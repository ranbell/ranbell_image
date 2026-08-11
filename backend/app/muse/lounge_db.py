"""Persistence for Muse lounge threads (wrap shares + friend replies)."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from qdrant_client import models as qm

from ..db.qdrant_client import MUSE_LOUNGE_COLLECTION

MAX_TRENDS = 12
_TRENDS_ID = "00000000-0000-4000-8000-000000000001"
_trends_lock = asyncio.Lock()


async def save_thread(db, thread: dict[str, Any]) -> dict[str, Any]:
    thread = dict(thread)
    thread_id = str(thread.get("id") or uuid.uuid4())
    thread["id"] = thread_id
    thread.setdefault("created_at", time.time())
    thread["updated_at"] = time.time()
    await db._qc.upsert(
        collection_name=MUSE_LOUNGE_COLLECTION,
        points=[qm.PointStruct(id=thread_id, vector={}, payload=thread)],
    )
    return thread


async def get_thread(db, thread_id: str) -> dict[str, Any] | None:
    if not thread_id:
        return None
    points = await db._qc.retrieve(
        collection_name=MUSE_LOUNGE_COLLECTION,
        ids=[thread_id], with_payload=True, with_vectors=False,
    )
    if not points:
        return None
    payload = points[0].payload or {}
    payload["id"] = str(points[0].id)
    return payload


async def list_threads(db, *, limit: int = 40, kind: str = "") -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 40), 100))
    scroll_filter = None
    if kind:
        scroll_filter = qm.Filter(must=[
            qm.FieldCondition(key="kind", match=qm.MatchValue(value=kind)),
        ])
    offset = None
    rows: list[dict[str, Any]] = []
    while True:
        points, offset = await db._qc.scroll(
            collection_name=MUSE_LOUNGE_COLLECTION,
            scroll_filter=scroll_filter,
            limit=64, offset=offset, with_payload=True, with_vectors=False,
        )
        for p in points:
            if str(p.id) == _TRENDS_ID:
                continue
            payload = p.payload or {}
            if str(payload.get("kind") or "") == "studio_trends":
                continue
            payload["id"] = str(p.id)
            rows.append(payload)
        if offset is None or not points:
            break
    rows.sort(key=lambda r: float(r.get("created_at") or 0.0), reverse=True)
    return rows[:limit]


async def get_trends(db) -> list[dict[str, Any]]:
    points = await db._qc.retrieve(
        collection_name=MUSE_LOUNGE_COLLECTION,
        ids=[_TRENDS_ID], with_payload=True, with_vectors=False,
    )
    if not points:
        return []
    return list((points[0].payload or {}).get("items") or [])


async def push_trend(db, item: dict[str, Any]) -> list[dict[str, Any]]:
    async with _trends_lock:
        items = await get_trends(db)
        entry = {
            **item,
            "id": str(item.get("id") or uuid.uuid4()),
            "at": time.time(),
            "twists": list(item.get("twists") or []),
            "tags": dict(item.get("tags") or {}),
        }
        items.insert(0, entry)
        items = items[:MAX_TRENDS]
        payload = {
            "id": _TRENDS_ID, "kind": "studio_trends",
            "items": items, "updated_at": time.time(),
        }
        await db._qc.upsert(
            collection_name=MUSE_LOUNGE_COLLECTION,
            points=[qm.PointStruct(id=_TRENDS_ID, vector={}, payload=payload)],
        )
        return items


async def summary(db, *, since: float = 0.0) -> dict[str, Any]:
    """Unread-ish counts for the gallery badge.

    `since` is the Showrunner's last peek (unix seconds). Threads created after
    that count as new; open pitches always count so unanswered ideas stay visible.
    A thread that is both new and an open pitch is counted once.
    """
    threads = await list_threads(db, limit=100)
    since = float(since or 0.0)
    new_threads = 0
    open_pitches = 0
    latest_at = 0.0
    badge_ids: set[str] = set()
    for th in threads:
        tid = str(th.get("id") or "")
        created = float(th.get("created_at") or 0.0)
        updated = float(th.get("updated_at") or 0.0)
        bumped = max(created, updated)
        if bumped > latest_at:
            latest_at = bumped
        is_new = bumped > since
        is_open_pitch = (
            str(th.get("kind") or "") == "pitch"
            and str(th.get("status") or "open") == "open"
        )
        if is_new:
            new_threads += 1
        if is_open_pitch:
            open_pitches += 1
        if tid and (is_new or is_open_pitch):
            badge_ids.add(tid)
    return {
        "new_threads": new_threads,
        "open_pitches": open_pitches,
        "unread": len(badge_ids),
        "latest_at": latest_at,
        "thread_count": len(threads),
    }


async def count_all(db) -> int:
    result = await db._qc.count(collection_name=MUSE_LOUNGE_COLLECTION, exact=True)
    return result.count


async def delete_all(db) -> int:
    """Hard-delete every lounge thread and the trends doc — used by the
    "記憶の消去" admin action."""
    n = await count_all(db)
    if n:
        await db._qc.delete(
            collection_name=MUSE_LOUNGE_COLLECTION,
            points_selector=qm.FilterSelector(filter=qm.Filter()),
        )
    return n


async def append_message(db, thread_id: str, message: dict[str, Any]) -> dict[str, Any] | None:
    thread = await get_thread(db, thread_id)
    if thread is None:
        return None
    messages = list(thread.get("messages") or [])
    msg = dict(message)
    msg.setdefault("id", str(uuid.uuid4()))
    msg.setdefault("turn", len(messages))
    messages.append(msg)
    thread["messages"] = messages
    thread["updated_at"] = time.time()
    return await save_thread(db, thread)
