"""LLM turns for the Muse table read.

Each cast Muse speaks in character (SAY) and revises TAGS/SCENE. The showrunner
chats; the crew answers until they ask for a board or the showrunner says OK.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import brief as brief_mod
from . import crew, identity

logger = logging.getLogger(__name__)

PROMPTS = Path(__file__).parent / "prompts"

TokenCallback = Callable[[str], None]


@dataclass(frozen=True)
class MuseTurn:
    muse_id: str
    say: str
    prompt: str
    pose_intent: str
    tags: str
    scene: str
    raw: str


class ChainError(Exception):
    """A turn produced nothing usable."""


def system_prompt(filename: str) -> str:
    return (PROMPTS / filename).read_text(encoding="utf-8").rstrip("\n")


async def _call(
    ollama, *, system: str, prompt: str, model: str,
    images: list[bytes] | None, num_ctx: int | None,
    think: bool, on_token: TokenCallback | None = None,
) -> str:
    options: dict[str, Any] = {"num_predict": -1}
    if num_ctx:
        options["num_ctx"] = int(num_ctx)
    kwargs = dict(model=model, options=options, system=system, think=think)

    stream = (ollama.generate_vlm_stream(prompt, images, **kwargs) if images
              else ollama.generate_text_stream(prompt, **kwargs))
    parts: list[str] = []
    async for event in stream:
        if event.get("type") == "token" and event.get("text"):
            parts.append(event["text"])
            if on_token is not None:
                try:
                    on_token(event["text"])
                except Exception:
                    logger.debug("[muse.chain] on_token failed", exc_info=True)

    text = "".join(parts).strip()
    if not text:
        raise ChainError("the model returned an empty prompt")
    return text


def _finish_turn(
    raw: str, *, muse_id: str, identity_tags: list[str] | None,
    framing: str, brief: str,
) -> MuseTurn:
    say, tags, scene = identity.parse_table_read(raw)
    positive = identity.assemble_positive(
        identity_tags, tags, scene, framing=framing,
    )
    identity.warn_reference_leak(brief, positive)
    if not positive.strip():
        raise ChainError("the model returned an empty prompt")
    intent = identity.pose_summary(scene or raw)
    return MuseTurn(
        muse_id=muse_id, say=say or "", prompt=positive,
        pose_intent=intent, tags=tags, scene=scene, raw=raw,
    )


async def run_muse(
    ollama, *, muse_id: str, user_prompt: str, model: str,
    num_ctx: int | None, identity_tags: list[str] | None,
    framing: str, brief: str, think: bool = False,
    images: list[bytes] | None = None,
    on_token: TokenCallback | None = None,
) -> MuseTurn:
    """One Muse at the table. Text by default; images optional for board review."""
    if muse_id not in crew.MUSES:
        raise ChainError(f"unknown muse: {muse_id}")
    raw = await _call(
        ollama, system=crew.system_prompt_for(muse_id), prompt=user_prompt,
        model=model, images=images, num_ctx=num_ctx, think=think,
        on_token=on_token,
    )
    return _finish_turn(
        raw, muse_id=muse_id, identity_tags=identity_tags,
        framing=framing, brief=brief,
    )


async def run_banter(
    ollama, *, muse_id: str, user_prompt: str, model: str,
    num_ctx: int | None, think: bool = False,
    on_token: TokenCallback | None = None,
) -> str:
    """Side comment only — returns SAY text, does not touch craft."""
    if muse_id not in crew.MUSES:
        raise ChainError(f"unknown muse: {muse_id}")
    raw = await _call(
        ollama, system=crew.banter_system_prompt_for(muse_id),
        prompt=user_prompt, model=model, images=None,
        num_ctx=num_ctx, think=think, on_token=on_token,
    )
    say, _, _ = identity.parse_table_read(raw)
    text = (say or raw).strip()
    # Strip a leading SAY: if the model ignored the parser path.
    if text.lower().startswith("say:"):
        text = text[4:].strip()
    if not text:
        raise ChainError("empty banter")
    return text


# ── legacy names used by older tests (thin wrappers) ───────────────────────
@dataclass(frozen=True)
class StageResult:
    prompt: str
    pose_intent: str = ""


REFINE_STAGES: tuple[tuple[str, str], ...] = (
    ("reinforce", "b_reinforce.md"),
    ("cinematic", "c_cinematic.md"),
    ("angle", "d_angle.md"),
)

STAGE_LABELS = {
    "pose": "Beat",
    "reinforce": "Patch",
    "cinematic": "Punch",
    "angle": "Orbit",
}


def stages_for(count: int) -> tuple[tuple[str, str], ...]:
    return REFINE_STAGES[:max(1, min(int(count), len(REFINE_STAGES)))]


async def run_pose(ollama, *, brief: str, model: str, num_ctx: int | None,
                   think: bool = False, identity_tags: list[str] | None = None,
                   framing: str = "auto", on_token: TokenCallback | None = None,
                   ) -> StageResult:
    turn = await run_muse(
        ollama, muse_id="beat", user_prompt=brief, model=model,
        num_ctx=num_ctx, identity_tags=identity_tags, framing=framing,
        brief=brief, think=think, on_token=on_token,
    )
    return StageResult(prompt=turn.prompt, pose_intent=turn.pose_intent)


async def run_refine(
    ollama, *, stage_file: str, brief: str, previous: str,
    image: bytes, model: str, num_ctx: int | None,
    tags: str = "", pose: str = "", think: bool = False,
    identity_tags: list[str] | None = None, framing: str = "auto",
    on_token: TokenCallback | None = None,
) -> StageResult:
    prompt = (brief_mod.with_tags(brief, tags, pose=pose) if tags or pose
              else brief_mod.with_prompt(brief, previous))
    raw = await _call(
        ollama, system=system_prompt(stage_file), prompt=prompt,
        model=model, images=[image], num_ctx=num_ctx, think=think,
        on_token=on_token,
    )
    say, tag_s, scene = identity.parse_table_read(raw)
    _ = say
    if not tag_s and not scene:
        tag_s, scene = identity.parse_hybrid(raw)
    positive = identity.assemble_positive(
        identity_tags, tag_s, scene, framing=framing,
    )
    if not positive.strip():
        raise ChainError("the model returned an empty prompt")
    return StageResult(
        prompt=positive,
        pose_intent=identity.pose_summary(scene or raw),
    )
