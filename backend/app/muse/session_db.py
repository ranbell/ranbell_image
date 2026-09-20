"""Qdrant storage for Muse sessions (payload-only, no vectors)."""
from __future__ import annotations

import logging
import time
from typing import Any

from qdrant_client import models as qm

from ..db.qdrant_client import MUSE_SESSIONS_COLLECTION
from . import events, notebook

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
    """Read a session, bringing it up to the current shape on the way out.

    This is the one read funnel — the runner, the API, the report and the
    service all come through here — so it is the one place a migration has to
    be hooked. `notebook.migrate` seeds the living notebook (and still runs
    the legacy facet migrate for older rows).
    """
    points = await db._qc.retrieve(
        collection_name=MUSE_SESSIONS_COLLECTION,
        ids=[session_id],
        with_payload=True,
    )
    if not points:
        return None
    return notebook.migrate(dict(points[0].payload or {}))


# Qdrant's scroll has no ordering, so "recent" means read everything and then
# sort. Asking it for `limit` points and sorting those returned an arbitrary
# handful — a report over "the last five sessions" was five sessions picked at
# random, which is worse than useless when the whole point is a trend.
_SCROLL_PAGE = 256
#: **Carry `studio` (2026-09-07).** Muse Refine sits in the same collection, so
#: which studio a session belongs to has to be told apart at the listing stage.
#: `character` is not carried — to keep the listing light (whoever needs the names
#: loads only their own rows).
_LIST_FIELDS = ["session_id", "status", "inputs", "created_at", "studio"]


async def list_recent(
    db, *, limit: int = 20, studio: str | None = None,
) -> list[dict[str, Any]]:
    """Recent sessions. `studio` selects which studio.

    `None` is all of them (the default), `""` is rows written by the retired
    classic, and `"muse_refine"` is the current studio (`service.STUDIO` — **a
    stored value, so the name stays old**).

    Classic retired on 2026-09-12, so the divider now means something else: rows
    that can be opened versus old rows that cannot. It is kept because old rows
    mixed into the list invite opening something that will not open.
    """
    rows: list[dict[str, Any]] = []
    offset = None
    while True:
        points, offset = await db._qc.scroll(
            collection_name=MUSE_SESSIONS_COLLECTION,
            limit=_SCROLL_PAGE,
            offset=offset,
            # Payload subset: the chat log is the bulk of a session and no
            # caller of this list has ever wanted it.
            with_payload=_LIST_FIELDS,
        )
        rows.extend(
            {
                "session_id": (p.payload or {}).get("session_id", str(p.id)),
                "status": (p.payload or {}).get("status", ""),
                "theme": ((p.payload or {}).get("inputs") or {}).get("theme", ""),
                "created_at": (p.payload or {}).get("created_at", 0.0),
                "studio": str((p.payload or {}).get("studio") or ""),
            }
            for p in points
        )
        if offset is None or not points:
            break
    if studio is not None:
        want = str(studio)
        rows = [r for r in rows if r.get("studio") == want]
    rows.sort(key=lambda r: r.get("created_at") or 0.0, reverse=True)
    return rows[:max(1, int(limit))]


async def delete(db, session_id: str) -> None:
    await db._qc.delete(
        collection_name=MUSE_SESSIONS_COLLECTION,
        points_selector=qm.PointIdsList(points=[session_id]),
    )


async def count_all(db) -> int:
    result = await db._qc.count(collection_name=MUSE_SESSIONS_COLLECTION, exact=True)
    return result.count


async def delete_all(db) -> int:
    """Hard-delete every Muse session — used by the "erase memory" admin action."""
    n = await count_all(db)
    if n:
        await db._qc.delete(
            collection_name=MUSE_SESSIONS_COLLECTION,
            points_selector=qm.FilterSelector(filter=qm.Filter()),
        )
    return n


async def attach_board_image(db, session_id: str, image_id: str, meta: dict) -> None:
    session = await load(db, session_id)
    if session is None:
        logger.warning("[muse] board landed for a session that is gone: %s", session_id)
        return
    board = session.setdefault("board", {})
    images = board.setdefault("images", [])
    used = meta.get("seed", board.get("seed"))
    images.append({
        "index": len(images), "image_id": image_id,
        "seed": used,
    })
    # **Write the seed actually used back into the field.** The request goes out
    # with 0 (= draw a new one), so until the render finished the field was lying.
    # The final reads this field (`service.approve_and_shoot`) — without the write
    # back it is handed 0 and **the final runs on a different seed from the picture
    # the Showrunner approved** (on 2026-09-12 all six live sessions were confirmed
    # to disagree).
    if not board.get("seed") and used:
        board["seed"] = int(used)
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
    used = meta.get("seed", shoot.get("seed"))
    images.append({
        "index": len(images), "image_id": image_id,
        "seed": used,
    })
    # The final receives the seed from the board and already has it; the same write
    # back is done for older rows that could not receive one.
    if not shoot.get("seed") and used:
        shoot["seed"] = int(used)
    await save(db, session, publish=False)
    events.publish(session_id, {
        "type": "shoot_attached", "index": len(images) - 1, "image_id": image_id,
    })


async def finish_shoot(db, session_id: str, *, error: str = "", ollama=None) -> None:
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
    # Continuity memory is written only after a successful final take.
    if shoot.get("images") and not error:
        try:
            from . import shared as muse_service
            await muse_service.record_shoot_continuity(db, session, ollama=ollama)
        except Exception:
            logger.warning("[muse] continuity write failed", exc_info=True)


def log(session: dict[str, Any], step: str, detail: str) -> None:
    session.setdefault("timeline", []).append({
        "at": time.time(), "step": step, "detail": detail,
    })
