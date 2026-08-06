"""GEN-lane jobs for Muse image board and final shoot."""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from . import events, identity, session_db
from .runtime import render_settings

logger = logging.getLogger(__name__)


def _negative_for(session: dict[str, Any]) -> str:
    inputs = session.get("inputs") or {}
    tags = [
        str(t) for t in ((session.get("character") or {}).get("identity_tags") or [])
        if str(t).strip()
    ]
    return identity.merge_negative(
        str(inputs.get("negative_prompt") or ""),
        identity.opposing_negative(tags),
        identity.framing_negative(str(inputs.get("framing") or "auto")),
    )


def preview_publisher(session_id: str, label: str):
    async def _publish(jpeg: bytes) -> None:
        events.publish(session_id, {
            "type": "preview", "label": label,
            "image": base64.b64encode(jpeg).decode(),
        })
    return _publish


def finished_image(shas: list[str]) -> str:
    return shas[-1]


async def run_board_job(reporter, cancel, *, db, comfy, session_id: str) -> dict[str, Any]:
    from ..jobs.render import run_render
    from ..scanner.drafts import PLAYGROUND_SUBDIR

    session = await session_db.load(db, session_id)
    if session is None:
        raise RuntimeError("session is gone")
    inputs = session.get("inputs") or {}
    board = session.get("board") or {}

    async def _attach(sha256: str, meta: dict) -> None:
        await session_db.attach_board_image(db, session_id, sha256, meta)

    error = ""
    try:
        return await run_render(
            reporter, cancel,
            db=db, comfy=comfy,
            workflow_name=str(inputs.get("workflow") or ""),
            positive=str(board.get("prompt") or ""),
            negative=_negative_for(session),
            seed=int(board.get("seed") or 0) or None,
            subdir=PLAYGROUND_SUBDIR,
            # The opening still is one frame, not four: at three seats in there
            # is not enough craft for four to differ, and the point of it is to
            # get something on the wall before the crew keeps talking.
            batch_count=1 if board.get("still") else max(
                1, int(inputs.get("draft_count", 4)),
            ),
            prefix="muse_still" if board.get("still") else "muse_board",
            method="muse_board",
            payload_extra={
                "muse_session_id": session_id,
                "muse_stage": "still" if board.get("still") else "board",
            },
            attach=_attach,
            preview=preview_publisher(session_id, "board"),
            **render_settings(inputs, draft=True),
        )
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        await session_db.finish_board(db, session_id, error=error)


async def run_shoot_job(reporter, cancel, *, db, comfy, session_id: str) -> dict[str, Any]:
    from ..jobs.render import run_render
    from ..scanner.drafts import PLAYGROUND_SUBDIR

    session = await session_db.load(db, session_id)
    if session is None:
        raise RuntimeError("session is gone")
    inputs = session.get("inputs") or {}
    shoot = session.get("shoot") or {}

    async def _attach(sha256: str, meta: dict) -> None:
        await session_db.attach_shoot_image(db, session_id, sha256, meta)

    error = ""
    try:
        return await run_render(
            reporter, cancel,
            db=db, comfy=comfy,
            workflow_name=str(inputs.get("workflow") or ""),
            positive=str(shoot.get("prompt") or ""),
            negative=_negative_for(session),
            seed=int(shoot.get("seed") or 0) or None,
            batch_count=max(1, int(inputs.get("draft_count", 4))),
            subdir=PLAYGROUND_SUBDIR,
            prefix="muse_shoot",
            method="muse_shoot",
            payload_extra={"muse_session_id": session_id, "muse_stage": "shoot"},
            attach=_attach,
            preview=preview_publisher(session_id, "shoot"),
            **render_settings(inputs, draft=False),
        )
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        await session_db.finish_shoot(db, session_id, error=error)


# Legacy name — board job doubles as old draft for any leftover callers.
async def run_draft_job(reporter, cancel, *, db, comfy, session_id: str) -> dict[str, Any]:
    return await run_board_job(reporter, cancel, db=db, comfy=comfy, session_id=session_id)


async def run_chain_job(
    reporter, cancel, *, db, comfy, ollama, session_id: str, chain_index: int,
) -> dict[str, Any]:
    raise RuntimeError("muse refine chain removed — use chat + board + OK")
