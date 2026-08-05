"""Muse API — two steps, a choice between them, and a session event stream."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from starlette.responses import StreamingResponse

from . import events, identity, service, session_db
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


class InputsPatch(BaseModel):
    theme: str | None = None
    character_id: str | None = None
    workflow: str | None = None
    model: str | None = None
    # Vision model for B/C/D. Empty = reuse model. Prefer a vision-capable id.
    vision_model: str | None = None
    llm_provider: str | None = None
    locale: str | None = None
    negative_prompt: str | None = None
    style: str | None = None
    # Composition bias: auto | full_body | upper_body | face_closeup | from_behind
    framing: str | None = None
    width: int | None = Field(default=None, ge=256, le=2048)
    height: int | None = Field(default=None, ge=256, le=2048)
    draft_steps: int | None = Field(default=None, ge=1, le=60)
    draft_cfg: float | None = Field(default=None, ge=0.0, le=30.0)
    draft_count: int | None = Field(default=None, ge=1, le=8)
    final_steps: int | None = Field(default=None, ge=1, le=100)
    final_cfg: float | None = Field(default=None, ge=0.0, le=30.0)
    # B, C, D. Default is 2 (B+C). There is no fourth instruction.
    refine_stages: int | None = Field(default=None, ge=1, le=3)
    # Reasoning on stage A only. Better poses, roughly eight times the wait.
    think: bool | None = None
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


class CharacterPick(BaseModel):
    character_id: str = Field(..., min_length=1)


class RefineRequest(BaseModel):
    """Which drafts go on. More than one is allowed — each becomes its own chain."""
    drafts: list[int] = Field(default_factory=list)


def _db(request: Request):
    return request.app.state.db


async def _session(request: Request, session_id: str) -> dict:
    session = await session_db.load(_db(request), session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session


def _llm(request: Request, session: dict):
    """Bind the gateway to whichever provider this session chose."""
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
    return await build_muse_catalog(request.app)


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


@router.post("/sessions/{session_id}/draft")
async def run_draft(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(service.run_draft(
        _db(request), _llm(request, session),
        request.app.state.comfy, request.app.state.spooler, session,
    ))


@router.post("/sessions/{session_id}/draft/cancel")
async def cancel_draft(session_id: str, request: Request):
    """Abandon a draft that is still rendering and go back to stage A."""
    session = await _session(request, session_id)
    return await _run(service.cancel_draft(
        _db(request), request.app.state.spooler, session,
    ))


@router.post("/sessions/{session_id}/refine")
async def run_refine(session_id: str, body: RefineRequest, request: Request):
    session = await _session(request, session_id)
    return await _run(service.run_refine(
        _db(request), _llm(request, session),
        request.app.state.comfy, request.app.state.spooler, session, body.drafts,
    ))


@router.get("/sessions/{session_id}/stream")
async def stream(session_id: str, request: Request):
    """Server-sent events for one session.

    Most events just mean "refetch". Exceptions that carry their own payload:
    ``preview`` (latent JPEG), ``prompt_delta`` / ``prompt_done`` (LLM tokens
    while a stage is writing).
    """
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
