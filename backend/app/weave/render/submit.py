"""Submit Weave GEN-lane jobs via the spooler."""
from __future__ import annotations

import logging
import random
from typing import Any

from ...spooler.models import JobLane
from .prompts import compile_board_slot, compile_panel_render

logger = logging.getLogger(__name__)


def _workflow(session: dict[str, Any], *, sample: bool) -> str:
    inputs = session.get("inputs") or {}
    if sample:
        return (
            str(inputs.get("workflow_sample") or "").strip()
            or str(inputs.get("workflow_final") or "").strip()
        )
    return str(inputs.get("workflow_final") or "").strip()


def _bound_llm(app, session: dict[str, Any]):
    gw = getattr(app.state, "ollama", None)
    if gw is None:
        return None
    provider = str((session.get("inputs") or {}).get("llm_provider") or "ollama")
    bind = getattr(gw, "bind", None)
    if callable(bind):
        return bind(provider)
    return gw


def submit_board_jobs(app, session_id: str, session: dict[str, Any]) -> list[dict[str, Any]]:
    from ...jobs.runners import run_weave_image_generate
    from ..character.board_slots import resolve_board_slots, sync_board_briefs

    workflow = _workflow(session, sample=True)
    if not workflow:
        raise ValueError("workflow_final or workflow_sample is required for board render")

    sync_board_briefs(session)
    slots = resolve_board_slots(session)
    briefs = (session.get("character") or {}).get("board_briefs") or [
        {"slot": s} for s in slots
    ]
    board = session.setdefault("character", {}).setdefault("board", {})
    images: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    base_sha = str((session.get("inputs") or {}).get("reference_image_id") or "")
    llm = _bound_llm(app, session)

    for b in briefs:
        slot = str(b.get("slot") or "").strip()
        if not slot:
            continue
        compiled = compile_board_slot(session, slot)
        seed = random.randint(0, (1 << 64) - 1)
        job_id = app.state.spooler.submit(
            JobLane.GENERATION,
            "weave_board",
            run_weave_image_generate,
            meta={
                "session_id": session_id,
                "kind": "board",
                "slot": slot,
            },
            db=app.state.db,
            comfy=app.state.comfy,
            session_id=session_id,
            kind="board",
            target=slot,
            workflow_name=workflow,
            positive=compiled["positive"],
            negative=compiled["negative"],
            seed=seed,
            steps=20,
            base_sha256=base_sha,
            ollama=llm,
        )
        images.append({
            "slot": slot,
            "camera": compiled.get("camera"),
            "image_id": None,
            "positive": compiled["positive"],
            "negative": compiled["negative"],
            "job_id": job_id,
            "pending": True,
        })
        jobs.append({"slot": slot, "job_id": job_id, "kind": "board"})

    board["images"] = images
    board["accepted"] = False
    return jobs


def _multi_seed_count(session: dict[str, Any]) -> int:
    policy = session.get("quality_policy") or {}
    try:
        n = int(policy.get("multi_seed") or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(3, n))


def submit_sample_job(
    app,
    session_id: str,
    session: dict[str, Any],
    panel_key: str,
) -> dict[str, Any]:
    """Queue 1..3 sample seeds. Returns primary job + optional ``jobs`` list."""
    from ...jobs.runners import run_weave_image_generate

    workflow = _workflow(session, sample=True)
    if not workflow:
        raise ValueError("workflow_final or workflow_sample is required for sample render")

    compiled = compile_panel_render(session, panel_key)
    inputs = session.get("inputs") or {}
    base_sha = str(inputs.get("reference_image_id") or "")
    steps_raw = inputs.get("sample_steps")
    try:
        steps = int(steps_raw) if steps_raw is not None else 20
    except (TypeError, ValueError):
        steps = 20
    steps = max(8, min(40, steps))
    llm = _bound_llm(app, session)
    n = _multi_seed_count(session)
    jobs: list[dict[str, Any]] = []
    for i in range(n):
        seed = random.randint(0, (1 << 64) - 1)
        job_id = app.state.spooler.submit(
            JobLane.GENERATION,
            "weave_sample",
            run_weave_image_generate,
            meta={
                "session_id": session_id,
                "kind": "sample",
                "panel_key": panel_key,
                "seed_index": i,
            },
            db=app.state.db,
            comfy=app.state.comfy,
            session_id=session_id,
            kind="sample",
            target=panel_key,
            workflow_name=workflow,
            positive=compiled["positive"],
            negative=compiled["negative"],
            seed=seed,
            steps=steps,
            base_sha256=base_sha,
            ollama=llm,
        )
        jobs.append({
            "panel_key": panel_key,
            "job_id": job_id,
            "kind": "sample",
            "seed": seed,
            "seed_index": i,
        })

    panel = next((p for p in session.get("panels") or [] if p.get("key") == panel_key), None)
    primary = jobs[0]
    if panel is not None:
        panel["sample"] = {
            "image_id": None,
            "job_id": primary["job_id"],
            "scorecard": None,
            "multi_seed": n,
        }
        hist = list(panel.get("sample_history") or [])
        for j in jobs:
            hist.append({
                "job_id": j["job_id"],
                "seed": j["seed"],
                "image_id": None,
                "pending": True,
            })
        panel["sample_history"] = hist[-9:]  # cap
        if panel.get("compile"):
            panel["compile"]["positive"] = compiled["positive"]
            panel["compile"]["negative"] = compiled["negative"]
    return {
        "panel_key": panel_key,
        "job_id": primary["job_id"],
        "kind": "sample",
        "jobs": jobs,
        "multi_seed": n,
    }


def submit_final_jobs(app, session_id: str, session: dict[str, Any]) -> list[dict[str, Any]]:
    from ...jobs.runners import run_weave_image_generate

    workflow = _workflow(session, sample=False)
    if not workflow:
        raise ValueError("workflow_final is required for final render")

    jobs: list[dict[str, Any]] = []
    base_sha = str((session.get("inputs") or {}).get("reference_image_id") or "")
    llm = _bound_llm(app, session)
    n = _multi_seed_count(session)
    for panel_key in ("panel_1", "panel_2", "panel_3"):
        compiled = compile_panel_render(session, panel_key)
        # Final: primary seed is required; extra seeds go to sample_history-like final_alts
        panel = next((p for p in session.get("panels") or [] if p.get("key") == panel_key), None)
        primary_job = None
        for i in range(n):
            seed = random.randint(0, (1 << 64) - 1)
            job_id = app.state.spooler.submit(
                JobLane.GENERATION,
                "weave_final",
                run_weave_image_generate,
                meta={
                    "session_id": session_id,
                    "kind": "final",
                    "panel_key": panel_key,
                    "seed_index": i,
                },
                db=app.state.db,
                comfy=app.state.comfy,
                session_id=session_id,
                kind="final",
                target=panel_key,
                workflow_name=workflow,
                positive=compiled["positive"],
                negative=compiled["negative"],
                seed=seed,
                steps=None,
                base_sha256=base_sha,
                ollama=llm,
            )
            entry = {
                "panel_key": panel_key,
                "job_id": job_id,
                "kind": "final",
                "seed": seed,
                "seed_index": i,
            }
            jobs.append(entry)
            if i == 0:
                primary_job = entry
        if panel is not None and primary_job is not None:
            panel["final"] = {
                "image_id": None,
                "job_id": primary_job["job_id"],
                "scorecard": None,
                "multi_seed": n,
            }
    session["status"] = "rendering"
    return jobs
