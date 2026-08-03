"""Qdrant storage for Muse sessions (payload-only, no vectors)."""
from __future__ import annotations

import logging
import time
from typing import Any

from qdrant_client import models as qm

from ..db.qdrant_client import MUSE_SESSIONS_COLLECTION
from . import events, schema

logger = logging.getLogger(__name__)


async def save(db, session: dict[str, Any], *, publish: bool = True) -> dict[str, Any]:
    session["updated_at"] = time.time()
    await db._qc.upsert(
        collection_name=MUSE_SESSIONS_COLLECTION,
        points=[qm.PointStruct(
            id=session["session_id"], vector={}, payload=session,
        )],
    )
    if publish:
        events.publish(session["session_id"], {
            "type": "session_updated",
            "status": session.get("status"),
        })
    return session


async def load(db, session_id: str) -> dict[str, Any] | None:
    points = await db._qc.retrieve(
        collection_name=MUSE_SESSIONS_COLLECTION,
        ids=[session_id],
        with_payload=True,
    )
    return dict(points[0].payload or {}) if points else None


async def list_recent(db, *, limit: int = 20) -> list[dict[str, Any]]:
    points, _ = await db._qc.scroll(
        collection_name=MUSE_SESSIONS_COLLECTION,
        limit=limit,
        with_payload=True,
    )
    rows = [
        {
            "session_id": (p.payload or {}).get("session_id", str(p.id)),
            "status": (p.payload or {}).get("status", ""),
            "theme": ((p.payload or {}).get("inputs") or {}).get("theme", ""),
            "created_at": (p.payload or {}).get("created_at", 0.0),
        }
        for p in points
    ]
    rows.sort(key=lambda r: r.get("created_at") or 0.0, reverse=True)
    return rows


async def delete(db, session_id: str) -> None:
    await db._qc.delete(
        collection_name=MUSE_SESSIONS_COLLECTION,
        points_selector=qm.PointIdsList(points=[session_id]),
    )


async def attach_draft_image(db, session_id: str, image_id: str, meta: dict) -> None:
    """Land one image of the draft batch.

    The four drafts come from a single job — one seed, one latent, batch of four
    — so this is called once per image as each is saved, in batch order. The
    session is re-read rather than closed over because the render runs in a
    different task from the one that queued it.
    """
    session = await load(db, session_id)
    if session is None:
        logger.warning("[muse] draft landed for a session that is gone: %s", session_id)
        return
    draft = session.setdefault("draft", {})
    images = draft.setdefault("images", [])
    images.append({"index": len(images), "image_id": image_id,
                   "seed": meta.get("seed", draft.get("seed"))})
    expected = int((session.get("inputs") or {}).get("draft_count") or 0)
    if expected and len(images) >= expected:
        draft["pending"] = False
    await save(db, session, publish=False)
    events.publish(session_id, {
        "type": "draft_attached", "index": len(images) - 1,
        "image_id": image_id, "total": expected,
    })


async def attach_stage_image(
    db, session_id: str, chain_index: int, stage_index: int,
    image_id: str, meta: dict,
) -> None:
    """Land the render for one refine stage of one chain."""
    session = await load(db, session_id)
    if session is None:
        return
    chains = session.get("chains") or []
    if not 0 <= chain_index < len(chains):
        return
    stages = chains[chain_index].get("stages") or []
    if not 0 <= stage_index < len(stages):
        return
    stages[stage_index]["image_id"] = image_id
    stages[stage_index]["pending"] = False
    stages[stage_index]["seed"] = meta.get("seed", stages[stage_index].get("seed"))
    landed = [s for s in schema.all_stages(session) if s.get("image_id")]
    total = len(schema.all_stages(session))
    if landed and len(landed) == total:
        session["status"] = "done"
    await save(db, session, publish=False)
    events.publish(session_id, {
        "type": "stage_attached", "chain": chain_index, "stage": stage_index,
        "image_id": image_id, "done": len(landed), "total": total,
    })


async def record_wd14(db, session_id: str, chain_index: int, tags: str) -> None:
    session = await load(db, session_id)
    if session is None:
        return
    chains = session.get("chains") or []
    if 0 <= chain_index < len(chains):
        chains[chain_index]["wd14"] = tags
        await save(db, session)


async def record_stage_prompt(
    db, session_id: str, chain_index: int, stage_index: int, prompt: str,
) -> None:
    """Store a stage's prompt as soon as it is written.

    Written before the render rather than after it, so the panel can show what
    is being drawn while it is being drawn — and so a render that fails still
    leaves behind the prompt that caused it.
    """
    session = await load(db, session_id)
    if session is None:
        return
    chains = session.get("chains") or []
    if not 0 <= chain_index < len(chains):
        return
    stages = chains[chain_index].get("stages") or []
    if not 0 <= stage_index < len(stages):
        return
    stages[stage_index]["prompt"] = prompt
    stages[stage_index]["pending"] = True
    await save(db, session)


def log(session: dict[str, Any], step: str, detail: str) -> None:
    session.setdefault("timeline", []).append({
        "at": time.time(), "step": step, "detail": detail,
    })
