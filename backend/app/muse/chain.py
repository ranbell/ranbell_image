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
from . import facets as facets_mod

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
    # Family sampling (Gemma → temp 1.0 / top_k 64 / top_p 0.95). Do not
    # hardcode temperature — model-card defaults live in llm_options.
    from ..ai.llm_options import llm_options
    options = llm_options({"num_predict": -1}, model=model, num_ctx=num_ctx)
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
    cast: list[dict] | None = None, duet: bool = False,
) -> MuseTurn:
    # Wardrobe carries a COSTUME tail in the crewed studio; in a duet she is
    # the only one who ever writes a shot, so she carries it on prep turns
    # instead. Strip it before parse_table_read so the SCENE capture (greedy
    # to end-of-string) does not swallow it.
    costume: dict[str, Any] | None = None
    if crew.role_of(muse_id) == "wardrobe" or duet:
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
    say = identity.sanitize_muse_say(
        re.sub(r"(?is)^\s*SAY\s*[:：]\s*", "", say).strip()
    )
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


def route_system(*, name_a: str = "", name_b: str = "") -> str:
    """The router's system prompt — eight parts alone, eleven with a partner.

    `name_b` truthy is what means "W-Muse": the three character-bound parts
    (`costume`/`pose`/`expression`) exist twice, once per Muse, and nothing
    else does — `camera` stays the one shared lens on both of them, which is
    also where an interaction (`looking_at_each_other`, `holding_hands`) goes,
    since it belongs to neither Muse alone.
    """
    partner = bool(name_b)
    a = name_a or "she"
    parts = [
        "place       — the room, and where in it they are",
        "hour        — time of day and season",
        "light       — where the light comes from and how bright it is",
        "props       — the objects in the frame",
        f"costume     — what {a} is wearing",
        f"pose        — what {a}'s body is doing",
        f"expression  — {a}'s face",
        "camera      — how far away, what angle, where the lens is pointed, "
        "and how the two of them relate to each other in frame (side by "
        "side, facing, touching)" if partner else
        "camera      — how far away, what angle, and where she is looking",
    ] if partner else [
        "place       — the room, and where in it she is",
        "hour        — time of day and season",
        "light       — where the light comes from and how bright it is",
        "props       — the objects in the frame",
        "costume     — what she is wearing",
        "pose        — what her body is doing",
        "expression  — her face",
        "camera      — how far away, what angle, and where she is looking",
    ]
    if partner:
        parts += [
            f"costume_b    — what {name_b} is wearing",
            f"pose_b       — what {name_b}'s body is doing",
            f"expression_b — {name_b}'s face",
        ]
    n_parts = "eleven" if partner else "eight"
    output_lines = [
        "PLACE: <what the place is now>", "HOUR: <…>", "LIGHT: <…>",
        "PROPS: <…>", "COSTUME: <…>", "POSE: <…>", "EXPRESSION: <…>",
        "CAMERA: <…>",
    ]
    if partner:
        output_lines += [
            "COSTUME_B: <…>", "POSE_B: <…>", "EXPRESSION_B: <…>",
        ]
    return "\n".join([
        "You are the script supervisor. You do not write craft and you do not "
        f"have opinions. The shot is kept in {n_parts} parts. One job: read "
        "what the Showrunner (総監督) just said and name ONLY the parts it "
        "changes.",
        "",
        f"THE {n_parts.upper()} PARTS",
        *parts,
        "",
        "RULES",
        "- Name a part ONLY when the note changes it. Most notes touch one or "
        "two. Naming a part you are unsure about rewrites it — say less, not "
        "more.",
        "- Copy part names EXACTLY from the list above. Never invent one, "
        "never translate one.",
        (
            f"- A note that names {name_a} says nothing about {name_b} and "
            f"the reverse — 「{name_a}の服を脱がせて」names `costume` only, "
            "never `costume_b` too, even when they are dressed alike."
            if partner else ""
        ),
        "- A note that changes nothing about the picture answers `FACETS: "
        "none`. That is a normal and complete answer.",
        "- For each part you named, write ONE short line, in the "
        "Showrunner's own language, saying what that part IS now. State the "
        "finished value, never the change: 「下から煽って」, not "
        "「カメラを下げて」.",
        "- A rule that is about the whole session and belongs to no single "
        "part (「足は絶対に映さない」) goes on the STANDING line instead, "
        "and its part is not named.",
        "",
        "THE DECISION DIGEST",
        f"Besides the {n_parts} parts, you keep one more thing: a short, "
        "plain-language record of what has actually been decided so far — "
        "added, then dropped, then maybe brought back. This is not a "
        "transcript and not a tag list. Every future turn reads THIS instead "
        "of the raw conversation, so it has to stay short, current, and free "
        "of anything that no longer matters.",
        "",
        "You are shown the digest as it stands below. Revise it for this "
        "note:",
        "- Settling something new: add ONE short line for it.",
        "- Reversing or replacing something already in the digest: REWRITE "
        "that line. Never keep both the old and the new statement of the "
        "same thing.",
        "- Something that can no longer affect the picture: drop its line "
        "entirely.",
        "- Most notes change nothing here. When in doubt, leave it exactly "
        "as it was.",
        "- A handful of lines, never a growing log.",
        "",
        "OUTPUT FORMAT — the FACETS line, then one line per part you named, "
        "then STANDING, then DIGEST last. Nothing else, no explanation:",
        "",
        "FACETS: <comma-separated part names, or the word none>",
        *output_lines,
        "STANDING: <one rule for the whole session, or the word none>",
        "DIGEST: <the whole revised digest, as plain lines, or the word "
        "unchanged>",
    ]).strip()


