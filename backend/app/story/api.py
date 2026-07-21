"""Chronicle / Storybook API.

POST /api/story/chronicle                    — Phase 1: pitch 3 candidates
POST /api/story/chronicle/{story_id}/select  — Phase 2: expand chosen candidate
POST /api/story/chronicle/{story_id}/respin  — regenerate (candidates | expand)
GET  /api/story/chronicle/{job_id}/stream    — SSE token/event stream for a job
POST /api/story/topic-suggest          — 起承転結 お題 from a base image (no job)
GET  /api/story/storybook              — list saved stories (newest first)
GET  /api/story/{story_id}             — one story
POST /api/story/{story_id}/generate-images   — manual-mode continue (writes
                                               edited prompts back, submits jobs)
POST /api/story/{story_id}/regenerate/{axis} — image-only retry with a new seed
"""

import asyncio
import json
import logging
import random
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..jobs.sse_stream import queue_sse_response
from ..spooler.models import JobLane
from . import db as story_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/story")


class ChronicleRequest(BaseModel):
    # Empty → topic-only mode (no source image); then user_topic is required.
    base_sha256: str = ""  # optional reference for character + art style only
    user_topic: str = ""  # theme
    character_tags: str = ""  # appearance; empty → WD14 from base image when present
    include_happening: bool = False
    author_style: str = ""
    author_id: str = ""
    custom_tags_panel_1: str = ""
    custom_tags_panel_2: str = ""
    custom_tags_panel_3: str = ""
    workflow_name: str = ""
    manual_mode: bool = False
    use_ref_seed: bool = True
    llm_provider: Literal["ollama", "openai"] = "ollama"
    vlm_model: str = ""
    story_model: str = ""
    utility_model: str = ""
    temperature: float = 0.7
    num_ctx: int = 32768
    locale: Literal["en", "ja"] = "ja"
    group_id: str = ""
    time_scale: str = "days"  # minutes|tens_of_minutes|hours|days|months|years|decades


class SelectCandidateRequest(BaseModel):
    candidate_id: str
    time_scale: str = ""  # optional override; draft context wins when empty


class RespinRequest(BaseModel):
    stage: Literal["candidates", "expand"]
    respin_count: int = 1
    workflow_name: str | None = None
    manual_mode: bool | None = None
    llm_provider: Literal["ollama", "openai"] | None = None
    temperature: float | None = None
    num_ctx: int | None = None
    user_topic: str | None = None
    character_tags: str | None = None
    include_happening: bool | None = None
    author_style: str | None = None
    author_id: str | None = None
    custom_tags_panel_1: str | None = None
    custom_tags_panel_2: str | None = None
    custom_tags_panel_3: str | None = None


class PinupRequest(BaseModel):
    mode: Literal["add", "replace"] = "add"


class TopicSuggestRequest(BaseModel):
    base_sha256: str
    locale: Literal["en", "ja"] = "ja"
    llm_provider: Literal["ollama", "openai"] = "ollama"
    vlm_model: str = ""
    utility_model: str = ""
    temperature: float = 1.0
    num_ctx: int = 8192


class TopicSuggestResponse(BaseModel):
    topic: str
    beats: dict[str, str]
    locale: str
    base_sha256: str


_RESPIN_OVERRIDE_FIELDS = (
    "workflow_name", "manual_mode", "llm_provider", "temperature", "num_ctx",
    "vlm_model", "story_model", "time_scale",
    "user_topic", "character_tags", "include_happening", "author_style", "author_id",
    "custom_tags_panel_1", "custom_tags_panel_2", "custom_tags_panel_3",
)


def _merge_respin_overrides(body_dict: dict, body: RespinRequest) -> dict:
    """Merge non-None RespinRequest override fields into a stored context body."""
    out = dict(body_dict or {})
    for key in _RESPIN_OVERRIDE_FIELDS:
        val = getattr(body, key, None)
        if val is not None:
            out[key] = val
    return out


# Temperature ladder for respin — each respin nudges creativity up (Refine's
# _FANOUT_TEMPS idea, applied to whole-story regeneration). Base default is
# Gemma 4's 1.0, so the step is +0.1 to avoid slamming into the 1.3 cap.
def _respin_temperature(base: float, respin_count: int) -> float:
    return min(1.3, round(base + 0.1 * max(1, respin_count), 3))


