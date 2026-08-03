"""The LLM stages. One system prompt per stage, one call each.

    A  pose      text only     brief                    -> the draft's prompt
    B  reinforce sees draft    brief + WD14 tags        -> objects, theme repair
    C  cinematic sees B        brief + B's prompt       -> angle, ordering, light
    D  angle     sees C        brief + C's prompt       -> a decisively new camera

Each stage sees the picture the previous one made and the prompt that made it,
never the whole history. The chain is short because the useful information is in
the image, and the image is always the most recent thing.

Stage A's prose is dropped at B rather than carried: WD14 reads the draft back at
a low threshold and *those* tags become the base. A cheap render says what the
checkpoint actually drew, which is more use downstream than what it was asked for.

Nothing here parses the output. The stages return prose that goes into a prompt
box, so a stray sentence costs a slightly worse image, not a crash — which is why
the instructions carry an OUTPUT FORMAT block instead of this module carrying a
parser.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import brief as brief_mod

logger = logging.getLogger(__name__)

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
    "reinforce": "B — objects and theme",
    "cinematic": "C — angle and light",
    "angle": "D — a new camera",
}


def system_prompt(filename: str) -> str:
    return (PROMPTS / filename).read_text(encoding="utf-8").rstrip("\n")


def stages_for(count: int) -> tuple[tuple[str, str], ...]:
    return REFINE_STAGES[:max(1, min(int(count), len(REFINE_STAGES)))]


class ChainError(Exception):
    """A stage produced nothing usable."""


async def _call(ollama, *, system: str, prompt: str, model: str,
                images: list[bytes] | None, num_ctx: int | None) -> str:
    options: dict[str, Any] = {}
    if num_ctx:
        options["num_ctx"] = int(num_ctx)
    kwargs = dict(model=model, options=options, system=system,
                  # Explicit. Left unset, a reasoning model spends its whole
                  # budget thinking and returns an empty response — measured at
                  # 2048 tokens of reasoning and not one word of prompt.
                  think=False)
    if images:
        text = await ollama.generate_vlm(prompt, images, **kwargs)
    else:
        text = await ollama.generate_text(prompt, **kwargs)

    text = (text or "").strip()
    if not text:
        raise ChainError("the model returned an empty prompt")
    return text


async def run_pose(ollama, *, brief: str, model: str, num_ctx: int | None) -> str:
    """Stage A. No image exists yet, so this is the only text-only call."""
    return await _call(
        ollama, system=system_prompt("a_pose.md"), prompt=brief,
        model=model, images=None, num_ctx=num_ctx,
    )


async def run_refine(ollama, *, stage_file: str, brief: str, previous: str,
                     image: bytes, model: str, num_ctx: int | None,
                     tags: str = "") -> str:
    """One refine stage. ``tags`` is set for B only, where WD14 replaces the prose."""
    prompt = (brief_mod.with_tags(brief, tags) if tags
              else brief_mod.with_prompt(brief, previous))
    return await _call(
        ollama, system=system_prompt(stage_file), prompt=prompt,
        model=model, images=[image], num_ctx=num_ctx,
    )
