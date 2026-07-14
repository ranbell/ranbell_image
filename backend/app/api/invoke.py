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

from ..jobs.sse_stream import queue_sse_response

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
    pro_topic: str = ""              # Pro mode natural language topic (お題テキスト直送)
    pro_sections: dict[str, str] = {}  # character / background / props / action seed hints
    # Rebel spirit control
    rebel_inversion: bool = True  # False = rebel aims for beautiful image without axis inversion
    # Resonance mode: drift all spirits toward the user's starred aesthetic
    resonance_mode: bool = False
    # Frontier mode: drift all spirits AWAY from the user's known territory (mutually exclusive with resonance)
    frontier_mode: bool = False
    # Global LLM temperature multiplier applied on top of each spirit's native temperature
    heat: float = 1.0  # 0.6–1.3
    # 乱れ度 1–3: widens stranger/lunatic vocab pools (2: wider band + 3 wild tags, 3: + rare tag)
    wildness: int = 1
    # Target emotion dimension ('' | loneliness | nostalgia | ... — see emotion_tagger.EMOTION_DIMENSIONS)
    emotion: str = ""
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


class EvolveRequest(BaseModel):
    sha256: str
    mutation: float = 0.3    # fraction of mutable axes to jitter (0–1)
    workflow_name: str = ""
    enabled_spirits: list[str] = []
    prompt_mode: str = "danbooru+natural"
    locale: str = "en"
    heat: float = 1.0
    wildness: int = 1


