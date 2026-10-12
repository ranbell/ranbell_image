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
    # The screen's model picker; empty falls back to the admin screen's default.
    model: str = ""
    # An earlier talk about the same picture: its reading is reused, so a
    # different actress can be called in without looking at the picture again.
    reuse_talk_id: str = ""


class TalkChat(BaseModel):
    message: str
    duration_s: int | None = None


class TalkWrite(BaseModel):
    duration_s: int | None = None


class TalkPhase(BaseModel):
    phase: str


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


async def make_room(request: Request, model: str) -> None:
    """Before an idea round, let the LLM have the card (the Showrunner, 2026-10-11).

    A still often comes straight off a render, with ComfyUI's checkpoint still
    resident; the 26B then loads mostly onto the CPU and a pitch takes minutes.
    Nothing is done when the model is already on the GPU, so a talk does not
    pay for this on every reply — only at the start and on a fresh pitch.
    """
    comfy = getattr(request.app.state, "comfy", None)
    if comfy is None or not hasattr(comfy, "free_vram"):
        return
    try:
        for m in await _ollama(request).loaded():
            if model in (m.get("name"), m.get("model")):
                if int(m.get("size_vram") or 0) >= 0.8 * int(m.get("size") or 1):
                    return
    except Exception:
        pass
    await comfy.free_vram()


def _length(talk: dict, duration_s: int | None) -> None:
    """The screen's seconds picker, taken whenever it is sent with a move."""
    if duration_s in video_expand.DURATIONS:
        talk["duration_s"] = duration_s


async def _run(move):
    try:
        return video_talk.view(await move())
    except video_talk.VideoTalkError as exc:
        raise HTTPException(400, exc.message) from exc


async def _last_muse_model(db) -> str:
    """The model the most recent Muse shoot ran on — the studio's first default."""
    from . import service, session_db

    for row in await session_db.list_recent(db, limit=5, studio=service.STUDIO):
        session = await session_db.load(db, row["session_id"])
        model = str(((session or {}).get("inputs") or {}).get("model") or "")
        if model:
            return model
    return ""


@router.post("/release")
async def release(request: Request):
    """Unload every Ollama model: the prompt is about to be rendered elsewhere.

    The studio calls this when a prompt is copied and when it closes. A prompt
    pasted straight into ComfyUI never passes `ComfyUIClient.before_queue`, so
    the 26B stayed resident and ComfyUI ran out of memory (2026-10-11).
    Never fails the screen: what went wrong is returned, not raised.
    """
    try:
        return {"unloaded": await _ollama(request).unload_all()}
    except Exception as exc:
        return {"unloaded": [], "error": str(exc)}


@router.get("/source/{sha256}")
async def source(sha256: str, request: Request):
    """Who a picture's own record says is in it, to seat them by default, and
    which model to offer first: that shoot's, else the latest shoot's."""
    src = await video_talk.source_of(_db(request), sha256)
    out = {k: src[k] for k in ("session_id", "character_id", "partner_character_id")}
    out["session_model"] = str(((src.get("session") or {}).get("inputs") or {}).get("model") or "")
    try:
        out["last_muse_model"] = await _last_muse_model(_db(request))
    except Exception:
        out["last_muse_model"] = ""
    return out


@router.get("/talks")
async def list_talks(request: Request, limit: int = 30, sha256: str = ""):
    rows = await video_db.list_recent(_db(request), limit=200 if sha256 else limit)
    if sha256:
        rows = [r for r in rows if r.get("sha256") == sha256][:limit]
    return {"talks": [video_talk.summary(r) for r in rows]}


@router.get("/search")
async def search(request: Request, q: str = "", limit: int = 30):
    """Past talks and prompts containing every word of `q`."""
    if not q.strip():
        return {"talks": []}
    return {"talks": video_talk.search(await video_db.all_talks(_db(request)), q, limit=limit)}


@router.post("/talks")
async def create_talk(body: TalkCreate, request: Request):
    if body.duration_s not in video_expand.DURATIONS:
        raise HTTPException(400, "duration_s must be 5, 10 or 15")
    reuse = await video_db.load(_db(request), body.reuse_talk_id) if body.reuse_talk_id else None
    cfg = await _cfg(request)
    await make_room(request, video_talk.model_inputs(cfg, body.locale, body.model)["model"])
    return await _run(lambda: video_talk.create(
        _db(request), _ollama(request), sha=body.sha256.strip(),
        character_id=body.character_id.strip(), partner_id=body.partner_id.strip(),
        duration_s=body.duration_s, short=body.short, locale=body.locale,
        cfg=cfg, reuse=reuse, model=body.model,
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
    return await _run(lambda: video_talk.write(_db(request), _ollama(request), talk))


@router.post("/talks/{talk_id}/settle")
async def settle(talk_id: str, request: Request):
    """The button: settle the direction as the talk stands; draft the timetable."""
    talk = await _talk(request, talk_id)
    return await _run(lambda: video_talk.settle(_db(request), _ollama(request), talk))


class TalkStyle(BaseModel):
    style: str


@router.post("/talks/{talk_id}/style")
async def style(talk_id: str, body: TalkStyle, request: Request):
    """How the clip is shot: calm, flow or cuts."""
    if body.style not in video_expand.STYLES:
        raise HTTPException(400, "style must be calm, flow or cuts")
    talk = await _talk(request, talk_id)
    return await _run(lambda: video_talk.set_style(_db(request), talk, body.style))


@router.post("/talks/{talk_id}/phase")
async def phase(talk_id: str, body: TalkPhase, request: Request):
    """Back to the direction talk (the only way back; forward is `settle`)."""
    if body.phase != "direction":
        raise HTTPException(400, "phase can only go back to direction")
    talk = await _talk(request, talk_id)
    return await _run(lambda: video_talk.back_to_direction(_db(request), talk))


@router.post("/talks/{talk_id}/repitch")
async def repitch(talk_id: str, request: Request):
    talk = await _talk(request, talk_id)
    await make_room(request, str((talk.get("inputs") or {}).get("model") or ""))
    return await _run(lambda: video_talk.repitch(_db(request), _ollama(request), talk))


@router.delete("/talks/{talk_id}")
async def delete_talk(talk_id: str, request: Request):
    await video_db.delete(_db(request), talk_id)
    return {"ok": True}
