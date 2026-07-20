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
    base_sha256: str = ""
    base_time_axis: Literal["past", "present", "future"] = "present"
    life_role: str = ""  # student_cafe_job | freeter_multi_job | career_barista | custom | random
    worldview: str = ""
    user_topic: str = ""  # お題 — what the story is about (separate from worldview)
    time_scale: Literal["minutes", "tens_of_minutes", "hours", "days", "months", "years", "decades"] = "years"
    prompt_style: str = "danbooru+natural"
    workflow_name: str = ""
    divergence: float = 0.0
    emotion: str = ""  # target emotion register ('' = off; see emotion_tagger.EMOTION_DIMENSIONS)
    dramatic_mode: str = ""  # preferred story-shape ('' = auto-vary; see generator._DRAMATIC_MODES)
    tone: Literal["bright", "neutral", "dark"] = "bright"  # overall story tone bias
    # Deprecated (accepted, ignored): the LLM conflict pass was replaced by
    # mechanical mutex rules only.
    suppress_conflict_tags: bool = True
    generate_pinup: bool = False  # generate + register a reference "pinup" for the base image
    use_ref_seed: bool = True
    manual_mode: bool = False
    # Skip timetable / long prose / densify / draft-refine: お題 → one-shot
    # lean tag prompts → Comfy. Theme costume tags are hard-kept.
    fast_mode: bool = False
    # Deprecated spice: story-seed WD14 + axis vocab search + fast mid-rank.
    # Default OFF — usually adds unrelated tags. Draft-image WD14 is separate.
    wd14_prompt_spice: bool = False
    # Mix WD14 tags from images similar to the axis situation (embed → search).
    # Raises visual resolution with grounded vocabulary. Default ON.
    similar_tag_mix: bool = True
    similar_tag_mix_ratio: float = 0.3  # fraction of tag budget from similar images
    similar_tag_mix_n: int = 4  # near-but-different neighbor count (3–6)
    # Phase B: cheap draft → WD14 → rebuild (borrow image-model expression).
    # Opt-in only: "on" enables it; "auto" is treated as OFF (it added a full
    # ComfyUI render + WD14 scan per axis for marginal gain).
    use_draft_refine: Literal["auto", "on", "off"] = "off"
    draft_width: int = 512
    draft_height: int = 512
    draft_steps: int = 12
    # Per-run LLM backend. Default ollama — OpenAI-compat only when chosen here.
    # Other app features ignore this and always use Ollama via the shared gateway.
    llm_provider: Literal["ollama", "openai"] = "ollama"
    vlm_model: str = ""  # all-tier override (kept for backward compatibility)
    # Stage-tiered overrides (Ollama provider only): story = creative arc/polish,
    # utility = translations. Empty → runtime config tier → vlm_model. Vision
    # always uses vlm_model; llm_provider="openai" collapses all tiers.
    story_model: str = ""
    utility_model: str = ""
    # Native `think` for creative story calls (None → runtime config default).
    story_think: bool | None = None
    temperature: float = 1.0  # Gemma 4 recommended default
    num_ctx: int = 32768
    # Visual Script prose length (3–7) → per-act word budget via
    # generator.chronicle_prose_budget. Ignored in fast_mode (tags only, no
    # prose stage).
    prose_paragraphs: int = 3
    locale: Literal["en", "ja"] = "en"  # language the story is written in
    group_id: str = ""  # issued server-side on submission


class SelectCandidateRequest(BaseModel):
    candidate_id: str
    time_scale: str = ""  # empty → keep the scale chosen at Phase 1


class RespinRequest(BaseModel):
    stage: Literal["candidates", "expand"]
    respin_count: int = 1
    # Optional overrides — same knobs as ChronicleRequest; None = keep stored.
    time_scale: Literal[
        "minutes", "tens_of_minutes", "hours", "days", "months", "years", "decades"
    ] | None = None
    divergence: float | None = None
    emotion: str | None = None
    dramatic_mode: str | None = None
    tone: Literal["bright", "neutral", "dark"] | None = None
    prompt_style: str | None = None
    workflow_name: str | None = None
    use_draft_refine: Literal["auto", "on", "off"] | None = None
    draft_width: int | None = None
    draft_height: int | None = None
    draft_steps: int | None = None
    suppress_conflict_tags: bool | None = None
    manual_mode: bool | None = None
    fast_mode: bool | None = None
    wd14_prompt_spice: bool | None = None
    similar_tag_mix: bool | None = None
    similar_tag_mix_ratio: float | None = None
    similar_tag_mix_n: int | None = None
    llm_provider: Literal["ollama", "openai"] | None = None
    temperature: float | None = None
    num_ctx: int | None = None
    prose_paragraphs: int | None = None
    worldview: str | None = None
    user_topic: str | None = None
    life_role: str | None = None


class PinupRequest(BaseModel):
    mode: Literal["add", "replace"] = "add"


class TopicSuggestRequest(BaseModel):
    base_sha256: str
    locale: Literal["en", "ja"] = "ja"
    worldview: str = ""
    llm_provider: Literal["ollama", "openai"] = "ollama"
    vlm_model: str = ""
    utility_model: str = ""
    temperature: float = 1.0
    num_ctx: int = 8192


class TopicSuggestResponse(BaseModel):
    topic: str  # 1–2 sentences, prefills the お題 field
    beats: dict[str, str]  # {"ki","shou","ten","ketsu"} — for display
    locale: str
    base_sha256: str


_RESPIN_OVERRIDE_FIELDS = (
    "time_scale", "divergence", "emotion", "dramatic_mode", "tone",
    "prompt_style", "workflow_name", "use_draft_refine", "draft_width",
    "draft_height", "draft_steps", "suppress_conflict_tags", "manual_mode",
    "fast_mode", "wd14_prompt_spice",
    "similar_tag_mix", "similar_tag_mix_ratio", "similar_tag_mix_n",
    "llm_provider", "temperature", "num_ctx", "prose_paragraphs",
    "worldview", "user_topic", "life_role",
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
                worldview=body.worldview,
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
