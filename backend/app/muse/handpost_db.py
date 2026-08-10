"""Studio handpost — director notices and preference history pages."""
from __future__ import annotations

import time
import uuid
from typing import Any

from qdrant_client import models as qm

from ..db.qdrant_client import MUSE_HANDPOST_COLLECTION


async def save_page(db, page: dict[str, Any]) -> dict[str, Any]:
    page = dict(page)
    page_id = str(page.get("id") or uuid.uuid4())
    now = time.time()
    page["id"] = page_id
    page.setdefault("created_at", now)
    page["updated_at"] = now
    page.setdefault("author", "director")
    page.setdefault("pinned", False)
    page.setdefault("title", "")
    page.setdefault("body_ja", "")
    page.setdefault("body_en", "")
    await db._qc.upsert(
        collection_name=MUSE_HANDPOST_COLLECTION,
        points=[qm.PointStruct(id=page_id, vector={}, payload=page)],
    )
    return page


async def get_page(db, page_id: str) -> dict[str, Any] | None:
    if not page_id:
        return None
    points = await db._qc.retrieve(
        collection_name=MUSE_HANDPOST_COLLECTION,
        ids=[page_id], with_payload=True, with_vectors=False,
    )
    if not points:
        return None
    payload = points[0].payload or {}
    payload["id"] = str(points[0].id)
    return payload


async def list_pages(db, *, pinned_only: bool = False) -> list[dict[str, Any]]:
    offset = None
    rows: list[dict[str, Any]] = []
    while True:
        points, offset = await db._qc.scroll(
            collection_name=MUSE_HANDPOST_COLLECTION,
            limit=64, offset=offset, with_payload=True, with_vectors=False,
        )
        for p in points:
            payload = p.payload or {}
            if pinned_only and not payload.get("pinned"):
                continue
            payload["id"] = str(p.id)
            rows.append(payload)
        if offset is None or not points:
            break
    rows.sort(key=lambda r: (
        0 if r.get("pinned") else 1,
        -float(r.get("updated_at") or 0.0),
    ))
    return rows


async def delete_page(db, page_id: str) -> bool:
    if not page_id:
        return False
    existing = await get_page(db, page_id)
    if existing is None:
        return False
    await db._qc.delete(
        collection_name=MUSE_HANDPOST_COLLECTION,
        points_selector=qm.PointIdsList(points=[page_id]),
    )
    return True


async def pinned_notice_lines(db, *, ja: bool = True, limit: int = 3) -> list[str]:
    pages = await list_pages(db, pinned_only=True)
    lines: list[str] = []
    for page in pages[:limit]:
        title = str(page.get("title") or "").strip()
        body = str(
            (page.get("body_ja") if ja else page.get("body_en"))
            or page.get("body_ja") or page.get("body_en") or ""
        ).strip()
        # One short line per pin — brief injection, not the whole notebook.
        snippet = body.splitlines()[0].strip() if body else ""
        if len(snippet) > 80:
            snippet = snippet[:79] + "…"
        text = f"{title}: {snippet}" if title and snippet else (title or snippet)
        if text:
            lines.append(text)
    return lines