def _draft_base_temp(story: dict) -> float:
    return float(((story.get("context") or {}).get("body") or {}).get("temperature", 1.0))


def _submit_prompt_job(app, name: str, runner, *, meta: dict, **kwargs) -> str:
    """Submit a PROMPT-lane chronicle job with the shared deps + SSE token queue.

    Registers the queue under its job_id (for /stream) and returns the job_id.
    """
    token_queue: asyncio.Queue = asyncio.Queue()
    job_id = app.state.spooler.submit(
        JobLane.PROMPT, name, runner,
        meta=meta,
        db=app.state.db,
        ollama=app.state.ollama,
        spooler=app.state.spooler,
        comfy=app.state.comfy,
        token_queue=token_queue,
        **kwargs,
    )
    app.state.story_token_queues[job_id] = token_queue
    return job_id


@router.post("/chronicle")
async def start_chronicle(body: ChronicleRequest, request: Request):
    """Phase 1: submit the candidate-generation job, return its job_id + group_id.

    The draft story_id is delivered later via the SSE "candidates" event.
    """
    from ..jobs.runners import run_chronicle_candidates

    if not (body.base_sha256 or "").strip() and not (body.user_topic or "").strip():
        raise HTTPException(
            400,
            "user_topic is required when no base image is provided",
        )

    app = request.app
    body.group_id = f"chr-{uuid.uuid4().hex[:12]}"

    job_id = _submit_prompt_job(
        app, "chronicle_candidates", run_chronicle_candidates,
        meta={
            "group_id": body.group_id,
            "base_sha256": body.base_sha256 or "topic-only",
        },
        body_dict=body.model_dump(),
    )
    return {"job_id": job_id, "group_id": body.group_id, "status": "queued"}


@router.post("/topic-suggest", response_model=TopicSuggestResponse)
async def suggest_topic(body: TopicSuggestRequest, request: Request):
    """Suggest a SHORT 起承転結 お題 from the base image (prefills the topic
    field). Does NOT start a story run.

    A plain awaited call, not a spooler job + SSE: the output is 1–2 sentences
    with nothing to stream, and the PROMPT lane would queue this behind a
    running chronicle, leaving the button dead for minutes. GPU concurrency is
    guarded by LlmGateway.set_resource, which applies to a direct call too.
    """
    from ..jobs.runners import (
        _chronicle_bind_llm,
        _chronicle_llm_options,
        _chronicle_models,
        _vlm_image_bytes,
    )
    from ..runtime_config import get_runtime_config
    from .generator import (
        base_act_from_image,
        build_json_translation_prompt,
        build_topic_suggest_prompt,
        build_vision_prompt,
        character_tags_from_wd14,
        parse_flat_json_translation,
        parse_topic_suggest_json,
        split_vision_sections,
    )

    db = request.app.state.db
    doc = await db.get(body.base_sha256)
    if not doc:
        raise HTTPException(404, "Base image not found")

    cfg = await get_runtime_config(db)
    llm = _chronicle_bind_llm(request.app.state.ollama, body)
    models = _chronicle_models(body, cfg)
    if not (models.get("utility") or models.get("vision") or "").strip():
        raise HTTPException(
            400,
            "No model selected in Chronicle details. Set the story model field.",
        )
    options = _chronicle_llm_options(body, body.temperature, cfg)

    wd14 = doc.get("wd14_tags") or []
    character_tags = character_tags_from_wd14(wd14)

    try:
        async with asyncio.timeout(90):
            if character_tags:
                # WD14 already covers appearance / pose / place, so skip the
                # VLM read entirely (~1–3s instead of ~30s). Mirrors the
                # expand runner's build_vision_prompt(full_extraction=not
                # character_tags) decision.
                desc = "[visual tags] " + ", ".join(character_tags)
                scene = ""
                base_act = base_act_from_image(wd14, "")
            else:
                image_bytes = _vlm_image_bytes(doc)
                vis = await llm.generate_vlm(
                    build_vision_prompt(full_extraction=True), [image_bytes],
                    model=models["vision"], options=options,
                )
                desc, _hooks = split_vision_sections(vis)
                scene = desc
                base_act = {}

            prompt = build_topic_suggest_prompt(
                character_desc=desc,
                scene_desc=scene,
                base_act=base_act,
                worldview="",
            )
            out = {"topic": "", "beats": {}}
            for attempt in range(2):  # one retry, like the arc stage
                raw = await llm.generate_text(
                    prompt, model=models["utility"], options=options,
                    fmt="json", think=False,
                )
                out = parse_topic_suggest_json(raw)
                if out.get("topic"):
                    break
                logger.info("[story] topic-suggest attempt %d unparseable", attempt + 1)
            if not out.get("topic"):
                raise HTTPException(502, "Topic suggestion produced no text")

            # Authored in English on purpose (see build_topic_suggest_prompt);
            # the ja UI gets a batched display translation, like the arc stage.
            if body.locale == "ja":
                src = {"topic": out["topic"], **out["beats"]}
                try:
                    tr = parse_flat_json_translation(
                        await llm.generate_text(
                            build_json_translation_prompt(src, target="Japanese"),
                            model=models["utility"], options=options, fmt="json",
                        ),
                        list(src),
                    )
                    if tr.get("topic"):
                        out["topic"] = tr["topic"]
                        out["beats"] = {
                            k: tr.get(k) or v for k, v in out["beats"].items()
                        }
                except Exception as exc:
                    # The English topic is still usable — the arc prompt says
                    # "follow the topic, do not translate it".
                    logger.warning("[story] topic-suggest ja translation failed: %s", exc)
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(404, "Base image file missing")
    except (TimeoutError, asyncio.TimeoutError):
        raise HTTPException(504, "Topic suggestion timed out")
    except Exception as exc:
        logger.warning("[story] topic-suggest failed: %s", exc)
        raise HTTPException(502, "Topic suggestion failed")

    return TopicSuggestResponse(
        topic=out["topic"],
        beats=out.get("beats") or {},
        locale=body.locale,
        base_sha256=body.base_sha256,
    )


