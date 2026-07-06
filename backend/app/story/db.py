"""CRUD wrapper for the "stories" collection.

Collection creation and payload indexes live in db/qdrant_client.py (start());
this module only reads and writes story points.

Payload schema:
    base_image_id: sha256 of the base image
    base_time_axis: "past" | "present" | "future"
    worldview: str
    user_topic: str                      # お題 — what the story is about
    time_scale: "minutes" | "hours" | "days" | "months" | "years" | "decades"
    workflow_name: str
    locale: "en" | "ja"                  # language the story was generated in
    status: "draft" | "final"            # draft = candidates picked, not expanded
    title / title_ja: str
    overall_story / overall_story_ja: str
    axes: { past/present/future: { story, story_ja, prompt_positive,
                                   prompt_negative, image_id } }
    candidates: [ {id, title, summary, suggested_time_scale, key_motif} ]
    selected_candidate: "A" | "B" | "C"
    respin_history: [ {kind, temperature, candidates?/title/overall/axes?} ]
    context: { character_desc, scene_desc, wd14_identity_tags, common_tags,
               story_hooks }             # carried Phase 1 → Phase 2
    created_at: float (unix time)
    group_id: str
"""

import logging
import time
import uuid

from qdrant_client import models as qm

from ..db.qdrant_client import STORIES_COLLECTION

logger = logging.getLogger(__name__)

AXES = ("past", "present", "future")


def new_story_payload(
    *,
    base_image_id: str,
    base_time_axis: str,
    worldview: str,
    workflow_name: str,
    group_id: str,
    time_scale: str = "years",
    title: str = "",
    overall_story: str = "",
    user_topic: str = "",
    locale: str = "en",
    status: str = "final",
    candidates: list | None = None,
    selected_candidate: str = "",
    context: dict | None = None,
) -> dict:
    return {
        "base_image_id": base_image_id,
        "base_time_axis": base_time_axis,
        "worldview": worldview,
        "user_topic": user_topic,
        "time_scale": time_scale,
        "workflow_name": workflow_name,
        "locale": locale,
        "status": status,
        "title": title,
        "title_ja": "",
        "overall_story": overall_story,
        "overall_story_ja": "",
        "axes": {
            axis: {
                "story": "",
                "story_ja": "",
                "prompt_positive": None,
                "prompt_negative": None,
                "image_id": base_image_id if axis == base_time_axis else None,
            }
            for axis in AXES
        },
        "candidates": candidates or [],
        "selected_candidate": selected_candidate,
        "respin_history": [],
        "context": context or {},
        "created_at": time.time(),
        "group_id": group_id,
    }


async def create_story(db, payload: dict, embedding: list[float] | None = None) -> str:
    """Insert a new story point and return its story_id (UUID point id)."""
    story_id = str(uuid.uuid4())
    vector: dict = {"embedding": embedding} if embedding else {}
    await db._qc.upsert(
        collection_name=STORIES_COLLECTION,
        points=[qm.PointStruct(id=story_id, vector=vector, payload=payload)],
    )
    return story_id


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
    """Return the most recent stories, newest first."""
    points, _ = await db._qc.scroll(
        collection_name=STORIES_COLLECTION,
        limit=limit,
        with_payload=True,
        order_by=qm.OrderBy(key="created_at", direction=qm.Direction.DESC),
    )
    return [{"story_id": str(p.id), **(p.payload or {})} for p in points]


async def search_stories(db, embedding: list[float], limit: int = 20) -> list[dict]:
    """Semantic search over story text embeddings."""
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
    """Partial top-level payload update."""
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
    """Merge updates into axes[axis] via a nested-key write.

    Uses Qdrant's payload `key` path so two axes updated concurrently (e.g.
    the past and future image jobs finishing at the same time) cannot clobber
    each other, which a read-modify-write of the whole axes object would.
    """
    if axis not in AXES:
        raise ValueError(f"Unknown axis: {axis!r}")
    await db._qc.set_payload(
        collection_name=STORIES_COLLECTION,
        payload=updates,
        points=qm.PointIdsList(points=[story_id]),
        key=f"axes.{axis}",
    )


async def update_story(db, story_id: str, updates: dict) -> None:
    """Patch top-level story fields (e.g. workflow_name)."""
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
