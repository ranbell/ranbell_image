"""GEN-lane jobs for Muse image board and final shoot."""
from __future__ import annotations

import base64
import logging
import time
from typing import Any

from . import events, session_db
from .runtime import negative_for, render_settings

logger = logging.getLogger(__name__)


def preview_publisher(session_id: str, label: str):
    async def _publish(jpeg: bytes) -> None:
        events.publish(session_id, {
            "type": "preview", "label": label,
            "image": base64.b64encode(jpeg).decode(),
        })
    return _publish


def finished_image(shas: list[str]) -> str:
    return shas[-1]


def direction_still_bytes(session: dict[str, Any]) -> bytes | None:
    """Latest coaching still saved on the session (JPEG bytes), if any."""
    still = session.get("direction_still") or {}
    if not isinstance(still, dict):
        return None
    b64 = str(still.get("jpeg_b64") or "").strip()
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception:
        return None
    return raw if len(raw) >= 32 else None


def store_direction_still(session: dict[str, Any], jpeg: bytes) -> None:
    """Keep the latest direction still for OpenPose injection on board/shoot."""
    if not jpeg:
        return
    session["direction_still"] = {
        "jpeg_b64": base64.b64encode(jpeg).decode(),
        "at": time.time(),
        "bytes": len(jpeg),
    }


def _character_payload_extra(session: dict) -> dict[str, Any]:
    """Who was cast, snapshotted onto every image she appears in.

    Presets can be renamed later; this keeps the record honest about who she
    was called at the time, and lets the image find its way back to her
    without a session lookup.
    """
    extra: dict[str, Any] = {}
    lead = session.get("character") or {}
    if lead.get("character_id"):
        extra["character_id"] = lead["character_id"]
        extra["character_name"] = lead.get("name_ja") or lead.get("name") or ""
    partner = session.get("partner_character") or {}
    if partner.get("character_id"):
        extra["partner_character_id"] = partner["character_id"]
        extra["partner_character_name"] = partner.get("name_ja") or partner.get("name") or ""
    return extra


async def run_board_job(reporter, cancel, *, db, comfy, session_id: str) -> dict[str, Any]:
    from ..jobs.render import run_render
    from ..scanner.drafts import PLAYGROUND_SUBDIR

    session = await session_db.load(db, session_id)
    if session is None:
        raise RuntimeError("session is gone")
    inputs = session.get("inputs") or {}
    board = session.get("board") or {}
    ref = direction_still_bytes(session)

    async def _attach(sha256: str, meta: dict) -> None:
        await session_db.attach_board_image(db, session_id, sha256, meta)

    error = ""
    try:
        return await run_render(
            reporter, cancel,
            db=db, comfy=comfy,
            workflow_name=str(inputs.get("workflow") or ""),
            positive=str(board.get("prompt") or ""),
            negative=negative_for(session),
            seed=int(board.get("seed") or 0) or None,
            subdir=PLAYGROUND_SUBDIR,
            # The opening still is one frame, not four: at three seats in there
            # is not enough craft for four to differ, and the point of it is to
            # get something on the wall before the crew keeps talking.
            batch_count=1 if board.get("still") else max(
                1, int(inputs.get("draft_count", 1)),
            ),
            prefix="muse_still" if board.get("still") else "muse_board",
            method="muse_board",
            payload_extra={
                "muse_session_id": session_id,
                "muse_stage": "still" if board.get("still") else "board",
                **_character_payload_extra(session),
            },
            attach=_attach,
            preview=preview_publisher(session_id, "board"),
            reference_image=ref,
            **render_settings(inputs, draft=True),
        )
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        await session_db.finish_board(db, session_id, error=error)


async def run_shoot_job(
    reporter, cancel, *, db, comfy, session_id: str, ollama=None,
) -> dict[str, Any]:
    from ..jobs.render import run_render
    from ..scanner.drafts import PLAYGROUND_SUBDIR

    session = await session_db.load(db, session_id)
    if session is None:
        raise RuntimeError("session is gone")
    inputs = session.get("inputs") or {}
    shoot = session.get("shoot") or {}
    ref = direction_still_bytes(session)

    async def _attach(sha256: str, meta: dict) -> None:
        await session_db.attach_shoot_image(db, session_id, sha256, meta)

    error = ""
    try:
        return await run_render(
            reporter, cancel,
            db=db, comfy=comfy,
            workflow_name=str(inputs.get("workflow") or ""),
            positive=str(shoot.get("prompt") or ""),
            negative=negative_for(session),
            seed=int(shoot.get("seed") or 0) or None,
            batch_count=max(1, int(inputs.get("draft_count", 1))),
            subdir=PLAYGROUND_SUBDIR,
            prefix="muse_shoot",
            method="muse_shoot",
            payload_extra={
                "muse_session_id": session_id,
                "muse_stage": "shoot",
                **_character_payload_extra(session),
            },
            attach=_attach,
            preview=preview_publisher(session_id, "shoot"),
            reference_image=ref,
            **render_settings(inputs, draft=False),
        )
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        await session_db.finish_shoot(db, session_id, error=error, ollama=ollama)


