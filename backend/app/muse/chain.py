"""LLM turns for the Muse table read.

Each cast Muse speaks in character (SAY) and revises TAGS/SCENE. The showrunner
chats; the crew answers until they ask for a board or the showrunner says OK.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
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
    # The locked outfit, parsed off a wardrobe turn's trailing COSTUME block.
    # None for every seat that is not Wardrobe (and for duet prep).
    costume: dict[str, Any] | None = None
    # Per-speaker split of `say`, duet-only. `identity.parse_duet_speakers`
    # already resolved this — `service._apply_turn` maps "A"/"B" onto the two
    # cast character ids, it does not re-parse anything.
    turns: tuple[dict[str, str], ...] | None = None


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
    # Only Wardrobe carries a COSTUME tail; strip it before parse_table_read so
    # the SCENE capture (greedy to end-of-string) does not swallow it.
    costume: dict[str, Any] | None = None
    if crew.role_of(muse_id) == "wardrobe":
        raw, parsed = _strip_costume(raw)
        costume = parsed or None
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
        costume=costume,
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


# Wardrobe appends a COSTUME block after SCENE. The SCENE capture is greedy to
# end-of-string (identity._SAY_TAGS_SCENE_RE), so the block has to be split off
# BEFORE parse_table_read or it is swallowed into the prose. Same lenient-label
# spirit as parse_plan.
_COSTUME_LABELS: dict[str, str] = {
    label.replace(" ", ""): key for key, label in brief_mod.COSTUME_FIELDS
}
_COSTUME_LINE_RE = re.compile(
    r"(?im)^[\s>*_#-]*(" + "|".join(
        label.replace(" ", r"\s*") for _, label in brief_mod.COSTUME_FIELDS
    ) + r")[\s*_]*[:：]\s*(.+?)\s*$"
)
_COSTUME_HEAD_RE = re.compile(r"(?im)^[\s>*_#-]*COSTUME[\s*_]*[:：]?\s*$")


def _strip_costume(raw: str) -> tuple[str, dict[str, Any]]:
    """Split a trailing COSTUME block off a wardrobe turn.

    Returns (raw_without_costume, costume_dict). No `COSTUME:` header → the raw
    is returned unchanged and the dict is empty, so a wardrobe turn that forgot
    the block still parses as an ordinary turn.
    """
    text = raw or ""
    m = _COSTUME_HEAD_RE.search(text)
    if not m:
        return text, {}
    head, tail = text[:m.start()], text[m.end():]
    out: dict[str, Any] = {}
    for mm in _COSTUME_LINE_RE.finditer(tail):
        key = _COSTUME_LABELS.get(re.sub(r"\s+", "", mm.group(1)).upper())
        value = mm.group(2).strip().strip("*_").strip()
        if key and value and key not in out:
            out[key] = value
    return head.rstrip(), out


async def run_plan(
    ollama, *, user_prompt: str, model: str, num_ctx: int | None,
    muse_id: str = "plan", images: list[bytes] | None = None,
    seed: str = "",
    on_token: TokenCallback | None = None,
) -> dict[str, Any]:
    """Settle place, hour, light, action and the object ledger for this shoot."""
    mid = crew.resolve_member(muse_id) or crew.DEFAULT_MEMBER["plan"]
    raw, blind = await _call_seeing(
        ollama,
        system=crew.plan_system_prompt(mid, seed=seed),
        prompt=user_prompt, model=model, images=images,
        num_ctx=num_ctx, think=False, on_token=on_token,
    )
    plan = parse_plan(raw)
    if plan:
        plan["blind"] = blind
    return plan


STRIKE_SYSTEM = """
You are the script supervisor's clerk. You do not write craft and you do not
have opinions. One job: read what the Showrunner (総監督) just said, look at the
tags currently in the script, and report which of them the Showrunner no longer
wants — and which, if any, they are asking to bring back.

RULES
- Answer ONLY with tags copied EXACTLY from the CURRENT TAGS / CURRENTLY
  REMOVED lists you are given. Never invent, translate, pluralise or reword.
- Most notes remove nothing. A note that asks for something *different* is not
  a removal unless the old thing plainly cannot stay alongside the new one.
- Remove what the Showrunner named, and the tags that are plainly the same
  thing under another name. Nothing else. Do not tidy, do not simplify, do not
  remove things you personally think are wrong.
- If the Showrunner asks for something back that is on the CURRENTLY REMOVED
  list, put it under RESTORE.
- Empty lists are the normal answer and a complete answer.

OUTPUT FORMAT — exactly two lines, nothing else, no explanation:

REMOVE: <comma-separated tags from CURRENT TAGS, or the word none>
RESTORE: <comma-separated tags from CURRENTLY REMOVED, or the word none>
""".strip()

_STRIKE_LINE_RE = re.compile(r"(?im)^[\s>*_-]*(REMOVE|RESTORE)[\s*_]*[:：]\s*(.*)$")


def parse_strike(
    raw: str, present: Iterable[str], removed: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Read the clerk's two lines, keeping only tags that actually exist.

    The model picks from a closed list, and this is what closes it: anything it
    returns that is not already in the script (or not already removed) is
    dropped on the floor. That is the whole reason this is a separate turn
    rather than free-form extraction — a wrong answer can only ever be a
    smaller answer, never an invented noun.
    """
    here = {identity.bare_tag(t): identity.bare_tag(t) for t in present if t}
    gone = {identity.bare_tag(t): identity.bare_tag(t) for t in removed if t}
    out: dict[str, list[str]] = {"REMOVE": [], "RESTORE": []}
    for match in _STRIKE_LINE_RE.finditer(raw or ""):
        pool = here if match.group(1).upper() == "REMOVE" else gone
        for part in match.group(2).split(","):
            tag = identity.bare_tag(part)
            if tag and tag in pool and tag not in out[match.group(1).upper()]:
                out[match.group(1).upper()].append(pool[tag])
    return out["REMOVE"], out["RESTORE"]


