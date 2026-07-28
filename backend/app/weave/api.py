"""Weave HTTP API — /api/weave"""
from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import service
from . import session_db
from .state_machine import next_cta

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/weave", tags=["weave"])


class CreateSessionRequest(BaseModel):
    topic: str = ""
    personality_text: str = ""
    author_id: str = ""
    author_style: str = ""
    reference_image_id: str = ""
    story_model: str = ""
    vlm_model: str = ""
    llm_provider: Literal["ollama", "openai"] = "ollama"
    workflow_final: str = ""
    workflow_sample: str = ""
    locale: Literal["en", "ja"] = "ja"
    use_gallery_nn: bool = False


class InferRequest(BaseModel):
    personality_text: str = ""
    story_model: str = ""
    temperature: float = 0.7
    use_gallery_nn: bool | None = None


class TopicPatch(BaseModel):
    topic: str = ""
    author_style: str = ""
    author_id: str | None = None
    story_model: str = ""
    vlm_model: str = ""
    llm_provider: Literal["ollama", "openai"] | None = None
    use_gallery_nn: bool | None = None
    vlm_assist: bool | None = None
    strict_seal: bool | None = None
    sample_steps: int | None = None
    spicer: bool | None = None
    multi_seed: int | None = None
    mode: Literal["standard", "lab"] | None = None
    age_band: str | None = None
    gender_hint: str | None = None
    occupation_hint: str | None = None
    time_scale: str | None = None


class StoryGenerateRequest(BaseModel):
    topic: str = ""
    story_model: str = ""
    temperature: float = 0.7
    author_style: str = ""


class UnlockIdentityRequest(BaseModel):
    confirm: bool = False


class NarrativePatchRequest(BaseModel):
    panel_key: str
    narrative_ja: str | None = None
    narrative_en: str | None = None


class RecreateRequest(BaseModel):
    chips: list[str] = Field(default_factory=list)
    story_model: str = ""
    temperature: float = 0.8


class RollbackRequest(BaseModel):
    to_version: int


class RateRequest(BaseModel):
    panel_key: str
    chips: list[str] = Field(default_factory=list)


class AdoptSampleRequest(BaseModel):
    panel_key: str
    image_id: str = ""
    history_index: int | None = None


class OverrideFramingRequest(BaseModel):
    panel_key: str
    reason: str


class SampleRequest(BaseModel):
    panel_key: str = "panel_1"
    placeholder: bool = False
    workflow_sample: str = ""
    sample_steps: int | None = None


def _model_from(session: dict, body_model: str = "") -> str:
    return (body_model or (session.get("inputs") or {}).get("story_model") or "").strip()


def _vlm_model_from(session: dict, body_model: str = "") -> str:
    inputs = session.get("inputs") or {}
    return (
        body_model
        or str(inputs.get("vlm_model") or "").strip()
        or str(inputs.get("story_model") or "").strip()
    )


def _ollama(request: Request):
    return request.app.state.ollama


def _llm(request: Request, session: dict[str, Any] | None = None):
    """Bind Weave LLM/VLM to session ``inputs.llm_provider`` (default ollama)."""
    gw = request.app.state.ollama
    provider = "ollama"
    if session:
        provider = str((session.get("inputs") or {}).get("llm_provider") or "ollama")
    bind = getattr(gw, "bind", None)
    if callable(bind):
        return bind(provider)
    return gw


async def _load(request: Request, session_id: str) -> dict[str, Any]:
    session = await session_db.get_session(request.app.state.db, session_id)
    if not session:
        raise HTTPException(404, "session not found")
    return session


async def _save(request: Request, session_id: str, session: dict) -> dict:
    await session_db.save_session(request.app.state.db, session_id, session)
    session["session_id"] = session_id
    try:
        from .events import publish

        publish(session_id, {
            "type": "session_updated",
            "status": session.get("status"),
        })
    except Exception:
        logger.debug("weave SSE publish failed", exc_info=True)
    return service.public_view(session)


@router.get("/catalog")
async def weave_catalog(request: Request):
    """Workflows, LLM models, authors — shared capability catalog for Weave UI."""
    from ..story.catalog import build_chronicle_catalog
    return await build_chronicle_catalog(request.app)


@router.get("/presets")
async def weave_presets(request: Request):
    """Character preset picker rows (summaries only — payloads stay server-side)."""
    from .character.presets import list_presets

    rows = await list_presets(request.app.state.db)
    return {"presets": rows, "count": len(rows)}


