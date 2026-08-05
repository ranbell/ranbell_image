"""The LLM stages. One system prompt per stage, one call each.

    A  pose      text only     brief                    -> draft prompt
    B  reinforce sees draft    brief + pose + WD14      -> theme repair
    C  cinematic sees B        brief + B's prompt       -> light / composition
    D  angle     sees C        brief + C's prompt       -> optional new camera

Each stage sees the picture the previous one made and the prompt that made it,
never the whole history. The chain is short because the useful information is in
the image, and the image is always the most recent thing.

Stage A's full prose is dropped at B rather than carried: a short pose intent
keeps the action, and WD14 reads the draft back at a low threshold. Those tags
plus the pose intent become B's base.

Answers are TAGS: / SCENE: blocks. ``identity.assemble_positive`` staples the
locked character tags onto whatever the model wrote before Comfy sees it.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import brief as brief_mod
from . import identity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageResult:
    """Assembled Comfy positive, plus the SCENE-side pose intent when useful."""
    prompt: str
    pose_intent: str = ""

PROMPTS = Path(__file__).parent / "prompts"

# The stages after the draft, in order. Fixed rather than open-ended: C and D say
# different things, and a fifth pass has nothing new to say. `refine_stages` cuts
# this short, it does not extend it.
REFINE_STAGES: tuple[tuple[str, str], ...] = (
    ("reinforce", "b_reinforce.md"),
    ("cinematic", "c_cinematic.md"),
    ("angle", "d_angle.md"),
)

STAGE_LABELS = {
    "pose": "A — what she is doing",
    "reinforce": "B — place objects and theme",
    "cinematic": "C — angle and light",
    "angle": "D — a new camera",
}

TokenCallback = Callable[[str], None]


def system_prompt(filename: str) -> str:
    return (PROMPTS / filename).read_text(encoding="utf-8").rstrip("\n")


def stages_for(count: int) -> tuple[tuple[str, str], ...]:
    return REFINE_STAGES[:max(1, min(int(count), len(REFINE_STAGES)))]


class ChainError(Exception):
    """A stage produced nothing usable."""


async def _call(
    ollama, *, system: str, prompt: str, model: str,
    images: list[bytes] | None, num_ctx: int | None,
    think: bool, on_token: TokenCallback | None = None,
) -> str:
    """One stage, always streamed.

    Streaming is what makes thinking usable at all — Ollama sends reasoning on a
    separate channel and leaves ``response`` empty until it is done — and it is
    also how the panel shows the prompt forming. ``on_token`` is optional; when
    set, each answer token is forwarded for SSE.
    """
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


def _finish(
    raw: str, *, identity_tags: list[str] | None, framing: str,
    brief: str,
) -> StageResult:
    tags, scene = identity.parse_hybrid(raw)
    positive = identity.assemble_positive(
        identity_tags, tags, scene, framing=framing,
    )
    identity.warn_reference_leak(brief, positive)
    if not positive.strip():
        raise ChainError("the model returned an empty prompt")
    # Pose intent comes from SCENE (or prose fallback), never from the stapled
    # identity tag prefix — that would poison stage B with hair/body tags.
    intent = identity.pose_summary(scene or raw)
    return StageResult(prompt=positive, pose_intent=intent)


async def run_pose(
    ollama, *, brief: str, model: str, num_ctx: int | None,
    think: bool = False, identity_tags: list[str] | None = None,
    framing: str = "auto", on_token: TokenCallback | None = None,
) -> StageResult:
    """Stage A. No image exists yet, so this is the only text-only call."""
    raw = await _call(
        ollama, system=system_prompt("a_pose.md"), prompt=brief,
        model=model, images=None, num_ctx=num_ctx, think=think,
        on_token=on_token,
    )
    return _finish(raw, identity_tags=identity_tags, framing=framing, brief=brief)


async def run_refine(
    ollama, *, stage_file: str, brief: str, previous: str,
    image: bytes, model: str, num_ctx: int | None,
    tags: str = "", pose: str = "", think: bool = False,
    identity_tags: list[str] | None = None, framing: str = "auto",
    on_token: TokenCallback | None = None,
) -> StageResult:
    """One refine stage. ``tags`` / ``pose`` are set for B only."""
    prompt = (brief_mod.with_tags(brief, tags, pose=pose) if tags or pose
              else brief_mod.with_prompt(brief, previous))
    raw = await _call(
        ollama, system=system_prompt(stage_file), prompt=prompt,
        model=model, images=[image], num_ctx=num_ctx, think=think,
        on_token=on_token,
    )
    return _finish(raw, identity_tags=identity_tags, framing=framing, brief=brief)
