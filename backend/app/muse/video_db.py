"""Qdrant storage for video studio talks (payload-only, no vectors)."""
from __future__ import annotations

import time
from typing import Any

from qdrant_client import models as qm

from ..db.qdrant_client import MUSE_VIDEO_COLLECTION

# What the talk list needs; the chat and the ideas stay out of it.
_LIST_FIELDS = [
    "talk_id", "sha256", "created_at", "updated_at", "character", "partner_character",
    "chosen_title", "prompts_count",
]
_PAGE = 256


async def save(db, talk: dict[str, Any]) -> dict[str, Any]:
    talk["updated_at"] = time.time()
    talk["chosen_title"] = next(
        (i.get("title", "") for i in talk.get("ideas") or [] if i.get("kind") == talk.get("chosen")),
        "",
    )
    talk["prompts_count"] = len(talk.get("prompts") or [])
    await db._qc.upsert(
        collection_name=MUSE_VIDEO_COLLECTION,
        points=[qm.PointStruct(id=talk["talk_id"], vector={}, payload=talk)],
    )
    return talk


async def load(db, talk_id: str) -> dict[str, Any] | None:
    points = await db._qc.retrieve(
        collection_name=MUSE_VIDEO_COLLECTION, ids=[talk_id], with_payload=True,
    )
    return dict(points[0].payload or {}) if points else None


async def list_recent(db, *, limit: int = 30) -> list[dict[str, Any]]:
    """Newest first. Scroll has no order, so read the light fields and sort."""
    rows: list[dict[str, Any]] = []
    offset = None
    while True:
        points, offset = await db._qc.scroll(
            collection_name=MUSE_VIDEO_COLLECTION, limit=_PAGE, offset=offset,
            with_payload=_LIST_FIELDS,
        )
        rows.extend(dict(p.payload or {}) for p in points)
        if offset is None or not points:
            break
    rows.sort(key=lambda r: r.get("updated_at") or r.get("created_at") or 0.0, reverse=True)
    return rows[:max(1, int(limit))]


async def delete(db, talk_id: str) -> None:
    await db._qc.delete(
        collection_name=MUSE_VIDEO_COLLECTION,
        points_selector=qm.PointIdsList(points=[talk_id]),
    )


async def count_all(db) -> int:
    return (await db._qc.count(collection_name=MUSE_VIDEO_COLLECTION, exact=True)).count


async def delete_all(db) -> int:
    """Part of the "erase memory" admin action, with the Muse sessions."""
    n = await count_all(db)
    if n:
        await db._qc.delete(
            collection_name=MUSE_VIDEO_COLLECTION,
            points_selector=qm.FilterSelector(filter=qm.Filter()),
        )
    return n