# Kept as the solo system prompt, unchanged — anything reading `ROUTE_SYSTEM`
# directly (tests included) still sees exactly today's eight-part text.
ROUTE_SYSTEM = route_system()

# Same lenient-label spirit as `parse_plan` — a run that loses the routing
# because the model wrote "CAMERA :" is a run that rewrites the whole shot.
#
# Built from `ALL_FACETS` (all eleven, partner ones included) unconditionally
# rather than switched on partner presence: parsing a `COSTUME_B` line a solo
# session's model never had a reason to write is harmless (nothing downstream
# treats a name outside that turn's own `allowed`/`want` set as real — see
# `parse_facets` and `service.route_note`'s `writable` filter), and it means
# this file has one label map instead of building it fresh per call.
_ROUTE_LABELS: dict[str, str] = {
    label.replace(" ", ""): key for key, label in facets_mod.ALL_FACETS
}
_ROUTE_LINE_RE = re.compile(
    r"(?im)^[\s>*_#-]*(FACETS|STANDING|" + "|".join(
        label for _, label in facets_mod.ALL_FACETS
    ) + r")[\s*_]*[:：]\s*(.*?)\s*$"
)
_NONE_WORDS = frozenset({"none", "なし", "無し", "-", "--", "n/a", "(none)"})
_UNCHANGED_WORDS = frozenset({"unchanged", "変更なし", "同じ", "そのまま"})

# DIGEST is free natural-language prose and can run to several lines, so it
# cannot be read by `_ROUTE_LINE_RE`'s one-line-per-label scan (`$` stops at
# the first newline in MULTILINE mode) — it is captured separately, greedy to
# the end of the text, the same "last field owns the rest of the string"
# pattern `_strip_costume` uses for the trailing COSTUME block.
_DIGEST_RE = re.compile(r"(?is)DIGEST\s*[:：]\s*(.+)$")


def parse_route(raw: str) -> tuple[list[str], dict[str, str], str, str]:
    """Read the clerk's routing. Returns (facets, directive per facet, standing, digest).

    The facet names are a closed list and this is what closes it: a name the
    model invented is dropped on the floor, exactly as `parse_strike` drops a
    tag that is not in the script. A wrong answer can only ever be a smaller
    answer, and a smaller answer rewrites less of the shot.

    `digest` is "" when the model left it unchanged, said nothing usable, or
    the field is missing — the caller keeps its own previous digest in every
    such case rather than replacing a good summary with an empty one.
    """
    text = raw or ""
    digest = ""
    dm = _DIGEST_RE.search(text)
    if dm:
        # Everything from DIGEST: to the end is the digest, not routing — cut
        # it off before the per-line scan so a colon inside the digest's own
        # prose ("衣装:まだ検討中" as a sentence) cannot be mistaken for a label.
        value = dm.group(1).strip()
        text = text[:dm.start()]
        if value and value.lower() not in _UNCHANGED_WORDS | _NONE_WORDS:
            digest = value

    named: list[str] = []
    lines: dict[str, str] = {}
    standing = ""
    for match in _ROUTE_LINE_RE.finditer(text):
        label = re.sub(r"\s+", "", match.group(1)).upper()
        value = match.group(2).strip().strip("*_").strip()
        if label == "FACETS":
            for part in value.split(","):
                key = _ROUTE_LABELS.get(re.sub(r"\s+", "", part).upper())
                if key and key not in named:
                    named.append(key)
            continue
        if label == "STANDING":
            if value and value.lower() not in _NONE_WORDS:
                standing = value
            continue
        key = _ROUTE_LABELS.get(label)
        if key and value and value.lower() not in _NONE_WORDS and key not in lines:
            lines[key] = value
    # A directive for a part the clerk did not name is not acted on: the FACETS
    # line is the decision, and the rest is its detail.
    return (
        named, {k: v for k, v in lines.items() if k in named}, standing, digest,
    )


