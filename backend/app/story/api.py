"""Chronicle / Storybook API.

POST /api/story/chronicle                    — Phase 1: pitch 3 candidates
POST /api/story/chronicle/{story_id}/select  — Phase 2: expand chosen candidate
POST /api/story/chronicle/{story_id}/respin  — regenerate (candidates | expand)
GET  /api/story/chronicle/{job_id}/stream    — SSE token/event stream for a job
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

from ..spooler.models import JobLane
from . import db as story_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/story")


class ChronicleRequest(BaseModel):
    # Empty → topic-only mode (no source image); then user_topic is required.
    base_sha256: str = ""
    base_time_axis: Literal["past", "present", "future"] = "present"
    worldview: str = ""
    user_topic: str = ""  # お題 — what the story is about (separate from worldview)
    time_scale: Literal["minutes", "tens_of_minutes", "hours", "days", "months", "years", "decades"] = "years"
    prompt_style: str = "danbooru+natural"
    workflow_name: str = ""
    divergence: float = 0.0
    emotion: str = ""  # target emotion register ('' = off; see emotion_tagger.EMOTION_DIMENSIONS)
    dramatic_mode: str = ""  # preferred story-shape ('' = auto-vary; see generator._DRAMATIC_MODES)
    tone: Literal["bright", "neutral", "dark"] = "bright"  # overall story tone bias
    suppress_conflict_tags: bool = True  # run the per-axis story-conflict tag removal (Refine parity)
    generate_pinup: bool = False  # generate + register a reference "pinup" for the base image
    use_ref_seed: bool = True
    manual_mode: bool = False
    # Phase B: cheap draft → WD14 → rebuild (borrow image-model expression).
    # auto = on for any non-micro scale, or divergence ≥ 0.25; on/off force.
    use_draft_refine: Literal["auto", "on", "off"] = "auto"
    draft_width: int = 512
    draft_height: int = 512
    draft_steps: int = 12
    vlm_model: str = ""
    temperature: float = 1.0  # Gemma 4 recommended default
    num_ctx: int = 16384
    locale: Literal["en", "ja"] = "en"  # language the story is written in
    group_id: str = ""  # issued server-side on submission


class SelectCandidateRequest(BaseModel):
    candidate_id: str
    time_scale: str = ""  # empty → keep the scale chosen at Phase 1


class RespinRequest(BaseModel):
    stage: Literal["candidates", "expand"]
    respin_count: int = 1


class PinupRequest(BaseModel):
    mode: Literal["add", "replace"] = "add"


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

    job_id = _submit_prompt_job(
        app, "chronicle_expand", run_chronicle_expand,
        meta={"group_id": story.get("group_id", ""), "story_id": story_id},
        story_id=story_id,
        candidate_id=body.candidate_id,
        time_scale=body.time_scale or story.get("time_scale", "years"),
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
    temp = _respin_temperature(_draft_base_temp(story), body.respin_count)
    meta = {"group_id": story.get("group_id", ""), "story_id": story_id}

    if body.stage == "candidates":
        body_dict = (story.get("context") or {}).get("body") or {}
        if not body_dict:
            raise HTTPException(409, "Draft has no stored context for respin")
        job_id = _submit_prompt_job(
            app, "chronicle_candidates", run_chronicle_candidates, meta=meta,
            body_dict=body_dict, story_id=story_id, temperature=temp,
        )
    else:  # expand
        candidate_id = story.get("selected_candidate")
        if not candidate_id:
            raise HTTPException(409, "No candidate has been selected yet")
        job_id = _submit_prompt_job(
            app, "chronicle_expand", run_chronicle_expand, meta=meta,
            story_id=story_id, candidate_id=candidate_id,
            time_scale=story.get("time_scale", "years"), temperature=temp,
        )
    return {"job_id": job_id, "story_id": story_id, "status": "queued"}


@router.get("/chronicle/{job_id}/stream")
async def chronicle_stream(job_id: str, request: Request):
    """Stream pipeline events (tokens, phases, prompts, done) via SSE."""
    token_queue: asyncio.Queue | None = request.app.state.story_token_queues.get(job_id)
    if token_queue is None:
        raise HTTPException(404, f"Chronicle job {job_id!r} not found")

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    await request.app.state.spooler.cancel(job_id)
                    break
                try:
                    item = await asyncio.wait_for(token_queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                if item is None:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        finally:
            request.app.state.story_token_queues.pop(job_id, None)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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

    base_axis = story.get("base_time_axis")
    has_base_image = bool(story.get("base_image_id"))
    for axis, updates in body.axes.items():
        # With a source image, the base axis is the 元絵 — don't overwrite its prompts.
        # Topic-only stories generate all three axes, so edits are allowed.
        if (has_base_image and axis == base_axis) or axis not in story_db.AXES:
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
        if has_base_image and axis == base_axis:
            continue
        if not ((story.get("axes") or {}).get(axis) or {}).get("prompt_positive"):
            continue
        job_id = _submit_axis_image_job(app, story, axis, seed,
                                        workflow_override=effective_workflow)
        jobs.append({"axis": axis, "job_id": job_id})
    if not jobs:
        raise HTTPException(409, "No axes with stored prompts to generate")
    return {"status": "queued", "jobs": jobs, "seed": seed}


@router.post("/{story_id}/regenerate/{axis}")
async def regenerate_axis(story_id: str, axis: str, request: Request):
    """Image-only retry for one axis with a fresh seed (story text unchanged)."""
    if axis not in story_db.AXES:
        raise HTTPException(400, f"Unknown axis: {axis!r}")
    story = await story_db.get_story(request.app.state.db, story_id)
    if story is None:
        raise HTTPException(404, f"Story {story_id!r} not found")
    if axis == story.get("base_time_axis") and story.get("base_image_id"):
        raise HTTPException(409, "The base axis image cannot be regenerated")

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