@router.post("/chronicle/{story_id}/select")
async def select_candidate(story_id: str, body: SelectCandidateRequest, request: Request):
    """Phase 2: expand the chosen candidate. Returns a new streaming job_id.

    If the target story is already finalized (the user picked another candidate
    from a completed run), the draft is forked into a fresh story record so the
    previous Storybook entry is not overwritten. Re-expanding on purpose stays
    on the /respin endpoint.
    """
    from ..jobs.runners import run_chronicle_expand

    app = request.app
    story = await story_db.get_story(app.state.db, story_id)
    if story is None:
        raise HTTPException(404, f"Story {story_id!r} not found")

    if story.get("status") == "final":
        story_id = await story_db.fork_draft(app.state.db, story)
        story = await story_db.get_story(app.state.db, story_id)

    from ..story.generator import TIME_SCALES, normalize_time_scale

    # Prefer a *valid* client scale; else Phase-1 story / context.body.
    # Invalid/empty client must not wipe a correct Phase-1 "hours" into years.
    ctx_body = ((story.get("context") or {}).get("body") or {})
    client = str(body.time_scale or "").strip()
    if client in TIME_SCALES:
        raw_scale = client
    else:
        raw_scale = (
            ctx_body.get("time_scale")
            or story.get("time_scale")
            or "years"
        )
    scale = normalize_time_scale(raw_scale)

    job_id = _submit_prompt_job(
        app, "chronicle_expand", run_chronicle_expand,
        meta={"group_id": story.get("group_id", ""), "story_id": story_id},
        story_id=story_id,
        candidate_id=body.candidate_id,
        time_scale=scale,
        temperature=_draft_base_temp(story),
    )
    return {"job_id": job_id, "story_id": story_id, "status": "queued"}