async def run_route(
    ollama, *, note: str, table_block: str, current_digest: str = "",
    model: str, num_ctx: int | None,
    name_a: str = "", name_b: str = "",
    on_token: TokenCallback | None = None,
) -> tuple[list[str], dict[str, str], str, str]:
    """Which parts of the shot did the Showrunner just change?

    Runs on every note, like the strike turn, and for the same reason: deciding
    "is this a camera note?" with a pattern would miss every phrasing nobody
    thought of. A note that changes nothing simply comes back empty.

    Also carries the decision digest forward — shown as it stands, handed back
    revised (or unchanged). This is the same call, not an extra one: the model
    is already reading the note and the table to decide routing, and updating
    a short running summary is the same judgment call, not a second one.

    `name_b` present is what switches the system prompt to the eleven-part
    (W-Muse) form — see `route_system`.
    """
    prompt = "\n\n".join([
        f"THE SHOT AS IT STANDS:\n{table_block}" if table_block.strip() else "",
        f"THE DECISION DIGEST AS IT STANDS:\n{current_digest}"
        if current_digest.strip() else "THE DECISION DIGEST AS IT STANDS: (empty so far)",
        f"総監督がいま言ったこと:\n{note.strip()}",
    ]).strip()
    system = route_system(name_a=name_a, name_b=name_b) if name_b else ROUTE_SYSTEM
    try:
        raw = await _call(
            ollama, system=system, prompt=prompt, model=model,
            images=None, num_ctx=num_ctx, think=False, on_token=on_token,
        )
    except ChainError:
        # A clerk who cannot answer changes nothing. Guessing which part to
        # rewrite would throw away a part of the picture the Showrunner never
        # asked about.
        logger.warning("[muse.chain] route turn produced nothing", exc_info=True)
        return [], {}, "", ""
    return parse_route(raw)


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


# Two lines per part: `CAMERA TAGS: …` and `CAMERA: …`. The TAGS variant has to
# be tried first or the bare label matches it and swallows the word "TAGS".
# `ALL_FACETS`, same reasoning as `_ROUTE_LABELS` above — a solo turn's
# `allowed` set never contains a `_b` name, so it cannot parse one out.
_FACET_LINE_RE = re.compile(
    r"(?im)^[\s>*_#-]*(" + "|".join(
        label for _, label in facets_mod.ALL_FACETS
    ) + r")[\s*_]*(TAGS)?[\s*_]*[:：]\s*(.*?)\s*$"
)


