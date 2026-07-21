"""Agent-facing Chronicle run orchestration (no SSE required).

Starts Phase1 → auto-select → Phase2 → wait for panel images → optional export.
State is polled via GET /api/story/chronicle/run/{run_id}.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .eval_export import export_eval_bundle

logger = logging.getLogger(__name__)

TERMINAL = frozenset({"done", "error"})
AXES = ("panel_1", "panel_2", "panel_3")


@dataclass
class AgentRun:
    run_id: str
    status: str = "queued"
    story_id: str | None = None
    group_id: str | None = None
    candidate_id: str = "A"
    candidates: list | None = None
    image_jobs: list[dict] | None = None
    quality_eval: dict | None = None
    export_dir: str | None = None
    export_meta: dict | None = None
    error: str | None = None
    phase1_job_id: str | None = None
    phase2_job_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "story_id": self.story_id,
            "group_id": self.group_id,
            "candidate_id": self.candidate_id,
            "candidates": self.candidates,
            "image_jobs": self.image_jobs,
            "quality_eval": self.quality_eval,
            "export_dir": self.export_dir,
            "export_meta": (
                {k: v for k, v in (self.export_meta or {}).items() if k != "bundle"}
                if self.export_meta
                else None
            ),
            "error": self.error,
            "phase1_job_id": self.phase1_job_id,
            "phase2_job_id": self.phase2_job_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def get_runs(app) -> dict[str, AgentRun]:
    store = getattr(app.state, "chronicle_agent_runs", None)
    if store is None:
        store = {}
        app.state.chronicle_agent_runs = store
    return store


async def drain_token_queue(
    token_queue: asyncio.Queue,
    *,
    until_types: set[str],
    timeout_sec: float,
    on_event: Callable[[dict], None] | None = None,
) -> dict:
    """Consume queue events until one of ``until_types`` (or error/None sentinel)."""
    deadline = time.monotonic() + timeout_sec
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for {sorted(until_types)}")
        try:
            evt = await asyncio.wait_for(token_queue.get(), timeout=remaining)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"timed out waiting for {sorted(until_types)}") from exc
        if evt is None:
            raise RuntimeError("job ended without expected event")
        if not isinstance(evt, dict):
            continue
        if on_event:
            on_event(evt)
        et = evt.get("type")
        if et == "error":
            raise RuntimeError(str(evt.get("message") or "chronicle error"))
        if et in until_types:
            return evt


async def wait_for_panel_images(
    db,
    story_id: str,
    *,
    timeout_sec: float,
    poll_sec: float = 2.0,
    required_axes: tuple[str, ...] = AXES,
    get_story_fn: Callable | None = None,
) -> dict:
    """Poll story until every required axis has image_id (or timeout)."""
    if get_story_fn is not None:
        getter = get_story_fn
    else:
        from . import db as story_db
        getter = story_db.get_story
    deadline = time.monotonic() + timeout_sec
    while True:
        story = await getter(db, story_id)
        if not story:
            raise RuntimeError(f"story {story_id} disappeared while waiting for images")
        axes = story.get("axes") or {}
        missing = [
            a for a in required_axes
            if not ((axes.get(a) or {}).get("image_id") or "").strip()
        ]
        if not missing:
            return story
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"images not ready for {story_id}; missing={missing}"
            )
        await asyncio.sleep(poll_sec)


async def _load_image_docs(db, story: dict) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for axis in AXES:
        sha = ((story.get("axes") or {}).get(axis) or {}).get("image_id") or ""
        if not sha or sha in docs:
            continue
        doc = await db.get(sha)
        if doc:
            docs[sha] = doc
    return docs


async def execute_agent_run(
    app,
    run: AgentRun,
    *,
    body_dict: dict,
    candidate_id: str,
    timeout_sec: float,
    wait_images: bool,
    do_export: bool,
    export_dir: str | None,
    submit_prompt_job: Callable[..., str],
) -> None:
    """Drive Phase1 → select → images → export. Mutates ``run`` in place."""
    from ..jobs.runners import run_chronicle_candidates, run_chronicle_expand
    from ..story.generator import TIME_SCALES, normalize_time_scale
    from . import db as story_db

    db = app.state.db
    run.candidate_id = candidate_id
    run.group_id = body_dict.get("group_id") or f"chr-{uuid.uuid4().hex[:12]}"
    body_dict["group_id"] = run.group_id
    run.touch()

    # Budget: phase1 / phase2 / images share the overall timeout.
    t0 = time.monotonic()

    def _remaining() -> float:
        return max(1.0, timeout_sec - (time.monotonic() - t0))

    try:
        # ── Phase 1 ──────────────────────────────────────────────────────────
        run.status = "candidates"
        run.touch()
        phase1_id = submit_prompt_job(
            app,
            "chronicle_candidates",
            run_chronicle_candidates,
            meta={
                "group_id": run.group_id,
                "base_sha256": body_dict.get("base_sha256") or "topic-only",
                "agent_run_id": run.run_id,
            },
            body_dict=body_dict,
        )
        run.phase1_job_id = phase1_id
        run.touch()
        q1 = app.state.story_token_queues[phase1_id]
        cand_evt = await drain_token_queue(
            q1,
            until_types={"candidates"},
            timeout_sec=_remaining(),
        )
        run.story_id = cand_evt.get("story_id")
        run.candidates = cand_evt.get("candidates") or []
        if not run.story_id:
            raise RuntimeError("Phase1 returned no story_id")

        ids = {str(c.get("id") or "") for c in run.candidates if isinstance(c, dict)}
        if candidate_id not in ids and run.candidates:
            # Fall back to first candidate if requested id missing.
            first = str((run.candidates[0] or {}).get("id") or "A")
            logger.warning(
                "[agent_run] candidate %s missing; using %s",
                candidate_id,
                first,
            )
            candidate_id = first
            run.candidate_id = candidate_id

        # ── Phase 2 ──────────────────────────────────────────────────────────
        run.status = "expanding"
        run.touch()
        story = await story_db.get_story(db, run.story_id)
        if not story:
            raise RuntimeError(f"draft story {run.story_id} not found")
        ctx_body = ((story.get("context") or {}).get("body") or {})
        raw_scale = (
            body_dict.get("time_scale")
            or ctx_body.get("time_scale")
            or story.get("time_scale")
            or "days"
        )
        if raw_scale not in TIME_SCALES:
            raw_scale = "days"
        scale = normalize_time_scale(raw_scale)
        base_temp = float(ctx_body.get("temperature") or body_dict.get("temperature") or 1.0)

        phase2_id = submit_prompt_job(
            app,
            "chronicle_expand",
            run_chronicle_expand,
            meta={
                "group_id": run.group_id,
                "story_id": run.story_id,
                "agent_run_id": run.run_id,
            },
            story_id=run.story_id,
            candidate_id=candidate_id,
            time_scale=scale,
            temperature=base_temp,
        )
        run.phase2_job_id = phase2_id
        run.touch()
        q2 = app.state.story_token_queues[phase2_id]

        image_jobs: list[dict] = []
        done_evt: dict | None = None

        def _on_expand(evt: dict) -> None:
            nonlocal image_jobs
            if evt.get("type") == "image_jobs":
                image_jobs = list(evt.get("jobs") or [])
            if evt.get("type") == "quality_eval":
                qe = {k: v for k, v in evt.items() if k != "type"}
                run.quality_eval = qe

        done_evt = await drain_token_queue(
            q2,
            until_types={"done"},
            timeout_sec=_remaining(),
            on_event=_on_expand,
        )
        run.image_jobs = image_jobs or done_evt.get("image_jobs")
        if done_evt.get("story_id"):
            run.story_id = done_evt["story_id"]

        # Refresh quality_eval from persisted story if SSE missed it.
        story = await story_db.get_story(db, run.story_id)
        if story and story.get("quality_eval") and not run.quality_eval:
            run.quality_eval = story["quality_eval"]

        # ── Wait for images ──────────────────────────────────────────────────
        manual = bool(body_dict.get("manual_mode"))
        workflow = (body_dict.get("workflow_name") or "").strip()
        if wait_images and not manual and workflow:
            run.status = "generating"
            run.touch()
            story = await wait_for_panel_images(
                db,
                run.story_id,
                timeout_sec=_remaining(),
            )
            if story.get("quality_eval"):
                run.quality_eval = story["quality_eval"]
        elif wait_images and (manual or not workflow):
            logger.info(
                "[agent_run] skip image wait (manual=%s workflow=%r)",
                manual,
                workflow,
            )

        # ── Export ───────────────────────────────────────────────────────────
        if do_export and run.story_id:
            story = await story_db.get_story(db, run.story_id)
            if not story:
                raise RuntimeError("story missing before export")
            docs = await _load_image_docs(db, story)
            out = Path(export_dir) if export_dir else None
            meta = export_eval_bundle(story, db_docs=docs, out_dir=out)
            run.export_dir = meta.get("export_dir")
            run.export_meta = meta
            if not run.quality_eval:
                run.quality_eval = (meta.get("bundle") or {}).get("quality_eval")

        run.status = "done"
        run.touch()
    except Exception as exc:
        logger.exception("[agent_run] %s failed", run.run_id)
        run.status = "error"
        run.error = str(exc)[:800]
        run.touch()


def start_agent_run(
    app,
    *,
    body_dict: dict,
    candidate_id: str = "A",
    timeout_sec: float = 1800.0,
    wait_images: bool = True,
    do_export: bool = True,
    export_dir: str | None = None,
    submit_prompt_job: Callable[..., str],
) -> AgentRun:
    """Create run state and schedule background orchestration."""
    run_id = f"arun-{uuid.uuid4().hex[:12]}"
    run = AgentRun(run_id=run_id, status="queued", candidate_id=candidate_id)
    get_runs(app)[run_id] = run

    async def _task() -> None:
        await execute_agent_run(
            app,
            run,
            body_dict=body_dict,
            candidate_id=candidate_id,
            timeout_sec=timeout_sec,
            wait_images=wait_images,
            do_export=do_export,
            export_dir=export_dir,
            submit_prompt_job=submit_prompt_job,
        )

    asyncio.create_task(_task(), name=f"chronicle-agent-run-{run_id}")
    return run