@router.post("/sessions")
async def create_session(body: CreateSessionRequest, request: Request):
    from .character.authors import resolve_author_style

    payload = await service.create_session_payload(
        topic=body.topic,
        personality_text=body.personality_text,
        author_id=body.author_id,
        author_style=body.author_style,
        reference_image_id=body.reference_image_id,
        story_model=body.story_model,
        llm_provider=body.llm_provider,
        workflow_final=body.workflow_final,
        workflow_sample=body.workflow_sample,
        locale=body.locale,
        use_gallery_nn=body.use_gallery_nn,
    )
    if body.vlm_model:
        payload.setdefault("inputs", {})["vlm_model"] = body.vlm_model
    await resolve_author_style(payload, request.app.state.db)
    session_id = await session_db.create_session(request.app.state.db, payload)
    session = await session_db.get_session(request.app.state.db, session_id)
    return service.public_view(session or {"session_id": session_id, **payload})


@router.get("/sessions")
async def list_sessions(request: Request, limit: int = 50):
    rows = await session_db.list_sessions(request.app.state.db, limit=limit)
    return {"sessions": [{"session_id": r["session_id"], "status": r.get("status"),
                          "topic": (r.get("inputs") or {}).get("topic"),
                          "created_at": r.get("created_at")} for r in rows]}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    session = await _load(request, session_id)
    return service.public_view(session)


@router.patch("/sessions/{session_id}/inputs")
async def patch_inputs(session_id: str, body: TopicPatch, request: Request):
    from .character.authors import resolve_author_style
    from .character.gallery_nn import set_gallery_nn_enabled
    from .character.topic_fit import apply_topic_warnings

    session = await _load(request, session_id)
    inputs = session.setdefault("inputs", {})
    if body.topic:
        inputs["topic"] = body.topic
    if body.author_style:
        inputs["author_style"] = body.author_style
    if body.author_id is not None:
        inputs["author_id"] = body.author_id
        if body.author_id and not body.author_style:
            # Clear stale freeform so preset can fill
            if inputs.get("author_name") and inputs.get("author_style") == inputs.get("author_name"):
                inputs["author_style"] = ""
    if body.story_model:
        inputs["story_model"] = body.story_model
    if body.vlm_model:
        inputs["vlm_model"] = body.vlm_model
    if body.llm_provider is not None:
        inputs["llm_provider"] = body.llm_provider
    if body.use_gallery_nn is not None:
        set_gallery_nn_enabled(session, body.use_gallery_nn)
    if body.vlm_assist is not None:
        session.setdefault("quality_policy", {})["vlm_assist"] = bool(body.vlm_assist)
    if body.strict_seal is not None:
        session.setdefault("quality_policy", {})["strict_seal"] = bool(body.strict_seal)
    if body.sample_steps is not None:
        session.setdefault("inputs", {})["sample_steps"] = int(body.sample_steps)
    if body.spicer is not None:
        from .character.spicer import set_spicer_enabled

        set_spicer_enabled(session, bool(body.spicer))
    if body.multi_seed is not None:
        session.setdefault("quality_policy", {})["multi_seed"] = max(
            1, min(3, int(body.multi_seed)),
        )
    if body.age_band is not None:
        inputs["age_band"] = str(body.age_band).strip()
    if body.gender_hint is not None:
        inputs["gender_hint"] = str(body.gender_hint).strip()
    if body.occupation_hint is not None:
        inputs["occupation_hint"] = str(body.occupation_hint).strip()
    if body.time_scale is not None:
        from ..story.generator import normalize_time_scale

        inputs["time_scale"] = normalize_time_scale(body.time_scale, default="hours")
    if body.mode is not None:
        session.setdefault("quality_policy", {})["mode"] = body.mode
        if body.mode == "lab" and body.spicer is None:
            # Entering lab does not force spicer on; leaving clears nothing.
            pass
    await resolve_author_style(session, request.app.state.db)
    apply_topic_warnings(session)
    return await _save(request, session_id, session)


@router.post("/sessions/{session_id}/character/infer")
async def character_infer(session_id: str, body: InferRequest, request: Request):
    from .character.gallery_nn import set_gallery_nn_enabled
    from ..runtime_config import get_runtime_config

    session = await _load(request, session_id)
    if body.use_gallery_nn is not None:
        set_gallery_nn_enabled(session, body.use_gallery_nn)
    model = _model_from(session, body.story_model)
    cfg = await get_runtime_config(request.app.state.db)
    try:
        await service.infer_character(
            session,
            _llm(request, session),
            model=model,
            options={"temperature": body.temperature},
            personality_text=body.personality_text or None,
            db=request.app.state.db,
            embed_model=str(cfg.get("embed_model") or "nomic-embed-text"),
        )
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    except Exception as e:
        logger.exception("personalitywright failed")
        raise HTTPException(502, f"personalitywright failed: {e}") from e
    if body.story_model:
        session.setdefault("inputs", {})["story_model"] = body.story_model
    return await _save(request, session_id, session)


