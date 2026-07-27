"""CRUD for weave_sessions collection."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from qdrant_client import models as qm

from ..db.qdrant_client import WEAVE_SESSIONS_COLLECTION
from .schema import new_session_payload

logger = logging.getLogger(__name__)


async def create_session(db, payload: dict[str, Any] | None = None, **kwargs) -> str:
    session_id = str(uuid.uuid4())
    body = payload or new_session_payload(**kwargs)
    await db._qc.upsert(
        collection_name=WEAVE_SESSIONS_COLLECTION,
        points=[qm.PointStruct(id=session_id, vector={}, payload=body)],
    )
    return session_id


async def get_session(db, session_id: str) -> dict[str, Any] | None:
    points = await db._qc.retrieve(
        collection_name=WEAVE_SESSIONS_COLLECTION,
        ids=[session_id],
        with_payload=True,
    )
    if not points:
        return None
    return {"session_id": session_id, **(points[0].payload or {})}


async def save_session(db, session_id: str, session: dict[str, Any]) -> None:
    payload = {k: v for k, v in session.items() if k != "session_id"}
    payload["updated_at"] = time.time()
    await db._qc.set_payload(
        collection_name=WEAVE_SESSIONS_COLLECTION,
        payload=payload,
        points=qm.PointIdsList(points=[session_id]),
    )


async def list_sessions(db, limit: int = 50) -> list[dict[str, Any]]:
    points, _ = await db._qc.scroll(
        collection_name=WEAVE_SESSIONS_COLLECTION,
        limit=limit,
        with_payload=True,
        order_by=qm.OrderBy(key="created_at", direction=qm.Direction.DESC),
    )
    return [{"session_id": str(p.id), **(p.payload or {})} for p in points]


async def attach_render_result(
    db,
    session_id: str,
    *,
    kind: str,
    target: str,
    image_id: str,
) -> None:
    """Link a finished Comfy image onto board/sample/final slots."""
    session = await get_session(db, session_id)
    if not session:
        logger.warning("weave attach: session %s missing", session_id)
        return
    if kind == "board":
        board = (session.get("character") or {}).setdefault("board", {})
        images = list(board.get("images") or [])
        found = False
        for img in images:
            if img.get("slot") == target:
                img["image_id"] = image_id
                img["pending"] = False
                found = True
                break
        if not found:
            images.append({
                "slot": target,
                "image_id": image_id,
                "pending": False,
            })
        board["images"] = images
        session.setdefault("character", {})["board"] = board
    else:
        for panel in session.get("panels") or []:
            if panel.get("key") != target:
                continue
            bucket = "sample" if kind == "sample" else "final"
            prev = dict(panel.get(bucket) or {})
            prev["image_id"] = image_id
            prev["pending"] = False
            panel[bucket] = prev
            if kind == "sample":
                cam = ((panel.get("intent") or {}).get("camera") or "")
                if cam == "long_shot" and (panel.get("qa") or {}).get("framing") is None:
                    panel.setdefault("qa", {})["framing"] = "pass"
            break
        if kind == "final":
            finals = [
                (p.get("final") or {}).get("image_id")
                for p in (session.get("panels") or [])
            ]
            if all(finals) and len(finals) >= 3:
                session["status"] = "lookdev"  # ready to seal; not sealed yet
                session.setdefault("cross_panel_qa", {})["ready_for_final"] = True
    await save_session(db, session_id, session)