def parse_facets(
    raw: str, allowed: Iterable[str],
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Read a facet-writing turn. Returns (say, {facet: {tags, nl, fields}}).

    A part the turn was not asked to write is dropped rather than applied — the
    scope line in the contract says so, and this is what makes it true. A part
    with tags but no prose keeps the prose it had; a part with neither is not
    written at all, the same "do not replace a good value with nothing" rule
    `parse_plan` follows.
    """
    text = raw or ""
    # The COSTUME block is a tail of eight labelled lines and the SCENE capture
    # is greedy to end-of-string, so it comes off first — same reason as ever.
    text, costume_fields = _strip_costume(text)

    want = {a for a in allowed}
    out: dict[str, dict[str, Any]] = {}
    first_at = len(text)
    for match in _FACET_LINE_RE.finditer(text):
        key = _ROUTE_LABELS.get(re.sub(r"\s+", "", match.group(1)).upper())
        if not key or key not in want:
            continue
        value = match.group(3).strip().strip("*_").strip()
        if not value:
            continue
        first_at = min(first_at, match.start())
        slot = out.setdefault(key, {})
        field = "tags" if match.group(2) else "nl"
        slot.setdefault(field, value)

    if costume_fields and "costume" in want:
        slot = out.setdefault("costume", {})
        fields, garments = facets_mod.from_costume_block(costume_fields)
        slot["fields"] = fields
        # GARMENTS is the outfit as tags and the only place it exists as tags.
        # It wins over a COSTUME TAGS line, so there is one garment authority.
        if garments:
            slot["tags"] = ", ".join(garments)
        first_at = min(first_at, len(text))

    say = text[:first_at].strip() if out else text.strip()
    say = identity.sanitize_muse_say(
        re.sub(r"(?is)^\s*SAY\s*[:：]\s*", "", say).strip()
    )
    return say, out


async def run_duet_facets(
    ollama, *, user_prompt: str, system: str, allowed: list[str],
    model: str, num_ctx: int | None,
    images: list[bytes] | None = None,
    on_token: TokenCallback | None = None,
) -> tuple[str, dict[str, dict[str, Any]], bool]:
    """One turn that rewrites some parts of the shot, and says so out loud."""
    raw, blind = await _call_seeing(
        ollama, system=system, prompt=user_prompt, model=model, images=images,
        num_ctx=num_ctx, think=False, on_token=on_token,
    )
    say, written = parse_facets(raw, allowed)
    if not written:
        raise ChainError("the turn wrote no part of the shot")
    return say, written, blind


def compose_system(*, name_a: str = "", name_b: str = "") -> str:
    """The composer's system prompt — solo alone, two-Muse-aware with a partner.

    `name_b` truthy is what switches this to the W-Muse form. The instruction
    to cover every part unchanged; what is added is explicit enough that the
    model cannot solve "two people" by quietly writing about one of them
    (2026-08-11's real-session report: one Muse dominant, the other barely
    present) — name both, give both real weight, and say what passes between
    them rather than describing two people who happen to share a frame.
    """
    partner = bool(name_b)
    parts = [
        "You are the script supervisor writing the shot up for the camera "
        "department. You have the shot in front of you, in parts. Turn it "
        "into one paragraph.",
        "",
        "You have no memory of any conversation and there is nothing else to "
        "read. Everything the picture contains is in the parts below.",
        "",
        "- ONE flowing paragraph, "
        + ("180–260" if partner else "140–200")
        + " English words. No headings, no bullets, no preamble, no "
          "alternatives, no lists.",
        "- Every part must be in it: the place, the hour, the light, the "
        "objects, "
        + (f"{name_a}'s clothes/body/face, {name_b}'s clothes/body/face, "
           if partner else "the clothes, the body, the face, ")
        + "the camera.",
    ]
    if partner:
        parts += [
            f"- BOTH {name_a} and {name_b} are in the picture. Name them "
            "both. Give each of them a genuinely comparable share of the "
            "paragraph — not one full sentence for one of them and a "
            "trailing clause for the other.",
            "- If the parts describe an interaction between them (facing "
            "each other, side by side, a touch, a held object passed "
            "between them), write it as something that happens between "
            "two people, not as two separate descriptions that happen to "
            "sit next to each other.",
        ]
    parts += [
        "- Write ONLY what the parts say. You may make a sentence out of a "
        "tag; you may NOT add an object, a garment, a room, a colour or a "
        "person that is not written above. If a part is thin, write it "
        "thin.",
        "- State absolutes. Never a change from something — no \"darker\", "
        "no \"lower\", no \"more than before\".",
        "",
        "OUTPUT FORMAT — one line, nothing else:",
        "",
        "SCENE: <the paragraph>",
    ]
    return "\n".join(parts).strip()


# Kept as the solo system prompt, unchanged — anything reading `COMPOSE_SYSTEM`
# directly (tests included) still sees exactly today's text.
COMPOSE_SYSTEM = compose_system()

_SCENE_LINE_RE = re.compile(r"(?is)\bSCENE\s*[:：]\s*(.+)$")


def parse_compose(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    match = _SCENE_LINE_RE.search(text)
    scene = (match.group(1) if match else text).strip()
    # One paragraph. A model that ignored "no headings" gets flattened rather
    # than having its stray newlines reach the sampler.
    return " ".join(scene.split())


async def run_compose(
    ollama, *, table_block: str, standing: str, model: str,
    num_ctx: int | None, name_a: str = "", name_b: str = "",
    on_token: TokenCallback | None = None,
) -> str:
    """Render the facet table into prose. A pure function of the table.

    The prompt is the table and the standing rules, and NOTHING else — no chat,
    no theme, no brief, no previous prompt, no board image. That is the whole
    design: composing was never the thing that went wrong, being handed twenty
    turns of contradicting history was. There is a test asserting this prompt
    stays empty of all of it.

    `name_b` present switches the system prompt to the W-Muse form — see
    `compose_system`.
    """
    prompt = "\n\n".join(b for b in [
        f"THE SHOT, IN PARTS:\n{table_block}", standing,
    ] if b.strip())
    system = compose_system(name_a=name_a, name_b=name_b) if name_b else COMPOSE_SYSTEM
    return parse_compose(await _call(
        ollama, system=system, prompt=prompt, model=model,
        images=None, num_ctx=num_ctx, think=False, on_token=on_token,
    ))


async def run_duet_talk(
    ollama, *, user_prompt: str, model: str, num_ctx: int | None,
    character: dict[str, Any] | None = None,
    partner_character: dict[str, Any] | None = None, seed: str = "",
    images: list[bytes] | None = None,
    on_token: TokenCallback | None = None,
    tier: str = "",
    locale: str = "ja",
    intent: str = "",
) -> tuple[str, tuple[dict[str, str], ...] | None, bool, str, str, str]:
    """Conversation turn. Returns say, turns, blind, aside, card, pitch."""
    if partner_character:
        system = crew.w_actress_duet_prompt(
            character or {}, partner_character, mode="talk", seed=seed, tier=tier,
            locale=locale, intent=intent,
        )
    else:
        system = crew.actress_duet_prompt(
            character or {}, mode="talk", seed=seed, locale=locale, intent=intent,
        )

    raw, blind = await _call_seeing(
        ollama,
        system=system,
        prompt=user_prompt, model=model, images=images,
        num_ctx=num_ctx, think=False, on_token=on_token,
    )
    blocks = identity.parse_talk_blocks(raw)
    text = identity.sanitize_muse_say(blocks["say"] or raw, locale=locale)
    if not text:
        raise ChainError("empty duet turn")
    turns = None
    if partner_character:
        name_a = str(
            (character or {}).get("name_ja")
            or (character or {}).get("name") or ""
        )
        name_b = str(
            partner_character.get("name_ja")
            or partner_character.get("name") or ""
        )
        turns = identity.parse_duet_speakers(
            text, name_a=name_a, name_b=name_b, locale=locale,
        )
    turns_out = tuple(turns) if turns else None
    aside = identity.sanitize_muse_say(blocks["aside"], locale=locale)
    return text, turns_out, blind, aside, blocks["card"], blocks["pitch"]


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
        framing=framing, brief=brief, style=style, cast=cast, duet=True,
    )
    if partner_character:
        name_a = str(
            (character or {}).get("name_ja")
            or (character or {}).get("name") or ""
        )
        name_b = str(
            partner_character.get("name_ja")
            or partner_character.get("name") or ""
        )
        turns = identity.parse_duet_speakers(
            turn.say, name_a=name_a, name_b=name_b,
        )
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
    text = identity.sanitize_muse_say(say or raw)
    if not text:
        raise ChainError("empty banter")
    return text


async def run_table_talk(
    ollama, *, system: str, user_prompt: str, model: str,
    num_ctx: int | None,
    images: list[bytes] | None = None,
    on_token: TokenCallback | None = None,
) -> str:
    """Packed multi-seat banter — raw SPEAKER/SAY text, no craft parse."""
    if images:
        raw, _blind = await _call_seeing(
            ollama, system=system, prompt=user_prompt, model=model,
            images=images, num_ctx=num_ctx, think=False, on_token=on_token,
        )
    else:
        raw = await _call(
            ollama, system=system, prompt=user_prompt, model=model,
            images=None, num_ctx=num_ctx, think=False, on_token=on_token,
        )
    text = str(raw or "").strip()
    if not text:
        raise ChainError("empty table talk")
    return text


# Being caught reading her diary used to be its own call, made while the panel
# waited on a read receipt. It is now a block on her next turn's user prompt
# (`crew.caught_block`) — she brings it up when they next meet, which is both
# how a person would find out and one fewer model load.


SCRIPTER_SYSTEM = """
You are the studio scripter. You do not speak in character. You maintain the
shot notebook. You do not write tags or craft_scene on conversation turns.

LANGUAGE: All instructions and field values you write are in English.
(Conversation history may contain Japanese — read it; still write notebook
fields in English.)

You are given the conversation, not just the last line. Read it.

INTENTS (pick one):
- casual — chit-chat only. Do not change SHOT sections. vibe may update.
- shot — showrunner changed the picture. Patch absolute values. No tags.
- mixed — both chat and picture. Patch what changed. No tags.
- recall — asking about past shoots. Do not change SHOT. vibe optional.

READING THE ROOM:
- Resolve what the showrunner means from the conversation. Short affirmations
  (e.g. Japanese「うん」/「それで」/「いいね」, or "yes" / "ok") affirm whatever
  was just proposed; references like「さっきの」/ "that earlier one" point back
  at a concrete earlier line. Nothing is pending unless the conversation says so.
- A change a Muse proposed and the showrunner accepted is a change to the
  picture. Patch it. Do not wait to be told a second time in plainer words.
- A Muse-proposed body action the showrunner did not contradict belongs in
  beat. Combine their direction with how she said she is holding it (SAY and
  CARD BEAT). Do not drop her pose to keep only the last noun they typed.
- A change is a change whatever words it arrived in. Judge by what the picture
  would look like now versus the notebook — not by whether some keyword showed
  up. Changing clothes and changing location are shot changes.
- 「まだ撮らなくていい」and chatting about the picture without asking to change
  it are casual. Do not lift them into shot.
- Questions about a past shoot, last time, 「この間」「前回」「あのとき」/
  「覚えてる」, or how a previous take felt, are recall. Clothes or place
  words inside a memory question do not make it shot.
- Decide from the conversation whose card an edit belongs to. An edit addressed
  to one Muse never touches the other's wearing / beat. A change meant for both
  patches both.
- A held prop belongs in beat; something worn belongs in wearing. You decide.
- When the picture did not move, say casual and change nothing. Do not repaint
  the notebook to look busy.

THE STILL IS THE LAST TAKE, NOT THE ASK:
- If a board image is attached, it is the previous take (the base).
- The current frame is that base PLUS what chat / the Muse CARD changed.
- Do not copy a hat (or anything else) from the photo if chat already removed it.
- Do not invent inventory the CARD and latest line did not ask for.
- Priority: Showrunner's latest direction together with uncontradicted Muse
  CARD/SAY body action > chat delta from the still > what the photo still shows.
- Mood-only SAY does not belong in scene or wearing. Body action from Muse
  SAY and CARD BEAT belongs in beat.

FIELD CONTRACTS (hard):
- scene = short place + time. NEVER paste long prose.
- atmosphere = mood/feeling only (tender, hushed, lonely). NEVER clock words,
  weather-as-hour, objects, or place nouns.
- frame / wearing / beat = short absolute phrases, not paragraphs.
- On a remove request, rewrite wearing as the finished state WITHOUT that noun.
  Do not write "no hat" / "remove hat". Omit the hat.

RULES:
- Write ABSOLUTE finished values, never "more" / "less" / "remove X" alone.
- When clothes or place change, rewrite wearing / scene as the finished state.
- wearing is the only home for clothes, hats, accessories on the body.
- Hairstyle changes belong in wearing. They override the character sheet.
- beat is body action only. Never put looking_up / looking_down /
  looking_at_viewer in beat — gaze belongs in frame with camera angle.
- Low angle / worm's-eye → frame must say she looks down toward the lens.
- If they ask to look at the sky, rewrite frame as one coherent camera story.
- Leave sections unchanged by omitting them (or list under unchanged).
- open is for Muse proposals not yet affirmed. clear_open: true when affirming
  or dropping them.
- Do NOT output tags, tags_shared, tags_a, tags_b, or craft_scene. Leave them "".
- Partner shoots: wearing_b / beat_b. Solo: leave those unused.
- Do not invent diary props. Only the notebook + CARD + showrunner line + still-as-base.
- Do not restore struck items named in the prompt.

Respond with a single JSON object matching the schema. Empty string means
clear that section; omit keys you are not changing.
""".strip()

SCRIPTER_WEAVE_SYSTEM = """
You are the studio scripter in WEAVE mode. You do not speak in character.
You expand the current notebook into sampler tags and craft_scene prose.
You do not rewrite SHOT fields.

LANGUAGE: English only for tags and craft_scene.

SOURCE: NOTEBOOK NOW is the only inventory. No theme, no chat, no photo.

THICKEN QUALITY, NOT INVENTORY:
- Unpack what is already named: cloth (knit, drape, folds), light (how it
  falls, shadow length), air, camera, eyes and hands.
- If wearing says "thin cardigan", write cardigan + fabric + folds — not a hat.
- If beat names a bench, the bench may be tagged. Do not add a vending machine.
- Do not add clothes, hats, lanterns, animals, or furniture the notebook
  does not name.
- Struck items listed in the prompt must not appear, including no_hat forms.

CEILINGS, NOT QUOTAS:
- At most 35–55 tags; craft_scene at most 140–200 words. Do not invent nouns
  to hit a count. Do not ship a 8-tag summary of the notebook either —
  unpack quality of what is there.

Partner shoots: tags_shared + tags_a + tags_b (never one mixed bag).
Solo: tags only.

INTENT: shot. Absolute values. Do not rewrite atmosphere/scene/frame/wearing/beat.
Leave those keys omitted or empty. English only.

Respond with a single JSON object matching the schema.
""".strip()

SCRIPTER_VERIFY_NOTE = (
    "VERIFY: Re-read the showrunner's latest line against NOTEBOOK NOW and the "
    "conversation. If following that line would make the picture look different "
    "(place, clothes, hairstyle, pose, camera, worn or held props, taking "
    "something off), return intent shot or mixed with ABSOLUTE finished values "
    "and NO tags. If it is truly chit-chat with no picture change, return intent "
    "casual again with no SHOT edits. Do not invent. Do not copy the still as "
    "the current ask."
)

SCRIPTER_CONSISTENCY_NOTE = (
    "REPAIR: Your last notebook patch left wearing/scene disagreeing with what "
    "the showrunner asked. Return intent shot with ABSOLUTE wearing/scene. "
    "Do not emit tags."
)

STILL_READ_SYSTEM = """
You are reading the latest test still for the studio notebook.
Write labelled English absolute values for what is in the photo.
Do not invent. Do not restore items listed as STRUCK.

ATMOSPHERE: mood/feeling only — no clock, no objects.
SCENE: short place + time (dusk goes here, not in ATMOSPHERE).
FRAME: camera and gaze.
WEARING: clothes and hair on the body. Omit struck items even if visible.
BEAT: body action. Held props here.
(Partner: WEARING_B / BEAT_B when two people.)

No TAGS. No JSON. No SAY.
""".strip()



async def run_scripter(
    ollama, *, notebook_block: str, note: str, transcript: str = "",
    theme: str = "", style: str = "", framing: str = "",
    partner: bool = False, model: str, num_ctx: int | None,
    mode: str = "compile", images: list[bytes] | None = None,
    card: str = "", struck: str = "",
) -> dict[str, Any]:
    """One non-stream scripter call: compile (notebook) or weave (tags).

    ``compile`` uses the conversation and optional still-as-base. ``weave``
    sees only the notebook. Images never mix with JSON schema — labelled
    parse_scripter fallback.
    """
    from ..ai.llm_options import llm_options
    from . import notebook as notebook_mod

    weave = mode == "weave"
    system = SCRIPTER_WEAVE_SYSTEM if weave else SCRIPTER_SYSTEM
    if weave:
        prompt = "\n\n".join(b for b in [
            f"NOTEBOOK NOW:\n{notebook_block}",
            f"STRUCK (do not restore):\n{struck}" if struck.strip() else "",
            (
                "WEAVE: expand TAGS and CRAFT_SCENE from the notebook only. "
                "Thicken quality (cloth, light, air, camera, eyes/hands) of "
                "what is already named. Do not add inventory. Do not rewrite "
                "SHOT. Ceilings 35–55 tags / 140–200 words — not quotas. "
                "INTENT: shot. English only."
            ),
            "Partner Muse: tags_shared + tags_a + tags_b." if partner else
            "Solo shoot — use tags only.",
            "Return JSON only.",
        ] if b.strip())
    else:
        prompt = "\n\n".join(b for b in [
            f"THEME:\n{theme}" if theme.strip() else "",
            f"STYLE: {style}" if style.strip() else "",
            f"FRAMING: {framing}" if framing.strip() else "",
            f"NOTEBOOK NOW:\n{notebook_block}",
            (
                "MUSE CARD (absolute names for this frame; the still is the "
                "last take, chat is the delta from that take):\n"
                f"{card.strip()}"
            ) if card.strip() else "",
            f"STRUCK (do not restore):\n{struck}" if struck.strip() else "",
            (
                "CONVERSATION SO FAR (who said what — read this to resolve "
                "affirmations, Muse-proposed poses, and what changed from the "
                "still; write notebook values in English):\n"
                f"{transcript.strip()}"
            ) if transcript.strip() else "",
            (
                "The attached image is the previous take (the base), not the "
                "current ask. Apply chat + CARD on top of it."
            ) if images else "",
            f"SHOWRUNNER'S LATEST LINE:\n{note.strip()}",
            "Partner Muse sections wearing_b/beat_b apply." if partner else
            "Solo shoot — leave wearing_b and beat_b unused.",
            "Return JSON only. Do not emit tags or craft_scene.",
        ] if b.strip())

    raw = ""
    validate_mode = "weave" if weave else "compile"

    if images:
        try:
            raw, _ = await _call_seeing(
                ollama, system=system, prompt=prompt, model=model,
                images=images, num_ctx=num_ctx, think=False, on_token=None,
            )
        except ChainError:
            logger.warning("[muse.chain] scripter image turn produced nothing",
                           exc_info=True)
            return notebook_mod.validate_scripter(
                notebook_mod._blank_result(""), partner=partner, mode=validate_mode,
            )
        parsed = notebook_mod.parse_scripter(raw)
        return notebook_mod.validate_scripter(
            parsed, partner=partner, mode=validate_mode,
        )

    gen = getattr(ollama, "generate_text", None)
    if callable(gen):
        try:
            options = llm_options({"num_predict": -1}, model=model, num_ctx=num_ctx)
            try:
                raw = await gen(
                    prompt, model=model, options=options,
                    system=system, think=False,
                    fmt=notebook_mod.SCRIPTER_FORMAT_SCHEMA,
                )
            except TypeError:
                raw = await gen(
                    prompt, model=model, options=options,
                    system=system, think=False,
                )
            except Exception:
                logger.warning(
                    "[muse.chain] scripter schema format failed; retry plain",
                    exc_info=True,
                )
                raw = await gen(
                    prompt, model=model, options=options,
                    system=system, think=False,
                )
        except Exception:
            logger.warning("[muse.chain] scripter generate_text failed", exc_info=True)
            raw = ""
    if not str(raw or "").strip():
        try:
            raw = await _call(
                ollama, system=system, prompt=prompt, model=model,
                images=None, num_ctx=num_ctx, think=False, on_token=None,
            )
        except ChainError:
            logger.warning("[muse.chain] scripter turn produced nothing", exc_info=True)
            return notebook_mod.validate_scripter(notebook_mod._blank_result(""))
    parsed = notebook_mod.parse_scripter(raw)
    validated = notebook_mod.validate_scripter(
        parsed, partner=partner, mode=validate_mode,
    )
    if validated.get("valid") or not str(raw or "").strip():
        return validated
    if not callable(gen):
        return validated
    reason = str(validated.get("refuse_reason") or "invalid_or_unparseable")
    repair_prompt = "\n\n".join([
        "Your previous studio-scripter output was rejected "
        f"({reason}). Return ONLY a corrected JSON object matching the schema. "
        "No prose, no markdown fences.",
        f"PREVIOUS OUTPUT:\n{str(raw)[:3500]}",
        f"ORIGINAL REQUEST:\n{prompt}",
    ])
    try:
        options = llm_options({"num_predict": -1}, model=model, num_ctx=num_ctx)
        try:
            raw2 = await gen(
                repair_prompt, model=model, options=options,
                system=system, think=False,
                fmt=notebook_mod.SCRIPTER_FORMAT_SCHEMA,
            )
        except TypeError:
            raw2 = await gen(
                repair_prompt, model=model, options=options,
                system=system, think=False,
            )
        except Exception:
            raw2 = await gen(
                repair_prompt, model=model, options=options,
                system=system, think=False,
            )
        if str(raw2 or "").strip():
            repaired = notebook_mod.validate_scripter(
                notebook_mod.parse_scripter(raw2), partner=partner,
                mode=validate_mode,
            )
            if repaired.get("valid"):
                logger.info("[muse.chain] scripter repair pass succeeded")
                return repaired
    except Exception:
        logger.warning("[muse.chain] scripter repair pass failed", exc_info=True)
    return validated


async def run_still_read(
    ollama, *, notebook_block: str, struck: str, partner: bool,
    model: str, num_ctx: int | None, images: list[bytes],
) -> dict[str, Any]:
    """Labelled still-read after a board lands. No JSON schema with the image."""
    from . import notebook as notebook_mod

    if not images:
        return notebook_mod._blank_result("")
    prompt = "\n\n".join(b for b in [
        f"NOTEBOOK NOW:\n{notebook_block}",
        f"STRUCK (do not put these back, even if visible):\n{struck}"
        if struck.strip() else "",
        "Read the attached still. Labelled blocks only.",
        "Partner: include WEARING_B / BEAT_B." if partner else "Solo.",
    ] if b.strip())
    try:
        raw, _ = await _call_seeing(
            ollama, system=STILL_READ_SYSTEM, prompt=prompt, model=model,
            images=images, num_ctx=num_ctx, think=False, on_token=None,
        )
    except ChainError:
        logger.warning("[muse.chain] still-read produced nothing", exc_info=True)
        return notebook_mod._blank_result("")
    parsed = notebook_mod.parse_scripter(raw)
    parsed["intent"] = "shot"
    return notebook_mod.validate_scripter(
        parsed, partner=partner, mode="compile",
    )
