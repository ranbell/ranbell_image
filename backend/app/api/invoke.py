"""Invoke (召喚) API endpoints."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/invoke", tags=["invoke"])


# ── Request / Response models ──────────────────────────────────────────────────

class SummonRequest(BaseModel):
    # Light mode inputs
    user_intent: str = ""
    emoji_codes: list[str] = []
    mood_sliders: dict = {}   # {warm_cool, calm_dynamic, dense_sparse, concrete_abstract} -2..2
    color_hex: list[str] = []
    # Pro mode inputs
    pro_prompt: str = ""
    pro_negative: str = ""
    pro_person_tags: str = ""  # free-form character tags for Pro mode (prepended to all spirits)
    seeds: dict = {}  # spirit_name -> int | null
    # Character specification
    person_gender: str = ""  # '' | 'girl' | 'boy'
    person_count: str = ""   # '' | '1' | '2' | '3+'
    # Prompt assembly mode
    prompt_mode: str = "danbooru+natural"  # 'danbooru+natural' | 'natural' | 'danbooru'
    # Camera work (light mode)
    camera_shot: str = ""   # e.g. "full_body", "cowboy_shot", "close_up"
    camera_angle: str = ""  # e.g. "from_above", "dutch_angle"
    # Locale
    locale: str = "en"      # 'en' | 'ja' — controls monologue language
    # Common
    workflow_name: str = ""
    input_mode: str = "light"  # light | pro
    enabled_spirits: list[str] = ["faithful", "rebel", "stranger", "lunatic", "oracle"]


class RespinRequest(BaseModel):
    session_id: str
    spirit_name: str


class AdoptRequest(BaseModel):
    session_id: str
    spirit_name: str


class SendToRefineRequest(BaseModel):
    session_id: str
    spirit_name: str
    workflow_name: str = ""


class DailyOracleRequest(BaseModel):
    workflow_name: str = ""


class CancelRequest(BaseModel):
    session_id: str


class EnhancePromptRequest(BaseModel):
    text: str
    tag_count: int = 25


# ── Helpers ───────────────────────────────────────────────────────────────────

def _oracle_tz(cfg: dict) -> ZoneInfo:
    tz_name = (cfg.get("invoke_daily_oracle_timezone") or "UTC").strip()
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, Exception):
        return ZoneInfo("UTC")


def _oracle_hm(cfg: dict) -> tuple[int, int]:
    t = (cfg.get("invoke_daily_oracle_time") or "00:00").strip()
    try:
        h, m = map(int, t.split(":"))
        return max(0, min(23, h)), max(0, min(59, m))
    except Exception:
        return 0, 0


def _oracle_date_str(cfg: dict) -> str:
    """Return today's date string in the configured oracle timezone."""
    return datetime.now(_oracle_tz(cfg)).date().isoformat()


def _oracle_next_run_iso(cfg: dict) -> str:
    """Return ISO 8601 datetime of the next oracle execution."""
    tz = _oracle_tz(cfg)
    h, m = _oracle_hm(cfg)
    now = datetime.now(tz)
    run = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if run <= now:
        run += timedelta(days=1)
    return run.isoformat()


async def _sse_generator(event_queue: asyncio.Queue, queues: dict, session_id: str):
    """Yield SSE frames from an asyncio.Queue until None sentinel."""
    try:
        while True:
            item = await event_queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"
        yield "data: {\"type\": \"eof\"}\n\n"
    finally:
        queues.pop(session_id, None)


