"""LLM turns for the Muse table read.

Each cast Muse speaks in character (SAY) and revises TAGS/SCENE. The showrunner
chats; the crew answers until they ask for a board or the showrunner says OK.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
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
    # True when the turn was offered the board and could not use it. A model
    # that cannot read images does not error — it returns nothing, or garbage —
    # so the retry is silent unless somebody surfaces this.
    blind: bool = False


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
    framing: str, brief: str, style: str = "",
    cast: list[dict] | None = None,
) -> MuseTurn:
    say, tags, scene = identity.parse_table_read(raw)
    positive = identity.assemble_positive(
        identity_tags, tags, scene, framing=framing, style=style,
        subject=identity.subject_tags(cast),
    )
    identity.warn_reference_leak(brief, positive)
    if not positive.strip():
        raise ChainError("the model returned an empty prompt")
    intent = identity.pose_summary(scene or raw)
    return MuseTurn(
        muse_id=muse_id, say=say or "", prompt=positive,
        pose_intent=intent, tags=tags, scene=scene, raw=raw,
    )


async def _call_seeing(
    ollama, *, system: str, prompt: str, model: str,
    images: list[bytes] | None, num_ctx: int | None, think: bool,
    on_token: TokenCallback | None = None,
) -> tuple[str, bool]:
    """Call with the board attached, falling back to text when it cannot read it.

    A model without vision does not refuse the image — it returns an empty
    response, which reads exactly like a bad turn. One retry without the picture
    keeps the table moving; the flag lets the caller say so out loud rather than
    quietly degrading for the rest of the session.
    """
    if not images:
        return await _call(
            ollama, system=system, prompt=prompt, model=model, images=None,
            num_ctx=num_ctx, think=think, on_token=on_token,
        ), False
    try:
        return await _call(
            ollama, system=system, prompt=prompt, model=model, images=images,
            num_ctx=num_ctx, think=think, on_token=on_token,
        ), False
    except ChainError:
        logger.warning(
            "[muse.chain] model %s returned nothing for an image turn — "
            "retrying blind", model,
        )
    return await _call(
        ollama, system=system, prompt=prompt, model=model, images=None,
        num_ctx=num_ctx, think=think, on_token=on_token,
    ), True


async def run_muse(
    ollama, *, muse_id: str, user_prompt: str, model: str,
    num_ctx: int | None, identity_tags: list[str] | None,
    framing: str, brief: str, think: bool = False,
    images: list[bytes] | None = None,
    character: dict[str, Any] | None = None,
    style: str = "", cast: list[dict] | None = None,
    seed: str = "",
    on_token: TokenCallback | None = None,
) -> MuseTurn:
    """One Muse at the table. Text by default; images once a board exists."""
    # Callers may name a job ("beat") or a person ("beat:ichibyou"). A job
    # resolves to whoever does it by default.
    muse_id = crew.resolve_member(muse_id)
    if not muse_id:
        raise ChainError(f"unknown muse: {muse_id}")
    raw, blind = await _call_seeing(
        ollama,
        system=crew.system_prompt_for(
            muse_id, character=character, base_style=style, seed=seed,
        ),
        prompt=user_prompt,
        model=model, images=images, num_ctx=num_ctx, think=think,
        on_token=on_token,
    )
    turn = _finish_turn(
        raw, muse_id=muse_id, identity_tags=identity_tags,
        framing=framing, brief=brief, style=style, cast=cast,
    )
    return turn if not blind else replace(turn, blind=True)


# The planner answers in labelled lines rather than TAGS/SCENE, so it gets its
# own parser. Deliberately lenient about the label spelling — a run that loses
# the plan because the model wrote "MUST APPEAR :" is a run that loses its place.
_PLAN_LABELS: dict[str, str] = {
    label.replace(" ", ""): key for key, label in brief_mod.PLAN_FIELDS
}
_PLAN_LINE_RE = re.compile(
    r"(?im)^[\s>*_#-]*(" + "|".join(
        label.replace(" ", r"\s*") for _, label in brief_mod.PLAN_FIELDS
    ) + r")[\s*_]*[:：]\s*(.+?)\s*$"
)
_LIST_FIELDS = {"must_appear"}


def parse_plan(raw: str) -> dict[str, Any]:
    """Return {say, place, hour, light, action, must_appear} from a planner turn.

    Returns {} when no labelled line came back at all — the caller keeps the
    plan it already had rather than replacing a good one with nothing.
    """
    text = (raw or "").strip()
    if not text:
        return {}
    matches = list(_PLAN_LINE_RE.finditer(text))
    if not matches:
        return {}

    out: dict[str, Any] = {}
    for m in matches:
        key = _PLAN_LABELS.get(re.sub(r"\s+", "", m.group(1)).upper())
        value = m.group(2).strip().strip("*_").strip()
        if not key or not value or key in out:
            continue
        if key in _LIST_FIELDS:
            # The sentence-ending period on the last item rides into the tag
            # otherwise, and `dim_ceiling_spotlight.` matches nothing.
            items = [v.strip().strip("*_").rstrip(".、,") for v in value.split(",")]
            out[key] = [v for v in items if v]
        else:
            out[key] = value

    say = text[:matches[0].start()].strip()
    say = re.sub(r"(?is)^\s*SAY\s*[:：]\s*", "", say).strip()
    if say:
        out["say"] = say
    return out


async def run_plan(
    ollama, *, user_prompt: str, model: str, num_ctx: int | None,
    muse_id: str = "plan", images: list[bytes] | None = None,
    think: bool = False, seed: str = "",
    on_token: TokenCallback | None = None,
) -> dict[str, Any]:
    """Settle place, hour, light, action and the object ledger for this shoot."""
    mid = crew.resolve_member(muse_id) or crew.DEFAULT_MEMBER["plan"]
    raw, blind = await _call_seeing(
        ollama,
        system=crew.plan_system_prompt(mid, seed=seed),
        prompt=user_prompt, model=model, images=images,
        num_ctx=num_ctx, think=think, on_token=on_token,
    )
    plan = parse_plan(raw)
    if plan:
        plan["blind"] = blind
    return plan


async def run_duet_talk(
    ollama, *, user_prompt: str, model: str, num_ctx: int | None,
    character: dict[str, Any] | None = None, seed: str = "",
    images: list[bytes] | None = None,
    on_token: TokenCallback | None = None,
) -> tuple[str, bool]:
    """A two-hander conversation turn. Nothing is written down.

    Returns her line and whether she was handed a picture she could not read.
    The craft is deliberately untouched: in 二人芝居 the script does not exist
    until the Showrunner asks to get ready, so that talking stays cheap and
    fast enough to feel like talking.
    """
    raw, blind = await _call_seeing(
        ollama,
        system=crew.actress_duet_prompt(character or {}, mode="talk", seed=seed),
        prompt=user_prompt, model=model, images=images,
        num_ctx=num_ctx, think=False, on_token=on_token,
    )
    say, _, _ = identity.parse_table_read(raw)
    text = (say or raw).strip()
    if text.lower().startswith("say:"):
        text = text[4:].strip()
    if not text:
        raise ChainError("empty duet turn")
    return text, blind


async def run_duet_prep(
    ollama, *, user_prompt: str, model: str, num_ctx: int | None,
    identity_tags: list[str] | None, framing: str, brief: str,
    character: dict[str, Any] | None = None, style: str = "",
    cast: list[dict] | None = None, seed: str = "",
    images: list[bytes] | None = None,
    on_token: TokenCallback | None = None,
) -> MuseTurn:
    """The turn where she builds the whole shot and reads the frame back."""
    raw, blind = await _call_seeing(
        ollama,
        system=crew.actress_duet_prompt(
            character or {}, mode="prep", base_style=style, seed=seed,
        ),
        prompt=user_prompt, model=model, images=images,
        num_ctx=num_ctx, think=False, on_token=on_token,
    )
    turn = _finish_turn(
        raw, muse_id=crew.DEFAULT_MEMBER["actress"], identity_tags=identity_tags,
        framing=framing, brief=brief, style=style, cast=cast,
    )
    return turn if not blind else replace(turn, blind=True)


async def run_banter(
    ollama, *, muse_id: str, user_prompt: str, model: str,
    num_ctx: int | None, think: bool = False,
    character: dict[str, Any] | None = None,
    on_token: TokenCallback | None = None,
) -> str:
    """Side comment only — returns SAY text, does not touch craft."""
    muse_id = crew.resolve_member(muse_id)
    if not muse_id:
        raise ChainError(f"unknown muse: {muse_id}")
    raw = await _call(
        ollama,
        system=crew.banter_system_prompt_for(muse_id, character=character),
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
