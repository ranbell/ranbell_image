"""HTTP API for the video studio (`video_talk`). Talks are their own records."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import video_db, video_expand, video_talk

router = APIRouter(prefix="/api/muse/video", tags=["muse-video"])


class TalkCreate(BaseModel):
    sha256: str
    character_id: str
    partner_id: str = ""
    duration_s: int = 5
    short: str = ""
    locale: str = "ja"
    # An earlier talk about the same picture: its reading is reused, so a
    # different actress can be called in without looking at the picture again.
    reuse_talk_id: str = ""


class TalkChat(BaseModel):
    message: str
    duration_s: int | None = None


class TalkWrite(BaseModel):
    kind: str
    duration_s: int | None = None


def _db(request: Request):
    return request.app.state.db


def _ollama(request: Request):
    return request.app.state.ollama


async def _cfg(request: Request) -> dict:
    from ..runtime_config import get_runtime_config

    try:
        return await get_runtime_config(_db(request))
    except Exception:
        return {}


async def _talk(request: Request, talk_id: str) -> dict:
    talk = await video_db.load(_db(request), talk_id)
    if talk is None:
        raise HTTPException(404, "talk not found")
    return talk


def _length(talk: dict, duration_s: int | None) -> None:
    """The screen's seconds picker, taken whenever it is sent with a move."""
    if duration_s in video_expand.DURATIONS:
        talk["duration_s"] = duration_s


async def _run(move):
    try:
        return video_talk.view(await move())
    except video_talk.VideoTalkError as exc:
        raise HTTPException(400, exc.message) from exc


@router.get("/source/{sha256}")
async def source(sha256: str, request: Request):
    """Who a picture's own record says is in it, to seat them by default."""
    src = await video_talk.source_of(_db(request), sha256)
    return {k: src[k] for k in ("session_id", "character_id", "partner_character_id")}


@router.get("/talks")
async def list_talks(request: Request, limit: int = 30, sha256: str = ""):
    rows = await video_db.list_recent(_db(request), limit=200 if sha256 else limit)
    if sha256:
        rows = [r for r in rows if r.get("sha256") == sha256][:limit]
    return {"talks": [video_talk.summary(r) for r in rows]}


@router.post("/talks")
async def create_talk(body: TalkCreate, request: Request):
    if body.duration_s not in video_expand.DURATIONS:
        raise HTTPException(400, "duration_s must be 5, 10 or 15")
    reuse = await video_db.load(_db(request), body.reuse_talk_id) if body.reuse_talk_id else None
    cfg = await _cfg(request)
    return await _run(lambda: video_talk.create(
        _db(request), _ollama(request), sha=body.sha256.strip(),
        character_id=body.character_id.strip(), partner_id=body.partner_id.strip(),
        duration_s=body.duration_s, short=body.short, locale=body.locale,
        cfg=cfg, reuse=reuse,
    ))


@router.get("/talks/{talk_id}")
async def get_talk(talk_id: str, request: Request):
    return video_talk.view(await _talk(request, talk_id))


@router.post("/talks/{talk_id}/chat")
async def chat(talk_id: str, body: TalkChat, request: Request):
    talk = await _talk(request, talk_id)
    if not body.message.strip():
        raise HTTPException(400, "message is empty")
    _length(talk, body.duration_s)
    return await _run(lambda: video_talk.chat(
        _db(request), _ollama(request), talk, body.message.strip(),
    ))


@router.post("/talks/{talk_id}/write")
async def write(talk_id: str, body: TalkWrite, request: Request):
    talk = await _talk(request, talk_id)
    _length(talk, body.duration_s)
    return await _run(lambda: video_talk.write(
        _db(request), _ollama(request), talk, body.kind.strip(),
    ))


@router.post("/talks/{talk_id}/repitch")
async def repitch(talk_id: str, request: Request):
    talk = await _talk(request, talk_id)
    return await _run(lambda: video_talk.repitch(_db(request), _ollama(request), talk))


@router.delete("/talks/{talk_id}")
async def delete_talk(talk_id: str, request: Request):
    await video_db.delete(_db(request), talk_id)
    return {"ok": True}
