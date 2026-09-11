"""HTTP API for Muse Refine — fully separate from /api/muse."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from ..muse import events
from ..muse.catalog import build_muse_catalog
from . import service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/muse-refine", tags=["muse-refine"])


class SessionCreate(BaseModel):
    theme: str = ""
    character_id: str = ""
    partner_preset: str = ""
    workflow: str = ""
    model: str = ""
    locale: str = "ja"
    enhance_quality: bool = False


class InputsPatch(BaseModel):
    theme: str | None = None
    character_id: str | None = None
    partner_preset: str | None = None
    workflow: str | None = None
    model: str | None = None
    locale: str | None = None
    style: str | None = None
    framing: str | None = None
    negative_prompt: str | None = None
    draft_count: int | None = Field(default=None, ge=1, le=8)
    draft_steps: int | None = Field(default=None, ge=1, le=60)
    final_steps: int | None = Field(default=None, ge=1, le=100)
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    enhance_quality: bool | None = None


class CharacterPick(BaseModel):
    character_id: str


class PartnerPick(BaseModel):
    partner_preset: str = ""


class ChatBody(BaseModel):
    message: str


class StandingBody(BaseModel):
    standing: list[str] = Field(default_factory=list)


def _db(request: Request):
    return request.app.state.db


async def _session(request: Request, session_id: str) -> dict:
    try:
        return await service.load_refine(_db(request), session_id)
    except service.RefineError as exc:
        raise HTTPException(404, exc.message) from exc


def _ollama(request: Request):
    return request.app.state.ollama


@router.get("/catalog")
async def catalog(request: Request):
    return await build_muse_catalog(request.app)


@router.get("/sessions")
async def list_sessions(request: Request, limit: int = 20):
    return {"sessions": await service.list_refine_sessions(_db(request), limit=limit)}


@router.post("/sessions")
async def create_session(body: SessionCreate, request: Request):
    session = await service.create_session(_db(request), body.model_dump())
    if body.character_id:
        try:
            session = await service.pick_character(
                _db(request), session, body.character_id,
            )
        except service.RefineError as exc:
            raise HTTPException(400, exc.message) from exc
    if body.partner_preset:
        try:
            session = await service.pick_partner(
                _db(request), session, body.partner_preset,
            )
        except service.RefineError as exc:
            raise HTTPException(400, exc.message) from exc
    return service.public_view(session)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    return service.public_view(await _session(request, session_id))


@router.get("/sessions/{session_id}/pipeline")
async def session_pipeline(session_id: str, request: Request):
    """Debug / eval: classify-style stage summary for this refine session.

    Same payload as ``public_view.pipeline`` — exposed as its own route so
    external tools (and Muse-style debug clients) can hit it without the full
    session body.
    """
    from . import pipeline_view
    session = await _session(request, session_id)
    return pipeline_view.build_pipeline_view(session)


@router.get("/sessions/{session_id}/debug")
async def session_debug(session_id: str, request: Request):
    """Debug bundle: pipeline + rewrite/turn/stage/refine logs only."""
    session = await _session(request, session_id)
    view = service.public_view(session)
    return {
        "session_id": view.get("session_id"),
        "status": view.get("status"),
        "pipeline": view.get("pipeline"),
        "rewrite_log": view.get("rewrite_log") or [],
        "turn_trace": view.get("turn_trace") or [],
        "stage_ms": view.get("stage_ms") or [],
        "refine_log": view.get("refine_log") or [],
        "craft": {
            "now": (view.get("craft") or {}).get("now"),
            "prompt": (view.get("craft") or {}).get("prompt"),
            "scene": (view.get("craft") or {}).get("scene"),
            "quality_tags": (view.get("craft") or {}).get("quality_tags"),
        },
        "refine_ledger": view.get("refine_ledger") or {},
    }


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    from ..muse import session_db
    await _session(request, session_id)
    await session_db.delete(_db(request), session_id)
    return {"ok": True}


@router.patch("/sessions/{session_id}/inputs")
async def patch_inputs(session_id: str, body: InputsPatch, request: Request):
    session = await _session(request, session_id)
    session = await service.patch_inputs(
        _db(request), session, body.model_dump(exclude_none=True),
    )
    return service.public_view(session)


@router.post("/sessions/{session_id}/character")
async def pick_character(session_id: str, body: CharacterPick, request: Request):
    session = await _session(request, session_id)
    try:
        session = await service.pick_character(
            _db(request), session, body.character_id,
        )
    except service.RefineError as exc:
        raise HTTPException(400, exc.message) from exc
    return service.public_view(session)


@router.post("/sessions/{session_id}/partner")
async def pick_partner(session_id: str, body: PartnerPick, request: Request):
    session = await _session(request, session_id)
    try:
        session = await service.pick_partner(
            _db(request), session, body.partner_preset,
        )
    except service.RefineError as exc:
        raise HTTPException(400, exc.message) from exc
    return service.public_view(session)


class RestoreBody(BaseModel):
    tag: str


class RestateBody(BaseModel):
    field: str


@router.post("/sessions/{session_id}/open")
async def open_session(session_id: str, request: Request):
    """Opening beat — she speaks first (theme / reunion / dress)."""
    session = await _session(request, session_id)
    try:
        session = await service.open_session(
            _db(request), _ollama(request), session,
        )
    except service.RefineError as exc:
        raise HTTPException(400, exc.message) from exc
    return service.public_view(session)


@router.post("/sessions/{session_id}/table")
async def open_table(session_id: str, request: Request):
    """班を開く（スタジオ撮り）。開幕の三席が当たりを付ける。

    **明示的な開扉**。`inputs.crew_preset` は既定で `"standard"` が入るので、
    席の有無で判断すると一人撮りでも16席が回ってしまう。
    """
    session = await _session(request, session_id)
    try:
        session = await service.open_table(
            _db(request), _ollama(request), session,
        )
    except service.RefineError as exc:
        raise HTTPException(400, exc.message) from exc
    return service.public_view(session)


@router.post("/sessions/{session_id}/banned/restore")
async def restore_banned(session_id: str, body: RestoreBody, request: Request):
    session = await _session(request, session_id)
    try:
        session = service.restore_banned(session, body.tag)
        from ..muse import session_db
        from . import assemble
        session = await assemble.rebuild_craft(_db(request), _ollama(request), session)
        await session_db.save(_db(request), session)
    except service.RefineError as exc:
        raise HTTPException(400, exc.message) from exc
    return service.public_view(session)


@router.post("/sessions/{session_id}/restate")
async def restate(session_id: str, body: RestateBody, request: Request):
    session = await _session(request, session_id)
    try:
        session = await service.restate_field(
            _db(request), _ollama(request), session, body.field,
        )
    except service.RefineError as exc:
        raise HTTPException(400, exc.message) from exc
    return service.public_view(session)


@router.put("/sessions/{session_id}/standing")
async def put_standing(session_id: str, body: StandingBody, request: Request):
    session = await _session(request, session_id)
    service.set_standing(session, body.standing)
    from ..muse import session_db
    await session_db.save(_db(request), session)
    return service.public_view(session)


@router.post("/sessions/{session_id}/chat")
async def chat(session_id: str, body: ChatBody, request: Request):
    session = await _session(request, session_id)
    try:
        session = await service.chat(
            _db(request), _ollama(request), session, body.message,
        )
    except service.RefineError as exc:
        raise HTTPException(400, exc.message) from exc
    return service.public_view(session)


@router.post("/sessions/{session_id}/rebuild")
async def rebuild(session_id: str, request: Request):
    session = await _session(request, session_id)
    session = await service.rebuild(_db(request), _ollama(request), session)
    return service.public_view(session)


@router.post("/sessions/{session_id}/board")
async def board(session_id: str, request: Request):
    session = await _session(request, session_id)
    try:
        session = await service.start_board(_db(request), request, session)
    except service.RefineError as exc:
        raise HTTPException(400, exc.message) from exc
    return service.public_view(session)


@router.post("/sessions/{session_id}/approve")
@router.post("/sessions/{session_id}/shoot")
async def approve_or_shoot(session_id: str, request: Request):
    """Board OK → final. Same gate whether called approve or shoot."""
    session = await _session(request, session_id)
    try:
        session = await service.start_shoot(_db(request), request, session)
    except service.RefineError as exc:
        raise HTTPException(400, exc.message) from exc
    return service.public_view(session)


@router.post("/sessions/{session_id}/finish")
async def finish(session_id: str, request: Request):
    session = await _session(request, session_id)
    try:
        session = await service.finish_session(_db(request), request, session)
    except service.RefineError as exc:
        raise HTTPException(400, exc.message) from exc
    return service.public_view(session)


@router.get("/sessions/{session_id}/stream")
async def stream(session_id: str, request: Request):
    await _session(request, session_id)
    queue = await events.subscribe(session_id)

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=25.0)
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
                    continue
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        finally:
            await events.unsubscribe(session_id, queue)

    return StreamingResponse(gen(), media_type="text/event-stream")
