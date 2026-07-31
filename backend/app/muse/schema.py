"""Muse session shape and step state.

The session is the whole record of a run: every intermediate tag set is kept,
not just the final prompt. That is deliberate — the interesting part of this
pipeline is *where a tag came from*, and the panel colour-codes provenance. It
is also what a later chat loop would edit against.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from .defaults import ALL_DEFAULTS

# The linear pipeline. Each step consumes the one before it, so the panel can
# render progress generically from `step_state` without knowing any of this.
STEPS: tuple[str, ...] = (
    "split",       # theme → character / background / props / action / mood / camera
    "tags",        # each side → WD14 vocabulary, plus the surprise layer
    "board",       # cheap renders, 3 per track
    "harvest",     # read the boards back at a low threshold
    "merge",       # weighted background/character merge into one tag line
    "brainstorm",  # scene ideas from the merged tags
    "render",      # the real image
)

TRACKS: tuple[str, ...] = ("background", "person")

SPLIT_SECTIONS: tuple[str, ...] = (
    "character", "background", "props", "action", "mood", "camera",
)

# Where a candidate tag came from. The panel colours chips by this, and it is
# the only honest way to tell "the theme asked for this" from "the vocabulary
# search wandered over here".
TAG_SOURCES: tuple[str, ...] = ("topic", "lunatic", "stranger", "frontier", "split")


def new_session(inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "session_id": str(uuid.uuid4()),
        "created_at": time.time(),
        "updated_at": time.time(),
        "status": "draft",
        "inputs": {**ALL_DEFAULTS, **{
            "theme": "",
            "character_id": "",
            "board_workflow": "",
            "final_workflow": "",
            "light_model": "",
            "llm_provider": "ollama",
            "locale": "ja",
            "negative_prompt": "",
        }, **(inputs or {})},
        "character": {},          # preset_to_character() output, frozen at pick time
        "split": {},              # SPLIT_SECTIONS → comma-separated tag string
        "seed_tags": {},          # track → [{tag, source}]
        "rejected_tags": [],      # user-clicked exclusions, applied from S2 onward
        "board": {t: [] for t in TRACKS},   # track → [{seed, image_id, job_id, pending}]
        "harvest": {},            # track → [{tag, score, count, category}]
        "merged": {},             # {tags[], protected[], removed[], context, analysis}
        "scene": {},              # {candidates: [{title, body}], chosen, text}
        "final": {},              # {positive, negative, image_id, job_id}
        "timeline": [],           # append-only log; the seam a chat loop edits against
        "warnings": [],
    }


def step_state(session: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per-step {done, detail} for the panel. The server decides, not the client."""
    board = session.get("board") or {}
    board_images = [
        img for track in TRACKS for img in (board.get(track) or [])
    ]
    landed = [i for i in board_images if i.get("image_id")]
    harvest = session.get("harvest") or {}
    merged = session.get("merged") or {}
    scene = session.get("scene") or {}
    final = session.get("final") or {}
    seed_tags = session.get("seed_tags") or {}

    def _count(d: dict, key: str) -> int:
        return len(d.get(key) or [])

    return {
        "split": {
            "done": bool(session.get("split")),
            "detail": ", ".join(
                k for k in SPLIT_SECTIONS if (session.get("split") or {}).get(k)
            ),
        },
        "tags": {
            "done": any(seed_tags.get(t) for t in TRACKS),
            "detail": " / ".join(
                f"{t}: {_count(seed_tags, t)}" for t in TRACKS
            ),
        },
        "board": {
            "done": bool(board_images) and len(landed) == len(board_images),
            "pending": bool(board_images) and len(landed) < len(board_images),
            "detail": f"{len(landed)}/{len(board_images)}",
        },
        "harvest": {
            "done": any(harvest.get(t) for t in TRACKS),
            "detail": " / ".join(f"{t}: {_count(harvest, t)}" for t in TRACKS),
        },
        "merge": {
            "done": bool(merged.get("tags")),
            "detail": f"{len(merged.get('tags') or [])} tags",
        },
        "brainstorm": {
            "done": bool(scene.get("text")),
            "detail": f"{len(scene.get('candidates') or [])} ideas",
        },
        "render": {
            "done": bool(final.get("image_id")),
            "pending": bool(final.get("job_id")) and not final.get("image_id"),
            "detail": final.get("image_id", "")[:8],
        },
    }


def next_step(session: dict[str, Any]) -> str:
    """The first step not yet done — what the single CTA button should run."""
    state = step_state(session)
    for step in STEPS:
        if not state[step]["done"]:
            return step
    return "done"


def missing_inputs(session: dict[str, Any], step: str) -> list[str]:
    """Which inputs this step needs and does not have."""
    inputs = session.get("inputs") or {}
    needs: list[str] = []
    if step in ("split", "tags", "board", "harvest", "merge", "brainstorm", "render"):
        if not str(inputs.get("theme") or "").strip():
            needs.append("theme")
    if step in ("split", "brainstorm") and not str(inputs.get("light_model") or "").strip():
        needs.append("lightModel")
    if step == "board":
        if not str(inputs.get("character_id") or "").strip():
            needs.append("character")
        if not str(inputs.get("board_workflow") or "").strip():
            needs.append("boardWorkflow")
    if step == "render" and not str(inputs.get("final_workflow") or "").strip():
        needs.append("finalWorkflow")
    return needs


def public_view(session: dict[str, Any]) -> dict[str, Any]:
    """What the panel gets: the session plus the state it should render."""
    return {
        **session,
        "steps": list(STEPS),
        "step_state": step_state(session),
        "next_step": next_step(session),
        "needs": missing_inputs(session, next_step(session)),
    }