class ApplyPresetRequest(BaseModel):
    preset_id: str


@router.post("/sessions/{session_id}/character/preset")
async def character_preset(session_id: str, body: ApplyPresetRequest, request: Request):
    """Apply a character preset deterministically (no LLM call)."""
    from .character.presets import get_preset

    session = await _load(request, session_id)
    preset = await get_preset(request.app.state.db, body.preset_id)
    if not preset:
        raise HTTPException(404, f"preset not found: {body.preset_id}")
    try:
        service.apply_preset(session, preset)
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    return await _save(request, session_id, session)


@router.post("/sessions/{session_id}/character/lock")
async def character_lock(session_id: str, request: Request):
    session = await _load(request, session_id)
    try:
        service.lock_identity(session)
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    return await _save(request, session_id, session)


@router.post("/sessions/{session_id}/character/unlock")
async def character_unlock(
    session_id: str, request: Request, body: UnlockIdentityRequest | None = None,
):
    session = await _load(request, session_id)
    body = body or UnlockIdentityRequest()
    try:
        service.unlock_identity(session, confirm=bool(body.confirm))
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    return await _save(request, session_id, session)


class AcceptBoardRequest(BaseModel):
    allow_pending: bool = False  # tests / dry-run only


@router.post("/sessions/{session_id}/character/accept-board")
async def character_accept_board(
    session_id: str, request: Request, body: AcceptBoardRequest | None = None,
):
    session = await _load(request, session_id)
    try:
        service.accept_board(
            session,
            allow_pending=bool(body and body.allow_pending),
        )
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    return await _save(request, session_id, session)


class BoardRenderRequest(BaseModel):
    workflow_sample: str = ""
    workflow_final: str = ""
    dry_pending: bool = False  # skip Comfy; create pending slots only


@router.post("/sessions/{session_id}/character/board")
async def character_board(
    session_id: str, request: Request, body: BoardRenderRequest | None = None,
):
    """Queue Comfy board renders (portrait/full/prop)."""
    from .schema import append_timeline
    from .render.submit import submit_board_jobs

    session = await _load(request, session_id)
    body = body or BoardRenderRequest()
    inputs = session.setdefault("inputs", {})
    if body.workflow_sample:
        inputs["workflow_sample"] = body.workflow_sample
    if body.workflow_final:
        inputs["workflow_final"] = body.workflow_final

    if body.dry_pending or not (
        inputs.get("workflow_final") or inputs.get("workflow_sample")
    ):
        from .schema import BOARD_SLOTS

        board = session.setdefault("character", {}).setdefault("board", {})
        board["images"] = [
            {"slot": slot, "image_id": None, "job_id": None, "pending": True}
            for slot in BOARD_SLOTS
        ]
        board["accepted"] = False
        append_timeline(
            session, actor="system", type_="message",
            text="board slots prepared without workflow (dry)",
        )
        view = await _save(request, session_id, session)
        view["jobs"] = []
        return view

    try:
        jobs = submit_board_jobs(request.app, session_id, session)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    append_timeline(
        session, actor="system", type_="message",
        text=f"board render queued: {len(jobs)} job(s)",
    )
    view = await _save(request, session_id, session)
    view["jobs"] = jobs
    return view


@router.post("/sessions/{session_id}/story/generate")
async def story_generate(session_id: str, body: StoryGenerateRequest, request: Request):
    session = await _load(request, session_id)
    if body.author_style:
        session.setdefault("inputs", {})["author_style"] = body.author_style
    model = _model_from(session, body.story_model)
    try:
        await service.generate_story(
            session,
            _llm(request, session),
            model=model,
            options={"temperature": body.temperature},
            topic=body.topic or None,
            db=request.app.state.db,
        )
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    except Exception as e:
        logger.exception("storywright failed")
        raise HTTPException(502, f"storywright failed: {e}") from e
    return await _save(request, session_id, session)


@router.patch("/sessions/{session_id}/story/narrative")
async def patch_narrative(session_id: str, body: NarrativePatchRequest, request: Request):
    """Typo-only narrative patch. Large rewrites are rejected → use Recreate."""
    from .schema import append_timeline
    from .story.narrative_patch import NarrativePatchError, apply_narrative_typo_patch

    session = await _load(request, session_id)
    try:
        result = apply_narrative_typo_patch(
            session,
            panel_key=body.panel_key,
            narrative_ja=body.narrative_ja,
            narrative_en=body.narrative_en,
        )
    except NarrativePatchError as e:
        raise HTTPException(400, str(e)) from e
    append_timeline(
        session, actor="user", type_="edit",
        text=f"narrative typo patch {body.panel_key}",
        ref=result,
    )
    return await _save(request, session_id, session)


