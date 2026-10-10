"""HTTP API for Muse Refine — fully separate from /api/muse."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from . import events
from .catalog import build_muse_catalog
from . import service
from . import session_db, video_expand, video_talk

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/muse", tags=["muse"])


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
    # **The crew preset (the missing field was added on 2026-09-13).**
    #
    # The screen had offered the choice for a long time (`muse.crewPreset`), and with
    # no field here pydantic silently threw it away — `{"crew_preset": "photoreal"}`
    # became `{}` and **every studio shoot ran on `standard`'s 18 seats**. The
    # Showrunner: "I hope photorealistic, vivid, flat and the rest work well.
    crew_preset: str | None = None
    #: Banter (off / light / full). This too had long been on the screen with no
    #: field to receive it — half the calls are banter, so **one lever on speed was
    #: blocked**.
    banter_mode: str | None = None
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


class VideoStartBody(BaseModel):
    sha256: str
    short: str = ""
    duration_s: int = 5


class VideoChatBody(BaseModel):
    message: str
    duration_s: int | None = None


class VideoWriteBody(BaseModel):
    kind: str
    duration_s: int | None = None


def _video_length(session: dict, duration_s: int | None) -> None:
    """The panel's seconds picker, taken whenever it is sent with a move."""
    if duration_s in video_expand.DURATIONS and session.get("video"):
        session["video"]["duration_s"] = duration_s


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
    """The catalogue, plus the crews available for a studio shoot.

    `crew.PRESETS` is the document of record for the crew presets. Writing them
    into the panel instead means they drift silently the day a seat is added.
    """
    from . import crew

    out = await build_muse_catalog(request.app)
    out["crew"] = {
        "presets": list(crew.PRESETS),
        "roles": [
            {
                "id": rid,
                "name": str(crew.ROLES[rid].get("name") or ""),
                "name_ja": str(crew.ROLES[rid].get("name_ja") or ""),
                "slot": (getattr(crew, "CRAFT_SLOTS", None) or {}).get(rid, ""),
            }
            for rid in crew.ROLE_ORDER
        ],
        "muses": [
            {
                "id": mid,
                "nick": str(m.get("nick") or ""),
                "nick_ja": str(m.get("nick_ja") or ""),
                "name": str(crew.ROLES.get(str(m.get("role_id") or ""), {}).get("name") or m.get("name") or ""),
                "name_ja": str(m.get("name_ja") or ""),
            }
            for mid, m in crew.MUSES.items()
        ],
    }
    return out


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
    from . import session_db
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
    """Open the table (studio shoot). The three opening seats rough the shot in.

    **An explicit door.** `inputs.crew_preset` defaults to `"standard"`, so
    deciding by "are there seats" would run sixteen of them in a solo shoot.
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
        from . import session_db
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
    from . import session_db
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


def _video_error(session: dict, status: int, ja: str, en: str) -> HTTPException:
    locale = str((session.get("inputs") or {}).get("locale") or "ja")
    return HTTPException(status, ja if locale.startswith("ja") else en)


async def _video(request: Request, session: dict, move):
    try:
        session = await move()
    except video_talk.VideoTalkError as exc:
        raise HTTPException(400, exc.message) from exc
    return service.public_view(session)


@router.get("/video/source/{sha256}")
async def video_source(sha256: str, request: Request):
    """Which Muse session a picture came out of, and who is in it.

    The gallery's "make a video" opens that session to carry on from the shoot;
    a picture with no session starts a new one with an actress to talk it over.
    """
    docs = await _db(request).get_by_sha256s([sha256])
    meta = docs[0] if docs else {}
    sid = str(meta.get("muse_session_id") or "")
    if sid and await session_db.load(_db(request), sid) is None:
        sid = ""
    return {
        "session_id": sid,
        "character_id": str(meta.get("character_id") or ""),
        "partner_character_id": str(meta.get("partner_character_id") or ""),
    }


@router.post("/sessions/{session_id}/video/start")
async def video_start(session_id: str, body: VideoStartBody, request: Request):
    """Bring a picture to the table and talk it into a video with the actress."""
    session = await _session(request, session_id)
    if body.duration_s not in video_expand.DURATIONS:
        raise _video_error(session, 400, "秒数は 5、10、15 です", "Length is 5, 10 or 15 seconds")
    if not (session.get("character") or {}).get("character_id"):
        raise _video_error(session, 400, "女優を選んでください", "Pick an actress first")
    return await _video(request, session, lambda: video_talk.start(
        _db(request), _ollama(request), session,
        sha=body.sha256.strip(), short=body.short, duration_s=body.duration_s,
    ))


@router.post("/sessions/{session_id}/video/chat")
async def video_chat(session_id: str, body: VideoChatBody, request: Request):
    session = await _session(request, session_id)
    if not body.message.strip():
        raise _video_error(session, 400, "ひとこと書いてください", "Say something first")
    _video_length(session, body.duration_s)
    return await _video(request, session, lambda: video_talk.chat(
        _db(request), _ollama(request), session, body.message.strip(),
    ))


@router.post("/sessions/{session_id}/video/write")
async def video_write(session_id: str, body: VideoWriteBody, request: Request):
    session = await _session(request, session_id)
    _video_length(session, body.duration_s)
    return await _video(request, session, lambda: video_talk.write(
        _db(request), _ollama(request), session, body.kind.strip(),
    ))


@router.post("/sessions/{session_id}/video/stop")
async def video_stop(session_id: str, request: Request):
    session = await _session(request, session_id)
    return await _video(request, session, lambda: video_talk.stop(_db(request), session))


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
