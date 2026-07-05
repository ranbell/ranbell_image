"""Chronicle / Storybook API.

POST /api/story/chronicle              — start the story pipeline (PROMPT lane)
GET  /api/story/chronicle/{id}/stream  — SSE token/event stream for that job
POST /api/story/upload-base            — upload an external base image
GET  /api/story/storybook              — list saved stories (newest first)
GET  /api/story/{story_id}             — one story
POST /api/story/{story_id}/generate-images   — manual-mode continue (writes
                                               edited prompts back, submits jobs)
POST /api/story/{story_id}/regenerate/{axis} — image-only retry with a new seed
"""

import asyncio
import hashlib
import json
import logging
import random
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..config import settings
from ..spooler.models import JobLane
from . import db as story_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/story")

_UPLOAD_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class ChronicleRequest(BaseModel):
    base_sha256: str
    base_time_axis: Literal["past", "present", "future"] = "present"
    worldview: str = ""
    time_scale: Literal["minutes", "hours", "days", "months", "years", "decades"] = "years"
    prompt_style: str = "danbooru+natural"
    workflow_name: str = ""
    divergence: float = 0.0
    use_ref_seed: bool = True
    manual_mode: bool = False
    vlm_model: str = ""
    temperature: float = 0.8
    group_id: str = ""  # issued server-side on submission


@router.post("/chronicle")
async def start_chronicle(body: ChronicleRequest, request: Request):
    """Submit the story pipeline job and return its job_id + group_id."""
    from ..jobs.runners import run_chronicle_story

    app = request.app
    body.group_id = f"chr-{uuid.uuid4().hex[:12]}"

    token_queue: asyncio.Queue = asyncio.Queue()
    job_id = app.state.spooler.submit(
        JobLane.PROMPT,
        "chronicle_story",
        run_chronicle_story,
        meta={"group_id": body.group_id, "base_sha256": body.base_sha256},
        body_dict=body.model_dump(),
        db=app.state.db,
        ollama=app.state.ollama,
        spooler=app.state.spooler,
        comfy=app.state.comfy,
        token_queue=token_queue,
    )
    app.state.story_token_queues[job_id] = token_queue
    return {"job_id": job_id, "group_id": body.group_id, "status": "queued"}


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


@router.post("/upload-base")
async def upload_base(file: UploadFile, request: Request):
    """Save an externally dropped image to Chronicles/ and register it."""
    from ..scanner.scanner import register_image

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _UPLOAD_SUFFIXES:
        raise HTTPException(400, f"Unsupported file type: {suffix or '(none)'}")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")

    sha256 = hashlib.sha256(data).hexdigest()
    gen_dir = settings.generated_images_dir / "Chronicles"
    gen_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = gen_dir / f"upload_{ts}_{sha256[:8]}{suffix}"

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, path.write_bytes, data)
    try:
        await register_image(path, request.app.state.db)
    except Exception as exc:
        logger.error("upload-base register_image failed: %s", exc)
        raise HTTPException(500, f"Image registration failed: {exc}")
    return {"sha256": sha256, "path": str(path)}


@router.get("/storybook")
async def get_storybook(request: Request, limit: int = 50):
    stories = await story_db.list_stories(request.app.state.db, limit=min(limit, 200))
    return {"stories": stories}


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


def _submit_axis_image_job(app, story: dict, axis: str, seed: int) -> str:
    from ..jobs.runners import run_chronicle_image_generate

    axis_data = (story.get("axes") or {}).get(axis) or {}
    positive = axis_data.get("prompt_positive")
    if not positive:
        raise HTTPException(409, f"Axis {axis!r} has no stored prompt")
    workflow_name = story.get("workflow_name")
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
    for axis, updates in body.axes.items():
        if axis == base_axis or axis not in story_db.AXES:
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

    seed = body.seed if body.seed is not None else random.randint(0, (1 << 64) - 1)
    jobs = []
    for axis in story_db.AXES:
        if axis == base_axis:
            continue
        if not ((story.get("axes") or {}).get(axis) or {}).get("prompt_positive"):
            continue
        job_id = _submit_axis_image_job(app, story, axis, seed)
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
    if axis == story.get("base_time_axis"):
        raise HTTPException(409, "The base axis image cannot be regenerated")

    seed = random.randint(0, (1 << 64) - 1)
    job_id = _submit_axis_image_job(request.app, story, axis, seed)
    return {"status": "queued", "job_id": job_id, "axis": axis, "seed": seed}
