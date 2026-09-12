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
#: **`studio` を載せる（2026-09-07）。** Muse Refine が同じコレクションに座った
#: ので、どちらのスタジオの回かを一覧の段階で見分ける必要がある。`character` は
#: 載せない —— 一覧を重くしないため（名前が要る側が自分の分だけ load する）。
_LIST_FIELDS = ["session_id", "status", "inputs", "created_at", "studio"]


async def list_recent(
    db, *, limit: int = 20, studio: str | None = None,
) -> list[dict[str, Any]]:
    """最近のセッション。`studio` でスタジオを選ぶ。

    `None` は全部（既定）、`""` は退役した classic が書いた行、`"muse_refine"` は
    いまの撮影室（`service.STUDIO`。**保存値なので名前が古いまま**）。

    classic は退役した（2026-09-12）ので仕切りの意味は変わった —— いまは
    「開ける行」と「もう開けない古い行」の区別。残しているのは、古い行が
    一覧に混ざると開けないものを開こうとしてしまうから。
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
    """Hard-delete every Muse session — used by the "記憶の消去" admin action."""
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
    # **この回に実際に使った種を欄に書き戻す。** 頼むときは 0（＝引き直して）で
    # 出すので、描き終わるまで欄は嘘をついていた。本番はこの欄を読む
    # （`service.approve_and_shoot`）—— 書き戻さないと 0 のまま渡り、
    # **総監督が OK を出した絵とは違う種で本番が走る**（2026-09-12 に実機の
    # 6セッションすべてで不一致を確認した）。
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
    # 本番は board から種を受け取っているので既に入っているが、受け取れなかった
    # 古い行のために同じ形で書き戻す。
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


# Legacy aliases used by older draft helpers / tests.
async def attach_draft_image(db, session_id: str, image_id: str, meta: dict) -> None:
    await attach_board_image(db, session_id, image_id, meta)


async def finish_draft(db, session_id: str, *, error: str = "") -> None:
    await finish_board(db, session_id, error=error)


def log(session: dict[str, Any], step: str, detail: str) -> None:
    session.setdefault("timeline", []).append({
        "at": time.time(), "step": step, "detail": detail,
    })
