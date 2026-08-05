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


async def attach_board_image(db, session_id: str, image_id: str, meta: dict) -> None:
    session = await load(db, session_id)
    if session is None:
        logger.warning("[muse] board landed for a session that is gone: %s", session_id)
        return
    board = session.setdefault("board", {})
    images = board.setdefault("images", [])
    images.append({
        "index": len(images), "image_id": image_id,
        "seed": meta.get("seed", board.get("seed")),
    })
    await save(db, session, publish=False)
    events.publish(session_id, {
        "type": "board_attached", "index": len(images) - 1, "image_id": image_id,
    })


async def finish_board(db, session_id: str, *, error: str = "") -> None:
    session = await load(db, session_id)
    if session is None:
        return
    board = session.get("board") or {}
    if not board:
        return
    board["pending"] = False
    if error:
        board["error"] = error
        warnings = session.setdefault("warnings", [])
        if error not in warnings:
            warnings.append(error)
    session["status"] = "awaiting_ok" if board.get("images") else "chat"
    await save(db, session)
    if board.get("images"):
        events.publish(session_id, {
            "type": "board_ready",
            "count": len(board["images"]),
            "question": True,
        })


async def attach_shoot_image(db, session_id: str, image_id: str, meta: dict) -> None:
    session = await load(db, session_id)
    if session is None:
        return
    shoot = session.setdefault("shoot", {})
    images = shoot.setdefault("images", [])
    images.append({
        "index": len(images), "image_id": image_id,
        "seed": meta.get("seed", shoot.get("seed")),
    })
    await save(db, session, publish=False)
    events.publish(session_id, {
        "type": "shoot_attached", "index": len(images) - 1, "image_id": image_id,
    })


async def finish_shoot(db, session_id: str, *, error: str = "") -> None:
    session = await load(db, session_id)
    if session is None:
        return
    shoot = session.get("shoot") or {}
    if not shoot:
        return
    shoot["pending"] = False
    if error:
        shoot["error"] = error
        warnings = session.setdefault("warnings", [])
        if error not in warnings:
            warnings.append(error)
    session["status"] = "done" if shoot.get("images") else "awaiting_ok"
    await save(db, session)


# Legacy aliases used by older draft helpers / tests.
async def attach_draft_image(db, session_id: str, image_id: str, meta: dict) -> None:
    await attach_board_image(db, session_id, image_id, meta)


async def finish_draft(db, session_id: str, *, error: str = "") -> None:
    await finish_board(db, session_id, error=error)


def log(session: dict[str, Any], step: str, detail: str) -> None:
    session.setdefault("timeline", []).append({
        "at": time.time(), "step": step, "detail": detail,
    })
