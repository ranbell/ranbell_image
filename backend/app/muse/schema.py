"""Muse session shape and step state.

The session keeps every prompt and every image the run produced, not just the
last one. That is deliberate: which stage came out best depends on how good the
draft was, so there is no "final" — there are results, and a person picks.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from .defaults import ALL_DEFAULTS

# Two steps and a choice between them. There is no AUTO mode: the choice of
# draft is a person looking at four pictures, and a pipeline that skips it is
# the pipeline Muse used to be.
STEPS: tuple[str, ...] = (
    "draft",    # one LLM call, then N variations from one seed
    "refine",   # per chosen draft: WD14 -> B -> C -> D, rendering after each
)


def new_session(inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "session_id": str(uuid.uuid4()),
        "created_at": time.time(),
        "updated_at": time.time(),
        "status": "draft",
        "inputs": {**ALL_DEFAULTS, **{
            "theme": "",
            "character_id": "",
            "workflow": "",
            "model": "",
            "llm_provider": "ollama",
            "locale": "ja",
        }, **(inputs or {})},
        "character": {},      # preset_to_character() output, frozen at pick time
        "brief": "",          # character sheet + theme, re-sent on every LLM call
        # {prompt, seed, job_id, images: [{index, image_id}], pending}
        "draft": {},
        "selected": [],       # draft indices the user sent onward
        # one per selected draft:
        # {draft_index, wd14, stages: [{stage, prompt, job_id, image_id, pending}]}
        "chains": [],
        "timeline": [],       # append-only log
        "warnings": [],
    }


def draft_images(session: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in ((session.get("draft") or {}).get("images") or [])
            if isinstance(i, dict)]


def all_stages(session: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for chain in (session.get("chains") or [])
            for s in (chain.get("stages") or []) if isinstance(s, dict)]


def step_state(session: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-step {done, pending, detail} for the panel. The server decides."""
    images = draft_images(session)
    landed = [i for i in images if i.get("image_id")]
    stages = all_stages(session)
    done_stages = [s for s in stages if s.get("image_id")]

    return {
        "draft": {
            "done": bool(images) and len(landed) == len(images),
            "pending": bool(session.get("draft")) and len(landed) < len(images or [1]),
            "detail": f"{len(landed)}/{len(images)}" if images else "",
        },
        "refine": {
            # Every stage of every chosen draft has to land. A half-filled grid
            # is a run in progress, not a finished one.
            "done": bool(stages) and len(done_stages) == len(stages),
            "pending": bool(stages) and len(done_stages) < len(stages),
            "detail": f"{len(done_stages)}/{len(stages)}" if stages else "",
        },
    }


def next_step(session: dict[str, Any]) -> str:
    state = step_state(session)
    for step in STEPS:
        if not state[step]["done"]:
            return step
    return "done"


def missing_inputs(session: dict[str, Any]) -> list[str]:
    """What the run cannot start without.

    Answered for the whole run rather than per step. Both steps need the same
    four things, and finding out at the refine stage that no model was chosen
    would mean having spent the draft renders to learn it.
    """
    inputs = session.get("inputs") or {}
    needs: list[str] = []
    if not str(inputs.get("theme") or "").strip():
        needs.append("theme")
    if not str(inputs.get("character_id") or "").strip():
        needs.append("character")
    if not str(inputs.get("workflow") or "").strip():
        needs.append("workflow")
    if not str(inputs.get("model") or "").strip():
        needs.append("model")
    return needs


def public_view(session: dict[str, Any]) -> dict[str, Any]:
    """What the panel gets: the session plus the state it should render."""
    return {
        **session,
        "steps": list(STEPS),
        "step_state": step_state(session),
        "next_step": next_step(session),
        "needs": missing_inputs(session),
    }