def _get_invoke_manager(request: Request):
    mgr = getattr(request.app.state, "invoke_session_manager", None)
    if mgr is None:
        raise HTTPException(503, "Invoke session manager not initialized")
    return mgr


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/summon")
async def summon(body: SummonRequest, request: Request):
    mgr = _get_invoke_manager(request)
    db = request.app.state.db
    ollama = request.app.state.ollama
    comfy = request.app.state.comfy
    spooler = request.app.state.spooler

    from ..runtime_config import get_runtime_config
    cfg = await get_runtime_config(db)
    workflow_name = body.workflow_name or cfg.get("invoke_daily_oracle_workflow", "")

    # Merge pro prompt into user_intent if pro mode
    user_intent = body.user_intent
    if body.input_mode == "pro" and body.pro_prompt:
        user_intent = body.pro_prompt

    from ..invoke.axis_decomposer import _resolve_person
    person_tags_str, _ = _resolve_person(body.person_gender, body.person_count)
    if body.input_mode == "pro" and body.pro_person_tags.strip():
        person_tags_str = body.pro_person_tags.strip()

    session = mgr.create_session(
        user_intent=user_intent,
        input_mode=body.input_mode,
        workflow_name=workflow_name,
        enabled_spirits=body.enabled_spirits,
        prompt_mode=body.prompt_mode,
        locale=body.locale,
        person_tags=person_tags_str,
        pro_negative=body.pro_negative if body.input_mode == "pro" else "",
        db=db,
        ollama=ollama,
        comfy=comfy,
        spooler=spooler,
    )

    from ..spooler.models import JobLane
    from ..jobs.runners import run_invoke_axis_decompose

    job_id = spooler.submit(
        JobLane.PROMPT,
        "invoke.axis_decompose",
        run_invoke_axis_decompose,
        meta={"session_id": session.session_id},
        db=db,
        ollama=ollama,
        spooler=spooler,
        session_id=session.session_id,
        user_intent=user_intent,
        emoji_codes=body.emoji_codes,
        mood_sliders=body.mood_sliders,
        color_hex=body.color_hex,
        person_gender=body.person_gender,
        person_count=body.person_count,
        camera_shot=body.camera_shot,
        camera_angle=body.camera_angle,
        session_manager=mgr,
    )

    request.app.state.invoke_event_queues[session.session_id] = session.event_queue

    return {"session_id": session.session_id, "job_id": job_id}


