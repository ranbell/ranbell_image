"""Qdrant-backed author archetype registry (no personal names)."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from qdrant_client import models as qm

from ..db.qdrant_client import AUTHORS_COLLECTION
from .author_seeds import AUTHOR_SEEDS

logger = logging.getLogger(__name__)


def _dummy_vector(dim: int) -> list[float]:
    return [0.0] * dim


async def list_authors(db, *, limit: int = 200) -> list[dict[str, Any]]:
    points, _ = await db._qc.scroll(
        collection_name=AUTHORS_COLLECTION,
        limit=limit,
        with_payload=True,
    )
    out = []
    for p in points:
        payload = p.payload or {}
        out.append({"id": str(p.id), **payload})
    out.sort(key=lambda x: (x.get("genre_tag") or "", x.get("name") or ""))
    return out


async def get_author(db, author_id: str) -> dict[str, Any] | None:
    points = await db._qc.retrieve(
        collection_name=AUTHORS_COLLECTION,
        ids=[author_id],
        with_payload=True,
    )
    if not points:
        return None
    return {"id": author_id, **(points[0].payload or {})}


async def find_author_by_name(db, name: str) -> dict[str, Any] | None:
    name = (name or "").strip()
    if not name:
        return None
    points, _ = await db._qc.scroll(
        collection_name=AUTHORS_COLLECTION,
        scroll_filter=qm.Filter(
            must=[qm.FieldCondition(key="name", match=qm.MatchValue(value=name))]
        ),
        limit=1,
        with_payload=True,
    )
    if not points:
        return None
    return {"id": str(points[0].id), **(points[0].payload or {})}


async def create_author(
    db,
    *,
    name: str,
    style_description: str,
    genre_tag: str = "",
    vector_dim: int,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    if await find_author_by_name(db, name):
        raise ValueError(f"author name already exists: {name}")
    author_id = str(uuid.uuid4())
    now = time.time()
    payload = {
        "name": name,
        "style_description": (style_description or "").strip(),
        "genre_tag": (genre_tag or "").strip(),
        "created_at": now,
        "updated_at": now,
    }
    await db._qc.upsert(
        collection_name=AUTHORS_COLLECTION,
        points=[
            qm.PointStruct(
                id=author_id,
                vector={"embedding": _dummy_vector(vector_dim)},
                payload=payload,
            )
        ],
    )
    return {"id": author_id, **payload}


async def update_author(
    db,
    author_id: str,
    *,
    name: str | None = None,
    style_description: str | None = None,
    genre_tag: str | None = None,
) -> dict[str, Any]:
    existing = await get_author(db, author_id)
    if not existing:
        raise KeyError(author_id)
    if name is not None:
        name = name.strip()
        other = await find_author_by_name(db, name)
        if other and other["id"] != author_id:
            raise ValueError(f"author name already exists: {name}")
        existing["name"] = name
    if style_description is not None:
        existing["style_description"] = style_description.strip()
    if genre_tag is not None:
        existing["genre_tag"] = genre_tag.strip()
    existing["updated_at"] = time.time()
    payload = {
        k: existing[k]
        for k in ("name", "style_description", "genre_tag", "created_at", "updated_at")
        if k in existing
    }
    await db._qc.set_payload(
        collection_name=AUTHORS_COLLECTION,
        payload=payload,
        points=qm.PointIdsList(points=[author_id]),
    )
    return {"id": author_id, **payload}


async def delete_author(db, author_id: str) -> None:
    await db._qc.delete(
        collection_name=AUTHORS_COLLECTION,
        points_selector=qm.PointIdsList(points=[author_id]),
    )


async def seed_authors_if_empty(db, *, vector_dim: int) -> int:
    """Insert archetype seeds when collection has no points. Returns inserted count."""
    existing, _ = await db._qc.scroll(
        collection_name=AUTHORS_COLLECTION,
        limit=1,
        with_payload=False,
    )
    if existing:
        return 0
    n = 0
    for seed in AUTHOR_SEEDS:
        try:
            await create_author(
                db,
                name=seed["name"],
                style_description=seed["style_description"],
                genre_tag=seed.get("genre_tag") or "",
                vector_dim=vector_dim,
            )
            n += 1
        except Exception as exc:
            logger.warning("[authors] seed failed for %s: %s", seed.get("name"), exc)
    logger.info("[authors] seeded %d archetypes", n)
    return n


async def resolve_author_style(
    db,
    *,
    author_id: str = "",
    author_style: str = "",
) -> str:
    """Prefer free-text author_style; else load style_description from registry."""
    text = (author_style or "").strip()
    if text:
        return text
    if not author_id:
        return ""
    row = await get_author(db, author_id)
    if not row:
        return ""
    return str(row.get("style_description") or "").strip()