class BreedRequest(BaseModel):
    sha256_a: str
    sha256_b: str
    workflow_name: str = ""
    enabled_spirits: list[str] = []
    prompt_mode: str = "danbooru+natural"
    locale: str = "en"
    heat: float = 1.0
    wildness: int = 1


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

    user_intent = body.user_intent
    _pro_topic = body.pro_topic if body.input_mode == "pro" else ""
    _pro_prompt = body.pro_prompt if body.input_mode == "pro" else ""

    from ..invoke.axis_decomposer import _resolve_person
    person_tags_str, _ = _resolve_person(body.person_gender, body.person_count)
    if body.input_mode == "pro" and body.pro_person_tags.strip():
        person_tags_str = body.pro_person_tags.strip()

    _pro_sections = body.pro_sections if body.input_mode == "pro" else {}
    session = mgr.create_session(
        user_intent=user_intent,
        input_mode=body.input_mode,
        workflow_name=workflow_name,
        enabled_spirits=body.enabled_spirits,
        prompt_mode=body.prompt_mode,
        locale=body.locale,
        person_tags=person_tags_str,
        pro_negative=body.pro_negative if body.input_mode == "pro" else "",
        pro_topic=_pro_topic,
        pro_sections=_pro_sections,
        rebel_inversion=body.rebel_inversion,
        heat=body.heat,
        wildness=body.wildness,
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
        pro_topic=_pro_topic,
        pro_sections=_pro_sections,
        pro_prompt=_pro_prompt,
        session_manager=mgr,
        resonance_mode=body.resonance_mode,
        frontier_mode=body.frontier_mode,
        emotion=body.emotion,
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

    # Preserve the previous attempt so the respin can diverge from it
    if spirit.prompt_result:
        spirit.history.append(spirit.prompt_result)

    spirit.status = "composing"
    spirit.sha256 = None
    spirit.prompt_result = None
    spirit.alignment_score = None
    spirit.novelty_score = None
    # Allow the finalize job (pipeline → novelty → alignment) to run again for the respun image
    session.finalize_submitted = False

    from ..spooler.models import JobLane
    from ..jobs.runners import run_invoke_respin

    job_id = session.spooler.submit(
        JobLane.PROMPT,
        f"invoke.respin/{body.spirit_name[:3]}",
        run_invoke_respin,
        meta={"session_id": body.session_id, "spirit": body.spirit_name, "respin": True},
        session_id=body.session_id,
        spirit_name=body.spirit_name,
        session_manager=mgr,
    )
    spirit.job_ids.append(job_id)
    return {"job_id": job_id}


async def _load_genesis_axes(db, sha256: str) -> tuple[dict, dict]:
    """Return (axes_snapshot, genesis) for an Invoke-born image, or raise HTTPException."""
    doc = await db.get(sha256)
    if not doc:
        raise HTTPException(404, f"Image {sha256[:12]} not found")
    genesis = doc.get("genesis") or {}
    axes = genesis.get("axes_snapshot") or {}
    if not axes:
        raise HTTPException(400, f"Image {sha256[:12]} has no genesis axes (not Invoke-born)")
    return axes, genesis


async def _launch_lineage_session(
    request: Request,
    *,
    mode: str,
    parent_shas: list[str],
    parent_axes: list[dict],
    user_intent: str,
    workflow_name: str,
    enabled_spirits: list[str],
    prompt_mode: str,
    locale: str,
    heat: float,
    wildness: int,
    mutation: float = 0.3,
) -> dict:
    mgr = _get_invoke_manager(request)
    db = request.app.state.db
    ollama = request.app.state.ollama

    from ..runtime_config import get_runtime_config
    cfg = await get_runtime_config(db)
    workflow_name = workflow_name or cfg.get("invoke_daily_oracle_workflow", "")
    if not workflow_name:
        raise HTTPException(422, "workflow_name required (no default workflow configured)")

    session = mgr.create_session(
        user_intent=user_intent,
        input_mode=mode,
        workflow_name=workflow_name,
        enabled_spirits=enabled_spirits,
        prompt_mode=prompt_mode,
        locale=locale,
        heat=heat,
        wildness=wildness,
        parent_sha256s=parent_shas,
        db=db,
        ollama=ollama,
        comfy=request.app.state.comfy,
        spooler=request.app.state.spooler,
    )

    from ..spooler.models import JobLane
    from ..jobs.runners import run_invoke_lineage

    job_id = request.app.state.spooler.submit(
        JobLane.PROMPT,
        f"invoke.{mode}",
        run_invoke_lineage,
        meta={"session_id": session.session_id, "mode": mode, "parents": parent_shas},
        db=db,
        ollama=ollama,
        session_id=session.session_id,
        parent_axes=parent_axes,
        mode=mode,
        mutation=mutation,
        session_manager=mgr,
    )

    request.app.state.invoke_event_queues[session.session_id] = session.event_queue
    return {"session_id": session.session_id, "job_id": job_id}


@router.post("/evolve")
async def evolve(body: EvolveRequest, request: Request):
    """Re-summon from an Invoke-born image's axes snapshot with mutation."""
    db = request.app.state.db
    axes, genesis = await _load_genesis_axes(db, body.sha256)
    return await _launch_lineage_session(
        request,
        mode="evolve",
        parent_shas=[body.sha256],
        parent_axes=[axes],
        user_intent=genesis.get("original_intent") or "[evolve]",
        workflow_name=body.workflow_name,
        enabled_spirits=body.enabled_spirits,
        prompt_mode=body.prompt_mode,
        locale=body.locale,
        heat=body.heat,
        wildness=body.wildness,
        mutation=body.mutation,
    )


@router.post("/breed")
async def breed(body: BreedRequest, request: Request):
    """Merge two Invoke-born images' axes snapshots into a child session."""
    db = request.app.state.db
    axes_a, genesis_a = await _load_genesis_axes(db, body.sha256_a)
    axes_b, genesis_b = await _load_genesis_axes(db, body.sha256_b)
    intent_a = genesis_a.get("original_intent") or ""
    intent_b = genesis_b.get("original_intent") or ""
    user_intent = " × ".join(filter(None, dict.fromkeys([intent_a, intent_b]))) or "[breed]"
    return await _launch_lineage_session(
        request,
        mode="breed",
        parent_shas=[body.sha256_a, body.sha256_b],
        parent_axes=[axes_a, axes_b],
        user_intent=user_intent,
        workflow_name=body.workflow_name,
        enabled_spirits=body.enabled_spirits,
        prompt_mode=body.prompt_mode,
        locale=body.locale,
        heat=body.heat,
        wildness=body.wildness,
    )


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
        roulette=bool(cfg.get("invoke_daily_oracle_roulette", False)),
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
    """Submit tag-generation job to PROMPT lane. Stream results via /enhance-prompt/{job_id}/stream."""
    db      = request.app.state.db
    spooler = request.app.state.spooler

    if not body.text.strip():
        raise HTTPException(422, "text must not be empty")

    count = await db.count_wd14_vocab()
    if count == 0:
        raise HTTPException(503, "WD14 vocab not imported — run POST /api/admin/invoke/import-wd14-vocab first")

    from ..spooler.models import JobLane
    from ..jobs.runners import run_invoke_enhance_prompt

    event_queue: asyncio.Queue = asyncio.Queue()
    job_id = spooler.submit(
        JobLane.PROMPT,
        "invoke.enhance_prompt",
        run_invoke_enhance_prompt,
        db=db,
        ollama=request.app.state.ollama,
        text=body.text,
        tag_count=body.tag_count,
        event_queue=event_queue,
    )
    request.app.state.inspire_event_queues[job_id] = event_queue
    return {"job_id": job_id, "status": "queued"}


@router.get("/enhance-prompt/{job_id}/stream")
async def enhance_prompt_stream(job_id: str, request: Request):
    q: asyncio.Queue | None = request.app.state.inspire_event_queues.get(job_id)
    if q is None:
        raise HTTPException(404, f"enhance-prompt job {job_id!r} not found")
    return queue_sse_response(
        request, q, job_id=job_id,
        registry=request.app.state.inspire_event_queues, encode="raw",
    )


@router.get("/session/{session_id}")
async def get_session(session_id: str, request: Request):
    """Return current state of a session."""
    mgr = _get_invoke_manager(request)
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session.to_dict()


@router.get("/resonance/preview")
async def resonance_preview(request: Request, n: int = 20):
    """Preview the taste centroid tags without triggering a summon.

    Returns {tags: [{name, score}], star4_count, star5_count, total_contributing}.
    Used by the frontend resonance toggle to show which aesthetic hints are active.
    """
    from qdrant_client import models as qm

    db = request.app.state.db
    # Count contributing images
    star4_count = 0
    star5_count = 0
    offset = None
    try:
        while True:
            pts, next_offset = await db._qc.scroll(
                collection_name="images",
                scroll_filter=qm.Filter(must=[
                    qm.FieldCondition(key="star_rating", range=qm.Range(gte=4)),
                    qm.FieldCondition(key="embedding_status", match=qm.MatchValue(value="done")),
                ]),
                limit=500,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["star_rating"]),
                with_vectors=False,
            )
            for p in pts:
                r = (p.payload or {}).get("star_rating", 4)
                if r >= 5:
                    star5_count += 1
                else:
                    star4_count += 1
            if next_offset is None or (star4_count + star5_count) >= 500:
                break
            offset = next_offset
    except Exception as e:
        logger.warning("resonance_preview count failed: %s", e)
        return {"tags": [], "star4_count": 0, "star5_count": 0, "total_contributing": 0}

    if star4_count + star5_count == 0:
        return {"tags": [], "star4_count": 0, "star5_count": 0, "total_contributing": 0}

    from ..invoke.vocab_bank import compute_resonance_hints
    hints = await compute_resonance_hints(db, n_tags=n)
    all_tags = hints.get("character", []) + hints.get("mood", []) + hints.get("scene", [])

    return {
        "tags": [{"name": t} for t in all_tags[:n]],
        "star4_count": star4_count,
        "star5_count": star5_count,
        "total_contributing": star4_count + star5_count,
    }


@router.get("/frontier/preview")
async def frontier_preview(request: Request, n: int = 20):
    """Preview the frontier tags (never-seen vocabulary far from the taste centroid).

    Returns {tags: [{name}], total_contributing}. Empty tags when no starred images
    exist (the frontier is computed relative to the taste centroid).
    """
    db = request.app.state.db

    from ..invoke.vocab_bank import compute_frontier_hints
    hints = await compute_frontier_hints(db, n_tags=n)
    all_tags = hints.get("character", []) + hints.get("mood", []) + hints.get("scene", [])

    return {"tags": [{"name": t} for t in all_tags[:n]]}
