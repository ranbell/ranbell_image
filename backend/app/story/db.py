"""CRUD wrapper for the "stories" collection — panel_1/2/3 contract."""

from __future__ import annotations

import logging
import time
import uuid

from qdrant_client import models as qm

from ..db.qdrant_client import STORIES_COLLECTION

logger = logging.getLogger(__name__)

AXES = ("panel_1", "panel_2", "panel_3")
PANELS = AXES


def new_story_payload(
    *,
    base_image_id: str,
    workflow_name: str,
    group_id: str,
    title: str = "",
    overall_story: str = "",
    user_topic: str = "",
    locale: str = "ja",
    status: str = "final",
    candidates: list | None = None,
    selected_candidate: str = "",
    context: dict | None = None,
    base_model_name: str = "",
    include_happening: bool = False,
    author_style: str = "",
    # Deprecated kwargs accepted for call-site compatibility during migration
    base_time_axis: str = "",
    worldview: str = "",
    time_scale: str = "",
    emotion: str = "",
) -> dict:
    return {
        "base_image_id": base_image_id,
        "base_model_name": base_model_name,
        "user_topic": user_topic,
        "workflow_name": workflow_name,
        "locale": locale,
        "status": status,
        "title": title,
        "title_ja": "",
        "overall_story": overall_story,
        "overall_story_ja": "",
        "include_happening": include_happening,
        "author_style": author_style,
        "axes": {
            axis: {
                "story": "",
                "story_ja": "",
                "prompt_positive": None,
                "prompt_negative": None,
                "image_id": None,
            }
            for axis in AXES
        },
        "candidates": candidates or [],
        "selected_candidate": selected_candidate,
        "respin_history": [],
        "context": context or {},
        "created_at": time.time(),
        "group_id": group_id,
        "time_scale": time_scale,
    }


async def create_story(db, payload: dict, embedding: list[float] | None = None) -> str:
    story_id = str(uuid.uuid4())
    vector: dict = {"embedding": embedding} if embedding else {}
    await db._qc.upsert(
        collection_name=STORIES_COLLECTION,
        points=[qm.PointStruct(id=story_id, vector=vector, payload=payload)],
    )
    return story_id


async def fork_draft(db, story: dict) -> str:
    payload = new_story_payload(
        base_image_id=story.get("base_image_id", ""),
        workflow_name=story.get("workflow_name", ""),
        group_id=story.get("group_id", ""),
        user_topic=story.get("user_topic", ""),
        locale=story.get("locale", "ja"),
        status="draft",
        candidates=story.get("candidates") or [],
        context=story.get("context") or {},
        include_happening=bool(story.get("include_happening")),
        author_style=story.get("author_style") or "",
        base_model_name=story.get("base_model_name") or "",
    )
    if story.get("pinups") or story.get("pinup_image_id"):
        payload["pinups"] = list(story.get("pinups") or [])
        payload["pinup_image_id"] = story.get("pinup_image_id")
    return await create_story(db, payload)


async def get_story(db, story_id: str) -> dict | None:
    points = await db._qc.retrieve(
        collection_name=STORIES_COLLECTION,
        ids=[story_id],
        with_payload=True,
    )
    if not points:
        return None
    return {"story_id": story_id, **(points[0].payload or {})}


async def list_stories(db, limit: int = 50) -> list[dict]:
    points, _ = await db._qc.scroll(
        collection_name=STORIES_COLLECTION,
        limit=limit,
        with_payload=True,
        order_by=qm.OrderBy(key="created_at", direction=qm.Direction.DESC),
    )
    return [{"story_id": str(p.id), **(p.payload or {})} for p in points]


async def search_stories(db, embedding: list[float], limit: int = 20) -> list[dict]:
    hits = await db._qc.query_points(
        collection_name=STORIES_COLLECTION,
        query=embedding,
        using="embedding",
        limit=limit,
        with_payload=True,
    )
    return [
        {"story_id": str(h.id), "score": h.score, **(h.payload or {})}
        for h in hits.points
    ]


async def set_story_payload(db, story_id: str, updates: dict) -> None:
    await db._qc.set_payload(
        collection_name=STORIES_COLLECTION,
        payload=updates,
        points=qm.PointIdsList(points=[story_id]),
    )


async def set_story_embedding(db, story_id: str, embedding: list[float]) -> None:
    await db._qc.update_vectors(
        collection_name=STORIES_COLLECTION,
        points=[qm.PointVectors(id=story_id, vector={"embedding": embedding})],
    )


async def update_story_axis(db, story_id: str, axis: str, updates: dict) -> None:
    if axis not in AXES:
        raise ValueError(f"Unknown axis: {axis!r}")
    await db._qc.set_payload(
        collection_name=STORIES_COLLECTION,
        payload=updates,
        points=qm.PointIdsList(points=[story_id]),
        key=f"axes.{axis}",
    )


async def update_story(db, story_id: str, updates: dict) -> None:
    await db._qc.set_payload(
        collection_name=STORIES_COLLECTION,
        payload=updates,
        points=qm.PointIdsList(points=[story_id]),
    )


async def delete_story(db, story_id: str) -> None:
    await db._qc.delete(
        collection_name=STORIES_COLLECTION,
        points_selector=qm.PointIdsList(points=[story_id]),
    )
