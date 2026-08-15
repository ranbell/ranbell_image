"""Muse API — showrunner chat studio."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from starlette.responses import StreamingResponse

from . import crew, events, identity, report, service, session_db
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
    crew_preset: str = "standard"
    # Named look — see `crew.LOOKS`. Settable here and mid-session.
    look: str = ""
    # 主演撮り (lead shoot) — one or two Muses and the Showrunner, no table
    # read. It is what people open the studio to do, so it is the default; a
    # client that wants the crewed floor sends `mode: ""` explicitly.
    mode: str = "duet"
    partner_preset: str | None = None


class InputsPatch(BaseModel):
    theme: str | None = None
    # "" is the crewed studio, "duet" is 主演撮り. Settable before the session
    # opens so the panel can hide the casting drawer.
    mode: str | None = None
    character_id: str | None = None
    partner_preset: str | None = None
    workflow: str | None = None
    model: str | None = None
    vision_model: str | None = None
    locale: str | None = None
    negative_prompt: str | None = None
    style: str | None = None
    look: str | None = None
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

    @field_validator("mode")
    @classmethod
    def _mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        mode = str(value).strip().lower()
        if mode not in ("", "duet"):
            raise ValueError('mode must be "" or "duet"')
        return mode

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


class PartnerPick(BaseModel):
    # "" clears the partner, so unlike the lead this one may be empty.
    partner_preset: str = ""


class ChatMessage(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    # Optional direction stills (base64 JPEG/PNG, max 1). Used while pose
    # coaching / direction is ON — not persisted to the board.
    images: list[str] | None = Field(default=None, max_length=1)


class FacetPatch(BaseModel):
    """Pinning a part of the shot. Editing its tags directly is not offered —
    the feel of the app is ひとこと / OK / リテイク, and a text box full of
    danbooru tags is the sliders-everywhere director's chair this app is not."""
    locked: bool


def _db(request: Request):
    return request.app.state.db


async def _session(request: Request, session_id: str) -> dict:
    session = await session_db.load(_db(request), session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session


def _llm(request: Request, session: dict):
    return request.app.state.ollama


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


@router.patch("/sessions/{session_id}/facets/{facet}")
async def patch_facet(
    session_id: str, facet: str, body: FacetPatch, request: Request,
):
    """Pin one part of the shot, or let it move again.

    A locked part is never rewritten by any turn — it is the Showrunner saying
    "not this one" about something the crew keeps reaching for. Refusals still
    outrank it: a pin means "do not rewrite", not "keep something I took out".
    """
    session = await _session(request, session_id)
    return await _run(service.set_facet_lock(
        _db(request), session, facet, bool(body.locked),
    ))


@router.post("/sessions/{session_id}/character")
async def pick_character(session_id: str, body: CharacterPick, request: Request):
    session = await _session(request, session_id)
    return await _run(service.pick_character(_db(request), session, body.character_id))


@router.post("/sessions/{session_id}/partner")
async def pick_partner(session_id: str, body: PartnerPick, request: Request):
    """Cast the second Muse in 主演撮り, or clear her with an empty id.

    Separate from the inputs patch because casting resolves the character then
    and there: storing the id alone left the panel showing "no partner" until
    she spoke, which read as the pick having failed.
    """
    session = await _session(request, session_id)
    return await _run(service.pick_partner(_db(request), session, body.partner_preset or ""))


@router.post("/sessions/{session_id}/table")
async def start_table(session_id: str, request: Request):
    """Open the table read: three seats rough it in, then a still goes up."""
    session = await _session(request, session_id)
    return await _run(service.start_table(
        _db(request), _llm(request, session), session,
        comfy=request.app.state.comfy, spooler=request.app.state.spooler,
    ))


@router.post("/sessions/{session_id}/duet")
async def start_duet(session_id: str, request: Request):
    """主演撮り — no crew, no table. One or two Muses open, and you work it out."""
    session = await _session(request, session_id)
    return await _run(service.start_duet(
        _db(request), _llm(request, session), session,
    ))


@router.post("/sessions/{session_id}/duet/prep")
async def duet_prep(session_id: str, request: Request):
    """The explicit "①撮影準備" button — builds the shot from notes said so far."""
    session = await _session(request, session_id)
    return await _run(service.duet_prep_stage(
        _db(request), _llm(request, session), session,
    ))


@router.post("/sessions/{session_id}/chat")
async def post_chat(session_id: str, body: ChatMessage, request: Request):
    """Showrunner message — always creative direction, never a stage trigger."""
    session = await _session(request, session_id)
    return await _run(service.post_chat(
        _db(request), _llm(request, session),
        request.app.state.comfy, request.app.state.spooler, session, body.text,
        images=body.images,
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


@router.post("/sessions/{session_id}/finish")
async def finish_session(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(service.finish_session(
        _db(request), request.app.state.spooler, session, ollama=_llm(request, session)
    ))


@router.post("/sessions/{session_id}/board/cancel")
async def cancel_board(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _run(service.cancel_board(
        _db(request), request.app.state.spooler, session,
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


@router.get("/report")
async def crew_report(request: Request, limit: int = 40):
    """Which seats keep their work across recent sessions, and what they cost.

    The retire/merge decision. One session is an anecdote — a seat can survive
    at 0% because it had a bad round — so this walks the recent ones and sums.
    """
    db = _db(request)
    rows = await session_db.list_recent(db, limit=max(1, min(int(limit), 200)))
    sessions = []
    for row in rows:
        loaded = await session_db.load(db, row["session_id"])
        if loaded is not None:
            sessions.append(loaded)
    return report.aggregate(sessions)


@router.get("/sessions/{session_id}/report")
async def session_report(session_id: str, request: Request):
    """One session's seats: what each added, what survived, what it cost."""
    return report.session_report(await _session(request, session_id))


@router.get("/steps")
async def list_steps():
    return {"steps": list(STEPS)}


# ── Lounge + studio handpost ─────────────────────────────────────────────────

@router.get("/lounge/threads")
async def lounge_threads(request: Request, limit: int = 40, kind: str = ""):
    from . import lounge_db
    rows = await lounge_db.list_threads(_db(request), limit=limit, kind=kind)
    return {"threads": rows}


@router.get("/lounge/threads/{thread_id}")
async def lounge_thread(thread_id: str, request: Request):
    from . import lounge_db
    row = await lounge_db.get_thread(_db(request), thread_id)
    if row is None:
        raise HTTPException(404, "thread not found")
    return row


@router.get("/lounge/trends")
async def lounge_trends(request: Request):
    from . import lounge_db
    return {"trends": await lounge_db.get_trends(_db(request))}


class LikeBody(BaseModel):
    liked: bool | None = None


@router.post("/lounge/threads/{thread_id}/like")
async def lounge_like(thread_id: str, request: Request, body: LikeBody = LikeBody()):
    """Toggle or set liked on a pitch (or any lounge thread)."""
    from . import lounge_db
    liked = body.liked
    row = await lounge_db.set_thread_liked(_db(request), thread_id, liked)
    if row is None:
        raise HTTPException(404, "thread not found")
    return row


@router.get("/lounge/summary")
async def lounge_summary(request: Request, since: float = 0.0):
    """Gallery badge: new threads since last peek + unanswered pitches."""
    from . import lounge_db
    return await lounge_db.summary(_db(request), since=since)


@router.get("/handpost")
async def handpost_list(request: Request, pinned_only: bool = False):
    """Read-only list. Pages are written by habit jobs — not by the showrunner."""
    from . import handpost_db
    return {"pages": await handpost_db.list_pages(_db(request), pinned_only=pinned_only)}
