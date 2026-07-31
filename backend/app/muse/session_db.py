"""Qdrant storage for Muse sessions (payload-only, no vectors)."""
from __future__ import annotations

import logging
import time
from typing import Any

from qdrant_client import models as qm

from ..db.qdrant_client import MUSE_SESSIONS_COLLECTION
from . import events

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


async def attach_board_image(
    db, session_id: str, track: str, seed_index: int, image_id: str, meta: dict,
) -> None:
    """Land a finished board render on its slot.

    Renders come back out of order and from a different task than the one that
    queued them, so the session is re-read here rather than closed over.
    """
    session = await load(db, session_id)
    if session is None:
        logger.warning("[muse] board landed for a session that is gone: %s", session_id)
        return
    slots = (session.get("board") or {}).get(track) or []
    for slot in slots:
        if slot.get("seed_index") == seed_index:
            slot["image_id"] = image_id
            slot["pending"] = False
            slot["seed"] = meta.get("seed", slot.get("seed"))
            break
    await save(db, session, publish=False)
    events.publish(session_id, {
        "type": "board_attached", "track": track,
        "seed_index": seed_index, "image_id": image_id,
    })


async def attach_final_image(db, session_id: str, image_id: str, meta: dict) -> None:
    session = await load(db, session_id)
    if session is None:
        return
    final = dict(session.get("final") or {})
    final["image_id"] = image_id
    final["seed"] = meta.get("seed", final.get("seed"))
    session["final"] = final
    session["status"] = "done"
    await save(db, session, publish=False)
    events.publish(session_id, {"type": "final_attached", "image_id": image_id})


def log(session: dict[str, Any], step: str, detail: str) -> None:
    session.setdefault("timeline", []).append({
        "at": time.time(), "step": step, "detail": detail,
    })
