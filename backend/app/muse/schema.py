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
    "compose",     # theme + character → ~30 danbooru tags per track, written by the model
    "board",       # cheap renders, 3 per track
    "harvest",     # read the boards back at a low threshold
    "topup",       # vocabulary the theme suggests and the picture lacks
    "merge",       # weighted background/character merge into one tag line
    "brainstorm",  # scene ideas from the merged tags
    "render",      # the real image
)

TRACKS: tuple[str, ...] = ("background", "person")

# How much of the run the user drives by hand.
#
# AUTO walks the whole chain from one press and draws every brainstorm idea;
# MANUAL stops after each step so the tags can be argued with. The tuning knobs
# only appear in MANUAL — in AUTO their defaults are the point.
MODE_AUTO = "auto"
MODE_MANUAL = "manual"
MODES: tuple[str, ...] = (MODE_AUTO, MODE_MANUAL)

# Where a tag came from. The panel colours chips by this.
TAG_SOURCES: tuple[str, ...] = ("compose", "vocab", "harvest", "topup", "character", "user")


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
            "mode": MODE_AUTO,
        }, **(inputs or {})},
        "character": {},          # preset_to_character() output, frozen at pick time
        "slots": {},              # slot key → [{tag, source}] — the prompt, aspect by aspect
        "seed_tags": {},          # track → [{tag, source}] — flattened, what the board renders
        "rejected_tags": [],      # user-clicked exclusions, applied from S2 onward
        "board": {t: [] for t in TRACKS},   # track → [{seed, image_id, job_id, pending}]
        "harvest": {},            # track → [{tag, score, count, category}]
        "harvest_dropped": {},    # track → [{tag, reason}] the LLM cleanup removed
        "topup": [],              # [{tag, why}] reinforcements chosen after the read-back
        "topup_candidates": [],   # what was offered, so the choice is inspectable
        "merged": {},             # {tags[], protected[], removed[], context, analysis}
        "scene": {},              # {candidates: [{title, body}], chosen, text}
        # One per brainstorm idea in AUTO, one in MANUAL. The whole point of
        # having four ideas is seeing all four drawn.
        "finals": [],             # [{idea_index, title, positive, job_id, image_id}]
        "timeline": [],           # append-only log; the seam a chat loop edits against
        "warnings": [],
    }


def finals_of(session: dict[str, Any]) -> list[dict[str, Any]]:
    """The finished renders, however many the run asked for.

    AUTO draws every brainstorm idea rather than making you choose one, so this
    is a list. Sessions written before that are a single dict under ``final``
    and read as a list of one.
    """
    finals = session.get("finals")
    if isinstance(finals, list):
        return [f for f in finals if isinstance(f, dict)]
    legacy = session.get("final")
    return [legacy] if isinstance(legacy, dict) and legacy else []


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
    finals = finals_of(session)
    seed_tags = session.get("seed_tags") or {}

    def _count(d: dict, key: str) -> int:
        return len(d.get(key) or [])

    return {
        "compose": {
            "done": any(seed_tags.get(t) for t in TRACKS),
            "detail": " / ".join(f"{t}: {_count(seed_tags, t)}" for t in TRACKS),
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
        "topup": {
            # Choosing nothing is a valid answer, so "done" follows the step
            # having run rather than having added something.
            "done": bool(session.get("topup_candidates")) or bool(session.get("topup")),
            "detail": f"+{len(session.get('topup') or [])}",
        },
        "merge": {
            "done": bool(merged.get("tags")),
            "detail": f"{len(merged.get('tags') or [])} tags",
        },
        "brainstorm": {
            # Choosing one idea is the step AUTO skips — it draws all of them,
            # writing each one's prose at render time — so having the ideas is
            # as far as this goes there. MANUAL still waits for a choice.
            "done": bool(scene.get("candidates") if is_auto(session)
                         else scene.get("text")),
            "detail": f"{len(scene.get('candidates') or [])} ideas",
        },
        "render": {
            # AUTO queues one render per brainstorm idea, so this is not done
            # until the last of them lands — a grid three-quarters full is a
            # run still in progress, not a finished one.
            "done": bool(finals) and all(f.get("image_id") for f in finals),
            "pending": any(f.get("job_id") and not f.get("image_id") for f in finals),
            "detail": f"{sum(1 for f in finals if f.get('image_id'))}/{len(finals)}"
                      if finals else "",
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
    """Which inputs this step needs and does not have.

    In AUTO the whole chain runs from one press, so the answer is every step's
    needs at once. Asking per step there would let someone start a run with no
    final workflow chosen and hit the wall six steps later, having spent the
    board renders getting there.
    """
    if is_auto(session):
        seen: list[str] = []
        for name in STEPS:
            for need in _needs_for(session, name):
                if need not in seen:
                    seen.append(need)
        return seen
    return _needs_for(session, step)


def _needs_for(session: dict[str, Any], step: str) -> list[str]:
    inputs = session.get("inputs") or {}
    needs: list[str] = []
    if not str(inputs.get("theme") or "").strip():
        needs.append("theme")
    if step in ("compose", "topup", "brainstorm") and not str(
        inputs.get("light_model") or ""
    ).strip():
        needs.append("lightModel")
    if step == "compose" and not str(inputs.get("character_id") or "").strip():
        needs.append("character")
    if step == "board":
        if not str(inputs.get("character_id") or "").strip():
            needs.append("character")
        if not str(inputs.get("board_workflow") or "").strip():
            needs.append("boardWorkflow")
    if step == "render" and not str(inputs.get("final_workflow") or "").strip():
        needs.append("finalWorkflow")
    return needs


def is_auto(session: dict[str, Any]) -> bool:
    return str((session.get("inputs") or {}).get("mode") or MODE_AUTO) == MODE_AUTO


def public_view(session: dict[str, Any]) -> dict[str, Any]:
    """What the panel gets: the session plus the state it should render."""
    return {
        **session,
        "steps": list(STEPS),
        "step_state": step_state(session),
        "next_step": next_step(session),
        "needs": missing_inputs(session, next_step(session)),
    }
