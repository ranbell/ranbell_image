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
    ollama=None,
    job_id: str = "",
    seed_index: int | None = None,
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
            jid = str(job_id or "").strip()
            if kind == "sample":
                hist = list(panel.get("sample_history") or [])
                matched = None
                if jid:
                    for h in hist:
                        if str(h.get("job_id") or "") == jid:
                            h["image_id"] = image_id
                            h["pending"] = False
                            matched = h
                            break
                if matched is None:
                    for h in hist:
                        if h.get("pending") and not h.get("image_id"):
                            h["image_id"] = image_id
                            h["pending"] = False
                            if jid:
                                h["job_id"] = jid
                            matched = h
                            break
                if matched is None:
                    matched = {
                        "job_id": jid or None,
                        "seed": None,
                        "image_id": image_id,
                        "pending": False,
                        "seed_index": seed_index,
                    }
                    hist.append(matched)
                panel["sample_history"] = hist[-9:]

                primary_job = str((panel.get("sample") or {}).get("job_id") or "")
                is_primary = (
                    (jid and jid == primary_job)
                    or (not primary_job and (seed_index in (None, 0)))
                    or (
                        not (panel.get("sample") or {}).get("image_id")
                        and seed_index in (None, 0)
                    )
                )
                if is_primary:
                    prev = dict(panel.get("sample") or {})
                    prev["image_id"] = image_id
                    prev["pending"] = False
                    if jid:
                        prev["job_id"] = jid
                    panel["sample"] = prev
                    from .verify.heuristics import apply_framing_to_panel, resolve_wd14_for_image
                    from .verify.score import apply_weave_scores
                    from .verify.vlm_assist import apply_heuristic_vlm, apply_vlm_assist_to_panel
                    from .verify.cross_panel import refresh_cross_panel_qa

                    wd14 = await resolve_wd14_for_image(db, image_id)
                    apply_framing_to_panel(panel, wd14, image_id=image_id)
                    policy = session.get("quality_policy") or {}
                    if policy.get("vlm_assist", True):
                        inputs = session.get("inputs") or {}
                        has_model = bool(
                            str(inputs.get("vlm_model") or inputs.get("story_model") or "").strip()
                        )
                        if ollama is not None and has_model:
                            try:
                                await apply_vlm_assist_to_panel(
                                    panel, session, db=db, ollama=ollama, wd14_tags=wd14,
                                )
                            except Exception as exc:
                                logger.warning("weave VLM assist failed: %s", exc)
                                apply_heuristic_vlm(panel, session, wd14)
                        else:
                            apply_heuristic_vlm(panel, session, wd14)
                    apply_weave_scores(session)
                    refresh_cross_panel_qa(session)
            else:
                # final
                primary_job = str((panel.get("final") or {}).get("job_id") or "")
                is_primary = (
                    (jid and jid == primary_job)
                    or (not primary_job and seed_index in (None, 0))
                    or (
                        not (panel.get("final") or {}).get("image_id")
                        and seed_index in (None, 0)
                    )
                )
                if is_primary:
                    prev = dict(panel.get("final") or {})
                    prev["image_id"] = image_id
                    prev["pending"] = False
                    if jid:
                        prev["job_id"] = jid
                    panel["final"] = prev
                else:
                    alts = list(panel.get("final_alts") or [])
                    alts.append({
                        "job_id": jid or None,
                        "image_id": image_id,
                        "seed_index": seed_index,
                        "pending": False,
                    })
                    panel["final_alts"] = alts[-6:]
            break
        if kind == "final":
            finals = [
                str((p.get("final") or {}).get("image_id") or "")
                for p in (session.get("panels") or [])
            ]
            ready = (
                len(finals) >= 3
                and all(f and not f.startswith(("pending:", "placeholder:")) for f in finals)
            )
            if ready:
                # Finals done → back to lookdev so CTA can offer Seal.
                session["status"] = "lookdev"
                session.setdefault("cross_panel_qa", {})["finals_ready"] = True
    await save_session(db, session_id, session)
    try:
        from .events import publish

        publish(session_id, {
            "type": "render_attached",
            "kind": kind,
            "target": target,
            "image_id": image_id,
            "job_id": job_id or None,
            "seed_index": seed_index,
        })
    except Exception:
        logger.debug("weave SSE publish failed", exc_info=True)