@router.post("/sessions/{session_id}/story/recreate")
async def story_recreate(session_id: str, body: RecreateRequest, request: Request):
    session = await _load(request, session_id)
    model = _model_from(session, body.story_model)
    try:
        await service.recreate_story(
            session,
            _llm(request, session),
            model=model,
            chips=body.chips,
            options={"temperature": body.temperature},
            db=request.app.state.db,
        )
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    except Exception as e:
        logger.exception("recreate failed")
        raise HTTPException(502, f"recreate failed: {e}") from e
    return await _save(request, session_id, session)


@router.post("/sessions/{session_id}/story/rollback")
async def story_rollback(session_id: str, body: RollbackRequest, request: Request):
    session = await _load(request, session_id)
    try:
        service.rollback_story(session, body.to_version)
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    return await _save(request, session_id, session)


@router.post("/sessions/{session_id}/lookdev")
async def enter_lookdev(session_id: str, request: Request):
    session = await _load(request, session_id)
    try:
        service.enter_lookdev(session)
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    return await _save(request, session_id, session)


@router.post("/sessions/{session_id}/compile")
async def compile_session(session_id: str, request: Request):
    session = await _load(request, session_id)
    compiled = service.compile_session(session)
    view = await _save(request, session_id, session)
    view["compiled"] = compiled
    return view


@router.post("/sessions/{session_id}/sample")
async def sample_panel(session_id: str, body: SampleRequest, request: Request):
    from .render.submit import submit_sample_job
    from .schema import append_timeline

    session = await _load(request, session_id)
    if session.get("status") != "lookdev":
        try:
            service.enter_lookdev(session)
        except service.WeaveError as e:
            raise HTTPException(e.status_code, e.message) from e
    if body.workflow_sample:
        session.setdefault("inputs", {})["workflow_sample"] = body.workflow_sample
    if body.sample_steps is not None:
        session.setdefault("inputs", {})["sample_steps"] = int(body.sample_steps)
    if body.placeholder:
        try:
            service.mark_sample_placeholder(session, body.panel_key)
        except service.WeaveError as e:
            raise HTTPException(e.status_code, e.message) from e
        return await _save(request, session_id, session)
    try:
        job = submit_sample_job(request.app, session_id, session, body.panel_key)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    jobs = job.get("jobs") or [job]
    append_timeline(
        session, actor="system", type_="sample",
        text=f"sample queued {body.panel_key} x{len(jobs)} job={job['job_id']}",
    )
    view = await _save(request, session_id, session)
    view["job"] = job
    view["jobs"] = jobs
    return view


@router.post("/sessions/{session_id}/sample/rate")
async def sample_rate(session_id: str, body: RateRequest, request: Request):
    session = await _load(request, session_id)
    try:
        service.rate_sample(session, panel_key=body.panel_key, chips=body.chips)
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    return await _save(request, session_id, session)


@router.post("/sessions/{session_id}/sample/adopt")
async def sample_adopt(session_id: str, body: AdoptSampleRequest, request: Request):
    """Promote a multi-seed history entry to the primary sample."""
    session = await _load(request, session_id)
    try:
        service.adopt_sample(
            session,
            panel_key=body.panel_key,
            image_id=body.image_id,
            history_index=body.history_index,
        )
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    return await _save(request, session_id, session)


@router.post("/sessions/{session_id}/sample/override-framing")
async def sample_override(session_id: str, body: OverrideFramingRequest, request: Request):
    session = await _load(request, session_id)
    try:
        service.override_framing(session, panel_key=body.panel_key, reason=body.reason)
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    return await _save(request, session_id, session)


@router.post("/sessions/{session_id}/sample/reeval-framing")
async def sample_reeval_framing(session_id: str, request: Request):
    """Re-run WD14-based framing on long_shot samples (unknown → pass/fail)."""
    session = await _load(request, session_id)
    await service.reeval_framing(session, request.app.state.db)
    return await _save(request, session_id, session)


class VlmAssistRequest(BaseModel):
    panel_key: str = "panel_1"
    force_heuristic: bool = False
    vlm_model: str = ""