@router.post("/respin")
async def respin(body: RespinRequest, request: Request):
    mgr = _get_invoke_manager(request)
    session = mgr.get_session(body.session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    spirit = session.spirits.get(body.spirit_name)
    if not spirit:
        raise HTTPException(400, f"Spirit '{body.spirit_name}' not in session")

    # Increment respin count on existing sha256 if present
    if spirit.sha256:
        try:
            old_count = (spirit.prompt_result or {}).get("respin_count", 0)
            await session.db.set_payload(spirit.sha256, {
                "genesis.respin_count": old_count + 1
            })
        except Exception:
            pass

    spirit.status = "composing"
    spirit.sha256 = None
    spirit.prompt_result = None
    spirit.alignment_score = None

    from ..invoke.vocab_bank import get_vocab_hints
    axis_tags = []
    for v in (session.axes or {}).values():
        if isinstance(v, list):
            axis_tags.extend(v)
        elif isinstance(v, str) and v:
            axis_tags.extend(v.split())

    try:
        vocab_hints = await get_vocab_hints(session.db, session.ollama, axis_tags)
    except Exception:
        vocab_hints = {"stranger": [], "lunatic": []}

    from ..spooler.models import JobLane
    from ..jobs.runners import run_invoke_spirit_compose

    spirit_vocab = vocab_hints if body.spirit_name in ("stranger", "lunatic") else {"stranger": [], "lunatic": []}

    job_id = session.spooler.submit(
        JobLane.PROMPT,
        f"invoke.respin/{body.spirit_name[:3]}",
        run_invoke_spirit_compose,
        meta={"session_id": body.session_id, "spirit": body.spirit_name, "respin": True},
        session_id=body.session_id,
        spirit_name=body.spirit_name,
        axes=session.axes or {},
        vocab_hints=spirit_vocab,
        locale=session.locale,
        session_manager=mgr,
    )
    spirit.job_ids.append(job_id)
    return {"job_id": job_id}


@router.post("/adopt")
async def adopt(body: AdoptRequest, request: Request):
    mgr = _get_invoke_manager(request)
    sha256 = await mgr.adopt_spirit(body.session_id, body.spirit_name)
    if sha256 is None:
        raise HTTPException(404, "Session or spirit not found, or image not ready")
    return {"sha256": sha256}


@router.post("/send-to-refine")
async def send_to_refine(body: SendToRefineRequest, request: Request):
    mgr = _get_invoke_manager(request)
    session = mgr.get_session(body.session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    spirit = session.spirits.get(body.spirit_name)
    if not spirit or not spirit.prompt_result:
        raise HTTPException(400, "Spirit not composed yet")

    pr = spirit.prompt_result
    prompt_mode = session.prompt_mode
    if prompt_mode == "danbooru":
        positive = pr.get("danbooru_tags") or ""
    elif prompt_mode == "natural":
        positive = pr.get("natural_language") or ""
    else:
        positive = (pr.get("natural_language") or "") + "\n" + (pr.get("danbooru_tags") or "")
    negative = pr.get("negative_supplement") or ""
    sha256 = spirit.sha256

    return {
        "positive_prompt": positive.strip(),
        "negative_prompt": negative,
        "sha256": sha256,
        "workflow_name": body.workflow_name or session.workflow_name,
    }


@router.post("/cancel")
async def cancel_session(body: CancelRequest, request: Request):
    mgr = _get_invoke_manager(request)
    ok = await mgr.cancel_session(body.session_id)
    if not ok:
        raise HTTPException(404, "Session not found or already completed")
    return {"cancelled": True}


@router.get("/stream/{session_id}")
async def stream_session(session_id: str, request: Request):
    """SSE stream for a specific invoke session."""
    queues: dict = getattr(request.app.state, "invoke_event_queues", {})
    q = queues.get(session_id)
    if q is None:
        # Session may already have completed — check manager
        mgr = getattr(request.app.state, "invoke_session_manager", None)
        if mgr:
            session = mgr.get_session(session_id)
            if session:
                q = session.event_queue
    if q is None:
        raise HTTPException(404, "Session stream not found")

    return StreamingResponse(
        _sse_generator(q, queues, session_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/daily")
async def get_daily(request: Request):
    """Return today's daily oracle images (or null if none exist or feature disabled)."""
    db = request.app.state.db
    from ..runtime_config import get_runtime_config
    cfg = await get_runtime_config(db)
    enabled = cfg.get("invoke_daily_oracle_enabled", False)
    if not enabled:
        return {"date": date.today().isoformat(), "enabled": False, "images": None, "next_run_at": None}
    today = _oracle_date_str(cfg)
    next_run_at = _oracle_next_run_iso(cfg)
    images = await db.get_daily_oracle(today)
    if not images:
        return {"date": today, "enabled": True, "images": None, "next_run_at": next_run_at}
    from ..invoke.session_manager import SPIRIT_ORDER
    by_spirit = {img["genesis"]["spirit"]: img for img in images if img.get("genesis")}
    return {"date": today, "enabled": True, "images": by_spirit, "spirit_order": SPIRIT_ORDER, "next_run_at": next_run_at}


@router.post("/daily-oracle")
async def trigger_daily_oracle(body: DailyOracleRequest, request: Request):
    """Manually trigger daily oracle (called by external cron)."""
    db = request.app.state.db
    ollama = request.app.state.ollama
    comfy = request.app.state.comfy
    spooler = request.app.state.spooler
    mgr = _get_invoke_manager(request)

    from ..runtime_config import get_runtime_config
    cfg = await get_runtime_config(db)
    if not cfg.get("invoke_daily_oracle_enabled", False):
        return {"status": "disabled"}
    workflow_name = body.workflow_name or cfg.get("invoke_daily_oracle_workflow", "")

    today = _oracle_date_str(cfg)
    existing = await db.get_daily_oracle(today)
    if existing:
        return {"status": "already_done", "date": today, "count": len(existing)}

    from ..spooler.models import JobLane
    from ..jobs.runners import run_invoke_daily_oracle

    topic = cfg.get("invoke_daily_oracle_topic", "") or ""
    job_id = spooler.submit(
        JobLane.SYNC,
        "invoke.daily_oracle",
        run_invoke_daily_oracle,
        meta={"daily_oracle_date": today},
        priority=-10,
        db=db,
        ollama=ollama,
        comfy=comfy,
        spooler=spooler,
        session_manager=mgr,
        daily_oracle_date=today,
        workflow_name=workflow_name,
        topic=topic,
    )
    return {"status": "queued", "job_id": job_id, "date": today}


@router.get("/stats")
async def get_stats(request: Request):
    """Return invoke usage statistics."""
    db = request.app.state.db
    stats = await db.get_invoke_stats()
    return stats or {}


@router.post("/enhance-prompt")
async def enhance_prompt(body: EnhancePromptRequest, request: Request):
    """Embed user text, find semantically related Danbooru tags, refine into prompt + natural language."""
    from ..invoke.vocab_bank import _is_species_tag

    db     = request.app.state.db
    ollama = request.app.state.ollama

    if not body.text.strip():
        raise HTTPException(422, "text must not be empty")

    count = await db.count_wd14_vocab()
    if count == 0:
        raise HTTPException(503, "WD14 vocab not imported — run POST /api/admin/invoke/import-wd14-vocab first")

    try:
        vec = await ollama.embed(body.text)
    except Exception as e:
        raise HTTPException(502, f"Embed failed: {e}")

    hits = await db.search_wd14_vocab(vec, min_freq=0.005, max_freq=1.0, limit=body.tag_count * 2)
    # Filter species/race tags from candidates before passing to LLM
    candidate_names = [h["name"] for h in hits if not _is_species_tag(h["name"])]

    system_prompt = (
        "You are an expert Danbooru image-tag curator. "
        "The user provides a scene description (possibly in Japanese). "
        "You receive a candidate tag list sourced by semantic search. "
        "Your job: select the most fitting tags, add obvious missing ones (e.g. 1girl), "
        "and write a polished English visual description (1-2 sentences). "
        "Output ONLY valid JSON, no markdown fences:\n"
        '{"tags": "tag1, tag2, ...", "natural_language": "..."}'
    )
    user_msg = (
        f"User description: {body.text}\n\n"
        f"Candidate tags: {', '.join(candidate_names)}\n\n"
        f"Select {body.tag_count} tags and write the natural_language description."
    )
    full_prompt = f"{system_prompt}\n\n{user_msg}"

    try:
        raw = await ollama.generate_text(full_prompt, fmt="json")
        import re as _re
        raw = _re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=_re.MULTILINE)
        raw = _re.sub(r"\s*```$", "", raw.strip(), flags=_re.MULTILINE)
        import json as _json
        result = _json.loads(raw)
    except Exception as e:
        logger.warning("enhance_prompt LLM parse failed: %s — returning raw hits", e)
        result = {
            "tags": ", ".join(candidate_names[:body.tag_count]),
            "natural_language": body.text,
        }

    # Filter species tags from LLM output as a second line of defense
    raw_tags = [t.strip() for t in result.get("tags", "").split(",")]
    result["tags"] = ", ".join(t for t in raw_tags if t and not _is_species_tag(t))

    return {
        "tags":             result.get("tags", ""),
        "natural_language": result.get("natural_language", ""),
        "vocab_hits":       [h for h in hits[:body.tag_count] if not _is_species_tag(h["name"])],
    }


@router.get("/session/{session_id}")
async def get_session(session_id: str, request: Request):
    """Return current state of a session."""
    mgr = _get_invoke_manager(request)
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session.to_dict()
