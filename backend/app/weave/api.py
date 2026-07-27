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
    story_model: str = ""
    use_gallery_nn: bool | None = None


class StoryGenerateRequest(BaseModel):
    topic: str = ""
    story_model: str = ""
    temperature: float = 0.7


class RecreateRequest(BaseModel):
    chips: list[str] = Field(default_factory=list)
    story_model: str = ""
    temperature: float = 0.8


class RollbackRequest(BaseModel):
    to_version: int


class RateRequest(BaseModel):
    panel_key: str
    chips: list[str] = Field(default_factory=list)


class OverrideFramingRequest(BaseModel):
    panel_key: str
    reason: str


class SampleRequest(BaseModel):
    panel_key: str = "panel_1"
    placeholder: bool = False
    workflow_sample: str = ""


def _model_from(session: dict, body_model: str = "") -> str:
    return (body_model or (session.get("inputs") or {}).get("story_model") or "").strip()


def _ollama(request: Request):
    return request.app.state.ollama


async def _load(request: Request, session_id: str) -> dict[str, Any]:
    session = await session_db.get_session(request.app.state.db, session_id)
    if not session:
        raise HTTPException(404, "session not found")
    return session


async def _save(request: Request, session_id: str, session: dict) -> dict:
    await session_db.save_session(request.app.state.db, session_id, session)
    session["session_id"] = session_id
    return service.public_view(session)


@router.get("/catalog")
async def weave_catalog(request: Request):
    """Workflows, LLM models, authors — shared capability catalog for Weave UI."""
    from ..story.catalog import build_chronicle_catalog
    return await build_chronicle_catalog(request.app)


@router.post("/sessions")
async def create_session(body: CreateSessionRequest, request: Request):
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
    from .character.gallery_nn import set_gallery_nn_enabled

    session = await _load(request, session_id)
    inputs = session.setdefault("inputs", {})
    if body.topic:
        inputs["topic"] = body.topic
    if body.author_style:
        inputs["author_style"] = body.author_style
    if body.story_model:
        inputs["story_model"] = body.story_model
    if body.use_gallery_nn is not None:
        set_gallery_nn_enabled(session, body.use_gallery_nn)
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
            _ollama(request),
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


@router.post("/sessions/{session_id}/character/lock")
async def character_lock(session_id: str, request: Request):
    session = await _load(request, session_id)
    try:
        service.lock_identity(session)
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
        briefs = (session.get("character") or {}).get("board_briefs") or [
            {"slot": "portrait"}, {"slot": "full"}, {"slot": "prop"},
        ]
        board = session.setdefault("character", {}).setdefault("board", {})
        board["images"] = [
            {
                "slot": b.get("slot"),
                "image_id": None,
                "job_id": None,
                "pending": True,
            }
            for b in briefs if b.get("slot")
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
    model = _model_from(session, body.story_model)
    try:
        await service.generate_story(
            session,
            _ollama(request),
            model=model,
            options={"temperature": body.temperature},
            topic=body.topic or None,
        )
    except service.WeaveError as e:
        raise HTTPException(e.status_code, e.message) from e
    except Exception as e:
        logger.exception("storywright failed")
        raise HTTPException(502, f"storywright failed: {e}") from e
    return await _save(request, session_id, session)


@router.post("/sessions/{session_id}/story/recreate")
async def story_recreate(session_id: str, body: RecreateRequest, request: Request):
    session = await _load(request, session_id)
    model = _model_from(session, body.story_model)
    try:
        await service.recreate_story(
            session,
            _ollama(request),
            model=model,
            chips=body.chips,
            options={"temperature": body.temperature},
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
    append_timeline(
        session, actor="system", type_="sample",
        text=f"sample queued {body.panel_key} job={job['job_id']}",
    )
    view = await _save(request, session_id, session)
    view["job"] = job
    return view


@router.post("/sessions/{session_id}/sample/rate")
async def sample_rate(session_id: str, body: RateRequest, request: Request):
    session = await _load(request, session_id)
    try:
        service.rate_sample(session, panel_key=body.panel_key, chips=body.chips)
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