@router.post("/chronicle/{story_id}/respin")
async def respin_chronicle(story_id: str, body: RespinRequest, request: Request):
    """Regenerate candidates or the expanded story at a raised temperature.

    stage="candidates": re-pitch the three candidates (reuses Phase 1 context).
    stage="expand":     re-expand the already-selected candidate.
    Returns a new streaming job_id.
    """
    from ..jobs.runners import run_chronicle_candidates, run_chronicle_expand

    app = request.app
    story = await story_db.get_story(app.state.db, story_id)
    if story is None:
        raise HTTPException(404, f"Story {story_id!r} not found")
    ladder_temp = _respin_temperature(_draft_base_temp(story), body.respin_count)
    temp = body.temperature if body.temperature is not None else ladder_temp
    meta = {"group_id": story.get("group_id", ""), "story_id": story_id}

    if body.stage == "candidates":
        body_dict = (story.get("context") or {}).get("body") or {}
        if not body_dict:
            raise HTTPException(409, "Draft has no stored context for respin")
        body_dict = _merge_respin_overrides(body_dict, body)
        # Persist updated knobs so subsequent respins / expand see them.
        try:
            ctx = dict(story.get("context") or {})
            ctx["body"] = body_dict
            await story_db.set_story_payload(app.state.db, story_id, {"context": ctx})
        except Exception as exc:
            logger.warning("[chronicle] respin context persist failed: %s", exc)
        job_id = _submit_prompt_job(
            app, "chronicle_candidates", run_chronicle_candidates, meta=meta,
            body_dict=body_dict, story_id=story_id, temperature=temp,
        )
    else:  # expand
        candidate_id = story.get("selected_candidate")
        if not candidate_id:
            raise HTTPException(409, "No candidate has been selected yet")
        ctx = dict(story.get("context") or {})
        body_dict = _merge_respin_overrides(ctx.get("body") or {}, body)
        ctx["body"] = body_dict
        try:
            await story_db.set_story_payload(app.state.db, story_id, {"context": ctx})
        except Exception as exc:
            logger.warning("[chronicle] respin expand context persist failed: %s", exc)
        scale = (
            body.time_scale
            or body_dict.get("time_scale")
            or story.get("time_scale")
            or "years"
        )
        job_id = _submit_prompt_job(
            app, "chronicle_expand", run_chronicle_expand, meta=meta,
            story_id=story_id, candidate_id=candidate_id,
            time_scale=scale, temperature=temp,
        )
    return {"job_id": job_id, "story_id": story_id, "status": "queued"}


@router.get("/chronicle/{job_id}/stream")
async def chronicle_stream(job_id: str, request: Request):
    """Stream pipeline events (tokens, phases, prompts, done) via SSE."""
    token_queue: asyncio.Queue | None = request.app.state.story_token_queues.get(job_id)
    if token_queue is None:
        raise HTTPException(404, f"Chronicle job {job_id!r} not found")
    # Never cancel Chronicle jobs on flaky SSE disconnects during long silent
    # LLM phases (concretizing / think=True). Explicit Cancel still works.
    return queue_sse_response(
        request,
        token_queue,
        job_id=job_id,
        registry=request.app.state.story_token_queues,
        encode="json",
        cancel_on_disconnect=False,
        disconnect_grace_seconds=90.0,
        ping_seconds=10.0,
    )


@router.get("/storybook")
async def get_storybook(request: Request, limit: int = 50):
    stories = await story_db.list_stories(request.app.state.db, limit=min(limit, 200))
    return {"stories": stories}


@router.delete("/{story_id}", status_code=204)
async def delete_story_endpoint(story_id: str, request: Request):
    """Delete a story record. Generated images are NOT deleted."""
    await story_db.delete_story(request.app.state.db, story_id)


@router.get("/{story_id}")
async def get_story(story_id: str, request: Request):
    story = await story_db.get_story(request.app.state.db, story_id)
    if story is None:
        raise HTTPException(404, f"Story {story_id!r} not found")
    return story


class GenerateImagesRequest(BaseModel):
    # Manual-mode edited prompts, keyed by axis. Omitted axes use stored prompts.
    axes: dict[str, dict] = {}
    seed: int | None = None
    workflow_name: str = ""


