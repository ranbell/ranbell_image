"""Muse API — showrunner chat studio."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from starlette.responses import StreamingResponse

from . import crew, events, identity, service, session_db
from .catalog import build_muse_catalog
from .schema import STEPS, public_view

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/muse")


class SessionCreate(BaseModel):
    theme: str = ""
    character_id: str = ""
    workflow: str = ""
    model: str = ""
    vision_model: str = ""
    locale: str = "ja"
    crew_preset: str = "gallery"


class InputsPatch(BaseModel):
    theme: str | None = None
    character_id: str | None = None
    workflow: str | None = None
    model: str | None = None
    vision_model: str | None = None
    llm_provider: str | None = None
    locale: str | None = None
    negative_prompt: str | None = None
    style: str | None = None
    framing: str | None = None
    crew_preset: str | None = None
    crew_ids: list[str] | None = None
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    draft_steps: int | None = Field(default=None, ge=1, le=60)
    draft_cfg: float | None = Field(default=None, ge=0.0, le=30.0)
    draft_count: int | None = Field(default=None, ge=1, le=8)
    final_steps: int | None = Field(default=None, ge=1, le=100)
    final_cfg: float | None = Field(default=None, ge=0.0, le=30.0)
    think: bool | None = None
    unload_vlm: bool | None = None
    banter_mode: str | None = None
    num_ctx: int | None = Field(default=None, ge=2048, le=131072)
    wd14_threshold: float | None = Field(default=None, ge=0.05, le=0.9)
    drop_rating_tags: bool | None = None
    drop_character_tags: bool | None = None

    @field_validator("framing")
    @classmethod
    def _normalize_framing(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return identity.parse_framing(value)

    @field_validator("crew_preset")
    @classmethod
    def _preset(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in crew.PRESETS:
            raise ValueError(f"crew_preset must be one of: {', '.join(crew.PRESETS)}")
        return value

    @field_validator("banter_mode")
    @classmethod
    def _banter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        mode = str(value).strip().lower()
        if mode not in ("light", "full", "off"):
            raise ValueError("banter_mode must be one of: light, full, off")
        return mode


class CharacterPick(BaseModel):
    character_id: str = Field(..., min_length=1)


class ChatMessage(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


def _db(request: Request):
    return request.app.state.db


async def _session(request: Request, session_id: str) -> dict:
    session = await session_db.load(_db(request), session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session


def _llm(request: Request, session: dict):
    provider = str((session.get("inputs") or {}).get("llm_provider") or "ollama")
    gateway = request.app.state.ollama
    bind = getattr(gateway, "bind", None)
    return bind(provider) if bind and provider in ("ollama", "openai") else gateway


async def _run(coro) -> dict:
    try:
        return public_view(await coro)
    except service.MuseError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/catalog")
async def catalog(request: Request):
    data = await build_muse_catalog(request.app)
    data["roster"] = crew.public_roster()
    return data


@router.get("/roster")
async def roster():
    """Static roster seats. Session public_view fills Actress from the cast character."""
    return crew.public_roster()


@router.get("/sessions")
async def list_sessions(request: Request, limit: int = 20):
    return {"sessions": await session_db.list_recent(_db(request), limit=limit)}


@router.post("/sessions")
async def create_session(body: SessionCreate, request: Request):
    session = await service.create_session(_db(request), body.model_dump())
    return public_view(session)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    return public_view(await _session(request, session_id))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    await session_db.delete(_db(request), session_id)
    return {"ok": True}


@router.patch("/sessions/{session_id}/inputs")
async def patch_inputs(session_id: str, body: InputsPatch, request: Request):
    session = await _session(request, session_id)
    return await _run(service.patch_inputs(
        _db(request), session, body.model_dump(exclude_none=True),
    ))


@router.post("/sessions/{session_id}/character")
async def pick_character(session_id: str, body: CharacterPick, request: Request):
    session = await _session(request, session_id)
    return await _run(service.pick_character(_db(request), session, body.character_id))


@router.post("/sessions/{session_id}/table")
async def start_table(session_id: str, request: Request):
    """Open the table read — crew discusses before any board."""
    session = await _session(request, session_id)
    return await _run(service.start_table(
        _db(request), _llm(request, session), session,
    ))


@router.post("/sessions/{session_id}/chat")
async def post_chat(session_id: str, body: ChatMessage, request: Request):
    """Showrunner message — note, board, or OK."""
    session = await _session(request, session_id)
    return await _run(service.post_chat(
        _db(request), _llm(request, session),
        request.app.state.comfy, request.app.state.spooler, session, body.text,
    ))


@router.post("/sessions/{session_id}/board")
async def request_board(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(service.request_board(
        _db(request), request.app.state.comfy, request.app.state.spooler, session,
        ollama=_llm(request, session),
    ))


@router.post("/sessions/{session_id}/approve")
async def approve(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(service.approve_and_shoot(
        _db(request), request.app.state.comfy, request.app.state.spooler, session,
        ollama=_llm(request, session),
    ))


# Legacy aliases
@router.post("/sessions/{session_id}/draft")
async def run_draft(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(service.run_draft(
        _db(request), _llm(request, session),
        request.app.state.comfy, request.app.state.spooler, session,
    ))


@router.post("/sessions/{session_id}/draft/cancel")
async def cancel_draft(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(service.cancel_draft(
        _db(request), request.app.state.spooler, session,
    ))


@router.post("/sessions/{session_id}/refine")
async def run_refine(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(service.run_refine(
        _db(request), _llm(request, session),
        request.app.state.comfy, request.app.state.spooler, session, [],
    ))


@router.get("/sessions/{session_id}/stream")
async def stream(session_id: str, request: Request):
    async def _gen():
        queue = await events.subscribe(session_id)
        try:
            yield 'data: {"type":"hello"}\n\n'
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            await events.unsubscribe(session_id, queue)

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@router.get("/steps")
async def list_steps():
    return {"steps": list(STEPS)}
