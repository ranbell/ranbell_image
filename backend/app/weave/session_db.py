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