async def run_strike(
    ollama, *, note: str, tags: Iterable[str], removed: Iterable[str] = (),
    model: str, num_ctx: int | None, on_token: TokenCallback | None = None,
) -> tuple[list[str], list[str]]:
    """What the Showrunner just took out of the picture, and what they want back.

    Runs on every note. Detecting "is this a removal?" with a pattern would miss
    the phrasings nobody thought of, and this cannot: a note that removes
    nothing simply comes back empty.
    """
    present = [t for t in tags if t]
    if not present and not list(removed):
        return [], []
    prompt = "\n\n".join([
        f"CURRENT TAGS:\n{', '.join(present)}",
        f"CURRENTLY REMOVED:\n{', '.join(removed) or '(none)'}",
        f"総監督がいま言ったこと:\n{note.strip()}",
    ])
    try:
        raw = await _call(
            ollama, system=STRIKE_SYSTEM, prompt=prompt, model=model,
            images=None, num_ctx=num_ctx, think=False, on_token=on_token,
        )
    except ChainError:
        # A clerk who cannot answer removes nothing. Guessing here would delete
        # the Showrunner's picture out from under them.
        logger.warning("[muse.chain] strike turn produced nothing", exc_info=True)
        return [], []
    return parse_strike(raw, present, removed)


async def run_duet_talk(
    ollama, *, user_prompt: str, model: str, num_ctx: int | None,
    character: dict[str, Any] | None = None,
    partner_character: dict[str, Any] | None = None, seed: str = "",
    images: list[bytes] | None = None,
    on_token: TokenCallback | None = None,
    tier: str = "",
) -> tuple[str, tuple[dict[str, str], ...] | None, bool]:
    """A two-hander (or W-Muse three-hander) conversation turn. Nothing is written down."""
    if partner_character:
        system = crew.w_actress_duet_prompt(
            character or {}, partner_character, mode="talk", seed=seed, tier=tier,
        )
    else:
        system = crew.actress_duet_prompt(character or {}, mode="talk", seed=seed)

    raw, blind = await _call_seeing(
        ollama,
        system=system,
        prompt=user_prompt, model=model, images=images,
        num_ctx=num_ctx, think=False, on_token=on_token,
    )
    say, _, _ = identity.parse_table_read(raw)
    text = (say or raw).strip()
    if text.lower().startswith("say:"):
        text = text[4:].strip()
    if not text:
        raise ChainError("empty duet turn")
    turns = identity.parse_duet_speakers(text) if partner_character else None
    turns_out = tuple(turns) if turns else None
    return text, turns_out, blind


async def run_duet_prep(
    ollama, *, user_prompt: str, model: str, num_ctx: int | None,
    identity_tags: list[str] | None, framing: str, brief: str,
    character: dict[str, Any] | None = None,
    partner_character: dict[str, Any] | None = None, style: str = "",
    cast: list[dict] | None = None, seed: str = "",
    images: list[bytes] | None = None,
    on_token: TokenCallback | None = None,
    tier: str = "",
) -> MuseTurn:
    """The turn where she (or they) build the whole shot and read the frame back."""
    if partner_character:
        system = crew.w_actress_duet_prompt(
            character or {}, partner_character, mode="prep", base_style=style, seed=seed,
            tier=tier,
        )
    else:
        system = crew.actress_duet_prompt(
            character or {}, mode="prep", base_style=style, seed=seed,
        )

    raw, blind = await _call_seeing(
        ollama,
        system=system,
        prompt=user_prompt, model=model, images=images,
        num_ctx=num_ctx, think=False, on_token=on_token,
    )
    turn = _finish_turn(
        raw, muse_id=crew.DEFAULT_MEMBER["actress"], identity_tags=identity_tags,
        framing=framing, brief=brief, style=style, cast=cast,
    )
    if partner_character:
        turns = identity.parse_duet_speakers(turn.say)
        turn = replace(turn, turns=tuple(turns) if turns else None)
    return turn if not blind else replace(turn, blind=True)


async def run_banter(
    ollama, *, muse_id: str, user_prompt: str, model: str,
    num_ctx: int | None,
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
        num_ctx=num_ctx, think=False, on_token=on_token,
    )
    say, _, _ = identity.parse_table_read(raw)
    text = (say or raw).strip()
    # Strip a leading SAY: if the model ignored the parser path.
    if text.lower().startswith("say:"):
        text = text[4:].strip()
    if not text:
        raise ChainError("empty banter")
    return text


# Being caught reading her diary used to be its own call, made while the panel
# waited on a read receipt. It is now a block on her next turn's user prompt
# (`crew.caught_block`) — she brings it up when they next meet, which is both
# how a person would find out and one fewer model load.