@router.post("/sessions/{session_id}/sample/vlm-assist")
async def sample_vlm_assist(
    session_id: str, body: VlmAssistRequest, request: Request,
):
    """Fixed 4-question VLM assist (or WD14 heuristic) on a sample panel."""
    session = await _load(request, session_id)
    resolved_vlm = _vlm_model_from(session, body.vlm_model)
    if resolved_vlm:
        session.setdefault("inputs", {})["vlm_model"] = resolved_vlm
    try:
        result = await service.run_panel_vlm_assist(
            session,
            panel_key=body.panel_key,
            db=request.app.state.db,
            ollama=_llm(request, session),
            force_heuristic=body.force_heuristic,
        )
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    view = await _save(request, session_id, session)
    view["vlm_assist"] = result
    return view


@router.post("/sessions/{session_id}/score")
async def recompute_score(session_id: str, request: Request):
    """Recompute WeaveScore (rules) for the session."""
    session = await _load(request, session_id)
    score = service.recompute_scores(session)
    view = await _save(request, session_id, session)
    view["weave_score"] = score
    return view


@router.get("/sessions/{session_id}/stream")
async def stream_session(session_id: str, request: Request):
    """SSE: session updates (render_attached / session_updated) + pings."""
    from ..jobs.sse_stream import sse_response
    from .events import publish, subscribe, unsubscribe

    # Ensure session exists
    await _load(request, session_id)
    queue = await subscribe(session_id)
    # Immediate hello so clients know the stream is live
    publish(session_id, {"type": "hello", "status": "connected"})

    async def _agen():
        import asyncio
        import json

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            await unsubscribe(session_id, queue)

    return sse_response(_agen())


@router.get("/sessions/{session_id}/export")
async def export_session(session_id: str, request: Request):
    session = await _load(request, session_id)
    return service.export_bundle(session)


@router.get("/sessions/{session_id}/cta")
async def get_cta(session_id: str, request: Request):
    session = await _load(request, session_id)
    return next_cta(session)


class RenderFinalRequest(BaseModel):
    workflow_final: str = ""


@router.post("/sessions/{session_id}/render_final")
async def render_final(
    session_id: str, request: Request, body: RenderFinalRequest | None = None,
):
    from .render.submit import submit_final_jobs
    from .schema import append_timeline
    from .state_machine import gates

    session = await _load(request, session_id)
    body = body or RenderFinalRequest()
    if body.workflow_final:
        session.setdefault("inputs", {})["workflow_final"] = body.workflow_final
    g = gates(session)
    if not g["G0_soft"]["pass"]:
        raise HTTPException(400, "identity must be locked")
    if not g["G1"]["pass"]:
        raise HTTPException(400, "story lint must pass before final render")
    if not g["G4"]["pass"]:
        raise HTTPException(400, "framing must pass or be overridden before final render")
    if not g["G0_hard"]["pass"]:
        raise HTTPException(400, "board must be accepted before final render")
    policy = session.get("quality_policy") or {}
    if bool(policy.get("strict_seal")) and not g["G5"]["pass"]:
        raise HTTPException(400, "strict: lookdev ready_for_final required before final render")
    try:
        jobs = submit_final_jobs(request.app, session_id, session)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    append_timeline(
        session, actor="system", type_="message",
        text=f"final render queued: {len(jobs)} job(s)",
    )
    view = await _save(request, session_id, session)
    view["jobs"] = jobs
    return view


@router.post("/sessions/{session_id}/seal")
async def seal(session_id: str, request: Request):
    from .schema import append_timeline
    from .state_machine import gates
    from .verify.seal import evaluate_seal_rubric

    session = await _load(request, session_id)
    session["session_id"] = session_id
    gate_map = gates(session)
    if not gate_map["G0_hard"]["pass"]:
        raise HTTPException(400, "board must be accepted before seal")
    if not gate_map["G1"]["pass"]:
        raise HTTPException(400, "story lint must pass before seal")
    if not gate_map["G4"]["pass"]:
        raise HTTPException(400, "framing must pass or be overridden before seal")
    rubric = evaluate_seal_rubric(session)
    if not rubric["pass"]:
        failed = [k for k, v in (rubric.get("checks") or {}).items() if not v]
        raise HTTPException(
            400,
            f"seal rubric failed: {', '.join(failed) or 'unknown'}",
        )
    try:
        story_id = await service.project_to_storybook(session, request.app.state.db)
    except Exception as e:
        logger.exception("storybook projection failed")
        raise HTTPException(502, f"storybook projection failed: {e}") from e
    session["status"] = "sealed"
    session["seal_rubric"] = rubric
    append_timeline(
        session, actor="user", type_="seal",
        text=f"sealed → storybook {story_id}",
        ref={"storybook_story_id": story_id},
    )
    return await _save(request, session_id, session)
