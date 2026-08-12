"""Long-term Muse shoot memories in Qdrant (embedded summaries)."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from qdrant_client import models as qm

from ..db.qdrant_client import MUSE_MEMORIES_COLLECTION

logger = logging.getLogger(__name__)

# Sticky detailed recaps kept on the character preset payload.
MAX_STICKY_RECAPS = 3


async def ensure_collection(db) -> None:
    qc = db._qc
    if await qc.collection_exists(MUSE_MEMORIES_COLLECTION):
        return
    from ..config import settings
    await qc.create_collection(
        collection_name=MUSE_MEMORIES_COLLECTION,
        vectors_config=qm.VectorParams(
            size=settings.embed_dim, distance=qm.Distance.COSINE, on_disk=True,
        ),
        on_disk_payload=True,
    )
    for field, schema in (
        ("character_id", qm.PayloadSchemaType.KEYWORD),
        ("kind", qm.PayloadSchemaType.KEYWORD),
        ("created_at", qm.PayloadSchemaType.FLOAT),
    ):
        try:
            await qc.create_payload_index(
                collection_name=MUSE_MEMORIES_COLLECTION,
                field_name=field,
                field_schema=schema,
            )
        except Exception:
            logger.debug("muse_memories index %s", field, exc_info=True)
    logger.info("Created collection: %s", MUSE_MEMORIES_COLLECTION)


def format_recap_text(recap: dict[str, Any]) -> str:
    when = str(recap.get("when") or "").strip()
    feel = str(recap.get("feel") or "").strip()
    liked = str(recap.get("liked") or "").strip()
    shot = str(recap.get("shot") or "").strip()
    parts = [p for p in (when, feel, liked, shot) if p]
    return " / ".join(parts)


async def upsert_summary(
    db, ollama, *, character_id: str, recap: dict[str, Any],
    session_id: str = "", embed_model: str = "",
) -> str:
    """Embed a short summary and store it. Returns memory id."""
    await ensure_collection(db)
    text = format_recap_text(recap)
    if not text or not character_id:
        return ""
    try:
        vec = await ollama.embed(text, model=embed_model or None)
    except Exception:
        logger.warning("[muse.memories] embed failed", exc_info=True)
        return ""
    mem_id = str(uuid.uuid4())
    payload = {
        "id": mem_id,
        "kind": "shoot_recap",
        "character_id": character_id,
        "session_id": session_id,
        "when": str(recap.get("when") or ""),
        "feel": str(recap.get("feel") or ""),
        "liked": str(recap.get("liked") or ""),
        "shot": str(recap.get("shot") or ""),
        "text": text,
        "created_at": time.time(),
    }
    await db._qc.upsert(
        collection_name=MUSE_MEMORIES_COLLECTION,
        points=[qm.PointStruct(id=mem_id, vector=vec, payload=payload)],
    )
    return mem_id


async def search(
    db, ollama, *, character_id: str, query: str, limit: int = 3,
    score_threshold: float = 0.35, embed_model: str = "",
) -> list[dict[str, Any]]:
    await ensure_collection(db)
    if not character_id or not str(query or "").strip():
        return []
    try:
        vec = await ollama.embed(str(query).strip(), model=embed_model or None)
    except Exception:
        logger.warning("[muse.memories] query embed failed", exc_info=True)
        return []
    try:
        results = await db._qc.query_points(
            collection_name=MUSE_MEMORIES_COLLECTION,
            query=vec,
            limit=limit,
            query_filter=qm.Filter(must=[
                qm.FieldCondition(
                    key="character_id", match=qm.MatchValue(value=character_id),
                ),
                qm.FieldCondition(
                    key="kind", match=qm.MatchValue(value="shoot_recap"),
                ),
            ]),
            with_payload=True,
            with_vectors=False,
        )
    except Exception:
        # Named vector collections sometimes need using=""; try without filter first path
        logger.warning("[muse.memories] search failed", exc_info=True)
        return []
    out: list[dict[str, Any]] = []
    for p in results.points:
        score = float(getattr(p, "score", 0.0) or 0.0)
        if score < score_threshold:
            continue
        payload = dict(p.payload or {})
        payload["_score"] = round(score, 4)
        payload["id"] = str(payload.get("id") or p.id)
        out.append(payload)
    return out


async def purge_character(db, character_id: str) -> int:
    await ensure_collection(db)
    if not character_id:
        return 0
    try:
        await db._qc.delete(
            collection_name=MUSE_MEMORIES_COLLECTION,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(must=[
                    qm.FieldCondition(
                        key="character_id",
                        match=qm.MatchValue(value=character_id),
                    ),
                ]),
            ),
        )
    except Exception:
        logger.warning("[muse.memories] purge failed", exc_info=True)
        return 0
    return 1


async def purge_all(db) -> None:
    await ensure_collection(db)
    try:
        await db._qc.delete(
            collection_name=MUSE_MEMORIES_COLLECTION,
            points_selector=qm.FilterSelector(filter=qm.Filter(must=[])),
        )
    except Exception:
        # recreate empty
        try:
            await db._qc.delete_collection(MUSE_MEMORIES_COLLECTION)
        except Exception:
            pass
        await ensure_collection(db)