def _submit_axis_image_job(app, story: dict, axis: str, seed: int,
                            workflow_override: str = "") -> str:
    from ..jobs.runners import run_chronicle_image_generate

    axis_data = (story.get("axes") or {}).get(axis) or {}
    positive = axis_data.get("prompt_positive")
    if not positive:
        raise HTTPException(409, f"Axis {axis!r} has no stored prompt")
    workflow_name = workflow_override or story.get("workflow_name")
    if not workflow_name:
        raise HTTPException(409, "Story has no stored workflow_name")

    return app.state.spooler.submit(
        JobLane.GENERATION,
        "chronicle_image",
        run_chronicle_image_generate,
        meta={
            "group_id": story.get("group_id", ""),
            "story_id": story["story_id"],
            "axis": axis,
        },
        db=app.state.db,
        comfy=app.state.comfy,
        story_id=story["story_id"],
        axis=axis,
        workflow_name=workflow_name,
        positive=positive,
        negative=axis_data.get("prompt_negative") or "",
        seed=seed,
        base_sha256=story.get("base_image_id") or "",
    )


@router.post("/{story_id}/generate-images")
async def generate_images(story_id: str, body: GenerateImagesRequest, request: Request):
    """Manual-mode continue: write edited prompts back, then submit image jobs.

    All axes of this batch share one seed (initial-generation consistency rule).
    """
    app = request.app
    db = app.state.db
    story = await story_db.get_story(db, story_id)
    if story is None:
        raise HTTPException(404, f"Story {story_id!r} not found")

    for axis, updates in body.axes.items():
        if axis not in story_db.AXES:
            continue
        allowed = {
            k: updates[k]
            for k in ("prompt_positive", "prompt_negative")
            if k in updates
        }
        if allowed:
            await story_db.update_story_axis(db, story_id, axis, allowed)
    if body.axes:
        story = await story_db.get_story(db, story_id)

    effective_workflow = body.workflow_name or story.get("workflow_name", "")
    if not effective_workflow:
        raise HTTPException(409, "workflow_name is required for image generation")
    if body.workflow_name and not story.get("workflow_name"):
        await story_db.update_story(db, story_id, {"workflow_name": body.workflow_name})

    seed = body.seed if body.seed is not None else random.randint(0, (1 << 64) - 1)
    jobs = []
    for axis in story_db.AXES:
        if not ((story.get("axes") or {}).get(axis) or {}).get("prompt_positive"):
            continue
        job_id = _submit_axis_image_job(app, story, axis, seed,
                                        workflow_override=effective_workflow)
        jobs.append({"axis": axis, "job_id": job_id})
    if not jobs:
        raise HTTPException(409, "No panels with stored prompts to generate")
    return {"status": "queued", "jobs": jobs, "seed": seed}


@router.post("/{story_id}/regenerate/{axis}")
async def regenerate_axis(story_id: str, axis: str, request: Request):
    """Image-only retry for one panel with a fresh seed (story text unchanged)."""
    if axis not in story_db.AXES:
        raise HTTPException(400, f"Unknown panel: {axis!r}")
    story = await story_db.get_story(request.app.state.db, story_id)
    if story is None:
        raise HTTPException(404, f"Story {story_id!r} not found")

    seed = random.randint(0, (1 << 64) - 1)
    job_id = _submit_axis_image_job(request.app, story, axis, seed)
    return {"status": "queued", "job_id": job_id, "axis": axis, "seed": seed}


@router.post("/{story_id}/pinup")
async def generate_pinup(story_id: str, body: PinupRequest, request: Request):
    """Add ('add', a fresh pose) or replace the latest reference pinup for the
    story's base character. The pinup runner builds its own prompt from the
    base image's biography + identity."""
    from ..jobs.runners import run_pinup_image_generate

    app = request.app
    story = await story_db.get_story(app.state.db, story_id)
    if story is None:
        raise HTTPException(404, f"Story {story_id!r} not found")
    base_sha = story.get("base_image_id")
    if not base_sha:
        raise HTTPException(409, "Story has no base image")
    workflow_name = story.get("workflow_name")
    if not workflow_name:
        raise HTTPException(409, "Story has no stored workflow_name")

    job_id = app.state.spooler.submit(
        JobLane.GENERATION,
        "pinup_image",
        run_pinup_image_generate,
        meta={"group_id": story.get("group_id", ""), "story_id": story_id,
              "base_sha256": base_sha},
        db=app.state.db,
        ollama=app.state.ollama,
        comfy=app.state.comfy,
        base_sha256=base_sha,
        story_id=story_id,
        workflow_name=workflow_name,
        seed=None,
        mode=body.mode,
    )
    return {"status": "queued", "job_id": job_id, "mode": body.mode}
