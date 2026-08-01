"""Muse API — one route per pipeline step, plus a session event stream."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from ..spooler.models import JobLane
from . import events, service, session_db
from .catalog import build_muse_catalog
from .schema import STEPS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/muse")


class SessionCreate(BaseModel):
    theme: str = ""
    character_id: str = ""
    board_workflow: str = ""
    final_workflow: str = ""
    light_model: str = ""
    locale: str = "ja"


class InputsPatch(BaseModel):
    theme: str | None = None
    character_id: str | None = None
    board_workflow: str | None = None
    final_workflow: str | None = None
    light_model: str | None = None
    llm_provider: str | None = None
    locale: str | None = None
    negative_prompt: str | None = None
    board_width: int | None = Field(default=None, ge=256, le=2048)
    board_height: int | None = Field(default=None, ge=256, le=2048)
    board_steps: int | None = Field(default=None, ge=1, le=40)
    board_cfg: float | None = Field(default=None, ge=0.0, le=30.0)
    board_count: int | None = Field(default=None, ge=1, le=4)
    harvest_threshold: float | None = Field(default=None, ge=0.05, le=0.9)
    harvest_rerank: bool | None = None
    drop_rating_tags: bool | None = None
    drop_character_tags: bool | None = None
    llm_cleanup: bool | None = None
    character_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    merge_common_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    merge_unique_count: int | None = Field(default=None, ge=1, le=100)
    compose_tag_count: int | None = Field(default=None, ge=8, le=60)
    must_tags: list[str] | None = None
    shot: str | None = None
    style: str | None = None
    effect: str | None = None
    vocab_supplement: bool | None = None
    topup_picks: int | None = Field(default=None, ge=0, le=15)
    topup_min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    final_seed: int | None = None


class CharacterPick(BaseModel):
    character_id: str = Field(..., min_length=1)


class TagReject(BaseModel):
    tags: list[str]
    remove: bool = False   # True → un-reject


class BrainstormRecord(BaseModel):
    markdown: str


class SceneChoice(BaseModel):
    index: int = Field(..., ge=0)


class SlotEdit(BaseModel):
    """Replace one aspect's tags outright — the user's swap affordance."""
    slot: str = Field(..., min_length=1)
    tags: list[str]


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


async def _run(request: Request, session: dict, coro) -> dict:
    try:
        session = await coro
    except service.MuseError as exc:
        raise HTTPException(400, str(exc)) from exc
    return service.view(session)


@router.get("/catalog")
async def catalog(request: Request):
    return await build_muse_catalog(request.app)


@router.get("/sessions")
async def list_sessions(request: Request, limit: int = 20):
    return {"sessions": await session_db.list_recent(_db(request), limit=limit)}


@router.post("/sessions")
async def create_session(body: SessionCreate, request: Request):
    session = await service.create_session(_db(request), body.model_dump())
    return service.view(session)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    return service.view(await _session(request, session_id))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    await session_db.delete(_db(request), session_id)
    return {"ok": True}


@router.patch("/sessions/{session_id}/inputs")
async def patch_inputs(session_id: str, body: InputsPatch, request: Request):
    session = await _session(request, session_id)
    return await _run(request, session, service.patch_inputs(
        _db(request), session, body.model_dump(exclude_none=True),
    ))


@router.post("/sessions/{session_id}/character")
async def pick_character(session_id: str, body: CharacterPick, request: Request):
    session = await _session(request, session_id)
    return await _run(request, session, service.pick_character(
        _db(request), session, body.character_id,
    ))


@router.post("/sessions/{session_id}/reject-tags")
async def reject_tags(session_id: str, body: TagReject, request: Request):
    session = await _session(request, session_id)
    return await _run(request, session, service.reject_tags(
        _db(request), session, body.tags, remove=body.remove,
    ))


@router.post("/sessions/{session_id}/slots")
async def edit_slot(session_id: str, body: SlotEdit, request: Request):
    session = await _session(request, session_id)
    return await _run(request, session, service.set_slot(
        _db(request), session, body.slot, body.tags,
    ))


@router.post("/sessions/{session_id}/compose")
async def run_compose(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(request, session, service.run_compose(
        _db(request), _llm(request, session), session,
    ))


@router.post("/sessions/{session_id}/topup")
async def run_topup(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(request, session, service.run_topup(
        _db(request), _llm(request, session), session,
    ))


@router.post("/sessions/{session_id}/board")
async def run_board(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(request, session, service.submit_board(
        _db(request), request.app.state.comfy, request.app.state.spooler, session,
    ))


@router.post("/sessions/{session_id}/harvest")
async def run_harvest(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(request, session, service.run_harvest(
        _db(request), session, _llm(request, session),
    ))


@router.post("/sessions/{session_id}/merge")
async def run_merge(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(request, session, service.run_merge(_db(request), session))


@router.post("/sessions/{session_id}/brainstorm")
async def run_brainstorm(session_id: str, request: Request):
    """Queue Inspire's brainstorm over this session's merged tags.

    The stream is Inspire's own (``/api/inspire/brainstorm/{job_id}/stream``);
    the client posts the finished markdown back to ``/brainstorm/record``. Muse
    does not re-implement the streaming — it is the same job, just fed a tag set
    instead of a set of library images.
    """
    from ..jobs.runners import run_brainstorm as brainstorm_runner

    session = await _session(request, session_id)
    tags = (session.get("merged") or {}).get("tags") or []
    if not tags:
        raise HTTPException(400, "merge the tags first")

    inputs = session.get("inputs") or {}
    board_shas = [
        slot["image_id"]
        for track in (session.get("board") or {}).values()
        for slot in track
        if slot.get("image_id")
    ]

    queue: asyncio.Queue = asyncio.Queue()
    job_id = request.app.state.spooler.submit(
        JobLane.PROMPT,
        "muse_brainstorm",
        brainstorm_runner,
        meta={"session_id": session_id},
        body_dict={
            "sha256s": board_shas,
            "extra_tags": tags[:12],
            "reference_tags": tags,
            "theme": str(inputs.get("theme") or ""),
            "lang": str(inputs.get("locale") or "ja"),
        },
        db=_db(request),
        ollama=_llm(request, session),
        event_queue=queue,
    )
    request.app.state.inspire_event_queues[job_id] = queue
    return {"job_id": job_id, "stream": f"/api/inspire/brainstorm/{job_id}/stream"}


@router.post("/sessions/{session_id}/brainstorm/record")
async def record_brainstorm(session_id: str, body: BrainstormRecord, request: Request):
    session = await _session(request, session_id)
    return await _run(request, session, service.record_brainstorm(
        _db(request), session, body.markdown,
    ))


@router.post("/sessions/{session_id}/scene")
async def choose_scene(session_id: str, body: SceneChoice, request: Request):
    session = await _session(request, session_id)
    return await _run(request, session, service.choose_scene(
        _db(request), _llm(request, session), session, body.index,
    ))


@router.post("/sessions/{session_id}/render")
async def run_render(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(request, session, service.submit_final(
        _db(request), request.app.state.comfy, request.app.state.spooler, session,
    ))


@router.get("/sessions/{session_id}/stream")
async def stream(session_id: str, request: Request):
    """Server-sent events for one session. Any event means 'refetch'."""
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

