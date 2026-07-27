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

    workflow = _workflow(session, sample=True)
    if not workflow:
        raise ValueError("workflow_final or workflow_sample is required for board render")

    briefs = (session.get("character") or {}).get("board_briefs") or [
        {"slot": "portrait"}, {"slot": "full"}, {"slot": "prop"},
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


def submit_sample_job(
    app,
    session_id: str,
    session: dict[str, Any],
    panel_key: str,
) -> dict[str, Any]:
    from ...jobs.runners import run_weave_image_generate

    workflow = _workflow(session, sample=True)
    if not workflow:
        raise ValueError("workflow_final or workflow_sample is required for sample render")

    compiled = compile_panel_render(session, panel_key)
    seed = random.randint(0, (1 << 64) - 1)
    inputs = session.get("inputs") or {}
    base_sha = str(inputs.get("reference_image_id") or "")
    steps_raw = inputs.get("sample_steps")
    try:
        steps = int(steps_raw) if steps_raw is not None else 20
    except (TypeError, ValueError):
        steps = 20
    steps = max(8, min(40, steps))
    llm = _bound_llm(app, session)
    job_id = app.state.spooler.submit(
        JobLane.GENERATION,
        "weave_sample",
        run_weave_image_generate,
        meta={
            "session_id": session_id,
            "kind": "sample",
            "panel_key": panel_key,
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
    panel = next((p for p in session.get("panels") or [] if p.get("key") == panel_key), None)
    if panel is not None:
        panel["sample"] = {
            "image_id": None,
            "job_id": job_id,
            "scorecard": None,
        }
        if panel.get("compile"):
            panel["compile"]["positive"] = compiled["positive"]
            panel["compile"]["negative"] = compiled["negative"]
    return {"panel_key": panel_key, "job_id": job_id, "kind": "sample"}


def submit_final_jobs(app, session_id: str, session: dict[str, Any]) -> list[dict[str, Any]]:
    from ...jobs.runners import run_weave_image_generate

    workflow = _workflow(session, sample=False)
    if not workflow:
        raise ValueError("workflow_final is required for final render")

    jobs: list[dict[str, Any]] = []
    base_sha = str((session.get("inputs") or {}).get("reference_image_id") or "")
    llm = _bound_llm(app, session)
    for panel_key in ("panel_1", "panel_2", "panel_3"):
        compiled = compile_panel_render(session, panel_key)
        seed = random.randint(0, (1 << 64) - 1)
        job_id = app.state.spooler.submit(
            JobLane.GENERATION,
            "weave_final",
            run_weave_image_generate,
            meta={
                "session_id": session_id,
                "kind": "final",
                "panel_key": panel_key,
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
        panel = next((p for p in session.get("panels") or [] if p.get("key") == panel_key), None)
        if panel is not None:
            panel["final"] = {"image_id": None, "job_id": job_id, "scorecard": None}
        jobs.append({"panel_key": panel_key, "job_id": job_id, "kind": "final"})
    session["status"] = "rendering"
    return jobs
