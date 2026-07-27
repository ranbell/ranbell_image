"""Storybook / story record API (Weave is the creation UI).

POST /api/story/topic-suggest          — short topic from a base image
GET  /api/story/storybook              — list saved stories (newest first)
GET  /api/story/{story_id}             — one story
GET  /api/story/{story_id}/eval-bundle — eval JSON (prompts + image URLs)
POST /api/story/{story_id}/export-eval — write report + panel PNGs to disk
POST /api/story/{story_id}/generate-images   — manual-mode continue
POST /api/story/{story_id}/regenerate/{axis} — image-only retry with a new seed
POST /api/story/{story_id}/pinup|snap|novel
DELETE /api/story/{story_id}
"""

import asyncio
import logging
import random
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..spooler.models import JobLane
from . import db as story_db
from .eval_export import build_eval_bundle, default_eval_root, export_eval_bundle

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/story")


class ExportEvalRequest(BaseModel):
    export_dir: str = ""

class PinupRequest(BaseModel):
    mode: Literal["add", "replace"] = "add"


class SnapRequest(BaseModel):
    axis: str  # panel_1 | panel_2 | panel_3 — the generated panel to snap


class NovelRequest(BaseModel):
    pass


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

async def _image_docs_for_story(db, story: dict) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for axis in story_db.AXES:
        sha = ((story.get("axes") or {}).get(axis) or {}).get("image_id") or ""
        if not sha or sha in docs:
            continue
        doc = await db.get(sha)
        if doc:
            docs[sha] = doc
    return docs


@router.post("/topic-suggest", response_model=TopicSuggestResponse)
async def suggest_topic(body: TopicSuggestRequest, request: Request):
    """Suggest a SHORT 起承転結 お題 from the base image (prefills the topic
    field). Does NOT start a story run.

    A plain awaited call, not a spooler job + SSE: the output is 1–2 sentences
    with nothing to stream, and the PROMPT lane would queue this behind a
    long LLM job, leaving the button dead for minutes. GPU concurrency is
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
            "No model selected. Set story_model / vlm_model.",
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

@router.get("/storybook")
async def get_storybook(request: Request, limit: int = 50):
    stories = await story_db.list_stories(request.app.state.db, limit=min(limit, 200))
    return {"stories": stories}


@router.delete("/{story_id}", status_code=204)
async def delete_story_endpoint(story_id: str, request: Request):
    """Delete a story record. Generated images are NOT deleted."""
    await story_db.delete_story(request.app.state.db, story_id)


@router.get("/{story_id}/eval-bundle")
async def get_eval_bundle(story_id: str, request: Request):
    """Agent-facing JSON: prompts, narratives, image URLs, quality_eval."""
    story = await story_db.get_story(request.app.state.db, story_id)
    if story is None:
        raise HTTPException(404, f"Story {story_id!r} not found")
    docs = await _image_docs_for_story(request.app.state.db, story)
    return build_eval_bundle(story, db_docs=docs)


@router.post("/{story_id}/export-eval")
async def export_eval(story_id: str, body: ExportEvalRequest, request: Request):
    """Write report.json + panel PNGs under chronicle_evals/ (or export_dir)."""
    story = await story_db.get_story(request.app.state.db, story_id)
    if story is None:
        raise HTTPException(404, f"Story {story_id!r} not found")
    docs = await _image_docs_for_story(request.app.state.db, story)
    out = Path(body.export_dir) if (body.export_dir or "").strip() else None
    try:
        meta = export_eval_bundle(story, db_docs=docs, out_dir=out)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "story_id": meta["story_id"],
        "export_dir": meta["export_dir"],
        "report_path": meta["report_path"],
        "copied_panels": meta["copied_panels"],
        "missing_panels": meta["missing_panels"],
        "default_root": str(default_eval_root()),
    }


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


@router.post("/{story_id}/snap")
async def register_snap(story_id: str, body: SnapRequest, request: Request):
    """Register a 'snap' character reference from a generated panel (no base image).

    Extracts the panel's WD14 features, infers a biography, and renders a
    neutral-pose reference linked onto the story's pinups[]."""
    from ..jobs.runners import run_snap_image_generate

    app = request.app
    axis = (body.axis or "").strip()
    if axis not in story_db.AXES:
        raise HTTPException(400, f"Unknown panel: {axis!r}")
    story = await story_db.get_story(app.state.db, story_id)
    if story is None:
        raise HTTPException(404, f"Story {story_id!r} not found")
    panel_sha = ((story.get("axes") or {}).get(axis) or {}).get("image_id") or ""
    if not panel_sha:
        raise HTTPException(409, f"Panel {axis!r} has no generated image yet")
    workflow_name = story.get("workflow_name")
    if not workflow_name:
        raise HTTPException(409, "Story has no stored workflow_name")

    body_ctx = (story.get("context") or {}).get("body") or {}
    author_style = story.get("author_style") or body_ctx.get("author_style") or ""
    model = body_ctx.get("story_model") or body_ctx.get("vlm_model") or None

    job_id = app.state.spooler.submit(
        JobLane.GENERATION,
        "snap_image",
        run_snap_image_generate,
        meta={"group_id": story.get("group_id", ""), "story_id": story_id, "axis": axis},
        db=app.state.db,
        ollama=app.state.ollama,
        comfy=app.state.comfy,
        story_id=story_id,
        axis=axis,
        panel_sha256=panel_sha,
        workflow_name=workflow_name,
        author_style=author_style,
        model=model,
        seed=None,
    )
    return {"status": "queued", "job_id": job_id, "axis": axis}


@router.post("/{story_id}/novel")
async def generate_novel(story_id: str, body: NovelRequest, request: Request):
    """Write a short scene-by-scene novel (one paragraph per panel) in the
    chronicle's author voice, and store it on the story as ``prose_scenes``."""
    from ..story.generator import build_novel_prompt, parse_novel_json

    app = request.app
    story = await story_db.get_story(app.state.db, story_id)
    if story is None:
        raise HTTPException(404, f"Story {story_id!r} not found")

    axes = story.get("axes") or {}
    scenes_in: list[dict] = []
    for axis in story_db.AXES:
        a = axes.get(axis) or {}
        tags = [t.strip() for t in str(a.get("prompt_positive") or "").split(",") if t.strip()]
        scenes_in.append({
            "narrative": a.get("story_ja") or a.get("story") or "",
            "tags": tags,
        })
    if not any(s["narrative"] for s in scenes_in):
        raise HTTPException(409, "Story has no panel narratives to write from")

    body_ctx = (story.get("context") or {}).get("body") or {}
    author_style = story.get("author_style") or body_ctx.get("author_style") or ""
    model = body_ctx.get("story_model") or body_ctx.get("vlm_model") or None
    prompt = build_novel_prompt(
        author_style=author_style,
        title=story.get("title") or "",
        overall=story.get("overall_story") or "",
        scenes=scenes_in,
        locale="ja",
    )
    try:
        raw = await app.state.ollama.chat_text(
            prompt, model=model,
            options={"num_ctx": 16384, "temperature": 0.8}, fmt=None,
        )
    except Exception as exc:
        raise HTTPException(502, f"Novel generation failed: {exc}")
    prose = parse_novel_json(raw)
    await story_db.set_story_payload(app.state.db, story_id, {"prose_scenes": prose})
    return {"status": "ok", "prose_scenes": prose}
