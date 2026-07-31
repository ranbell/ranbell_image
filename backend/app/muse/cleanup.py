"""LLM cleanup of a track's harvested tags.

The rule filters get the easy cases and then stop. Nothing in a frozenset knows
that ``zettai_ryouiki`` presupposes a girl's thighs, that ``pokemon_(creature)``
drags a franchise into the frame, or that ``official_alternate_costume`` is an
instruction to draw a costume chart rather than a description of anything. Real
runs produced all three, and each one survived every deterministic filter and
reached the final image.

So after WD14 reads the board back, one small model looks at each track's list
and says which tags do not belong to that track.

The danger is obvious and the prompt is built around it: a model told to "clean
up" a tag list will cheerfully delete everything unusual, and the unusual tail
is the entire reason this pipeline reads its own drafts at threshold 0.15. The
instructions therefore enumerate what may be removed and state plainly that
being odd, rare or unexpected is not on the list. On any failure the tags pass
through untouched — losing the run is worse than keeping a bad tag.
"""
from __future__ import annotations

import logging
from typing import Any

from ..ai.json_util import parse_json_object
from ..ai.llm_options import llm_options

logger = logging.getLogger(__name__)

# What each track is for, in the model's words rather than ours.
_TRACK_ROLE = {
    "background": (
        "the SETTING of the picture: the place, the architecture, the weather, "
        "the time of day, the light, and objects that sit in the scene"
    ),
    "person": (
        "the CHARACTER in the picture: her body, her clothes, what she carries, "
        "her pose and her expression"
    ),
}

_TRACK_FOREIGN = {
    "background": "anything that only makes sense as part of a person",
    "background_examples": (
        "zettai_ryouiki, thighhighs, closed_eyes, maid_headdress, hood_down, "
        "1girl, solo, blush"
    ),
    "person": "anything that describes the location or the backdrop rather than her",
    "person_examples": (
        "simple_background, blue_background, library, indoors, scenery, "
        "cloudy_sky, night"
    ),
}

_PROMPT = """\
# ROLE
You are reviewing Danbooru tags that were read automatically off some rough
draft images. Your only job is to say which tags do not belong on this list.

# WHAT THIS LIST IS FOR
This list describes {role}.

# THE PICTURE BEING BUILT
Theme: {theme}
{character_block}
# TAGS
{tags}

# REMOVE A TAG ONLY IF IT IS ONE OF THESE
1. WRONG TRACK — {foreign}. Examples: {foreign_examples}
2. A NAMED CHARACTER, franchise, series or creature from an existing work —
   anything in parentheses like foo_(bar), plus recognisable character names.
   These make the picture be about somebody else's property.
3. A FRAMING OR SHEET ARTIFACT — the draft's own layout leaking in, not
   anything in the scene: multiple_views, reference_sheet, character_sheet,
   alternate_costume, official_alternate_costume, cropped_legs, border,
   letterboxed, chibi, isometric, fisheye.
4. CONTRADICTS THE FIXED CHARACTER above, if one is given — a different hair
   colour, eye colour or body type than the one stated.

# DO NOT REMOVE A TAG FOR ANY OTHER REASON
Being strange, rare, unexpected, off-theme, low-confidence or simply not what
you would have chosen is NOT a reason. Those tags are wanted. If you are unsure
about a tag, keep it. Removing too much is a worse mistake than keeping a bad
tag.

# OUTPUT (JSON only)
{{"remove": [{{"tag": "<exact tag from the list>", "reason": "<wrong_track|franchise|artifact|contradicts>"}}]}}
Output an empty list if nothing qualifies."""

_VALID_REASONS = {"wrong_track", "franchise", "artifact", "contradicts"}


def _character_block(identity_tags: list[str]) -> str:
    if not identity_tags:
        return ""
    return "Fixed character (cannot change): " + ", ".join(identity_tags) + "\n"


async def clean_track(
    rows: list[dict[str, Any]],
    track: str,
    ollama,
    *,
    theme: str = "",
    identity_tags: list[str] | None = None,
    model: str = "",
    num_ctx: int | None = None,
    max_removed_ratio: float = 0.5,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """``(kept, removed)`` for one track's harvested tags.

    ``max_removed_ratio`` is a backstop against the failure this whole prompt is
    written to avoid. If the model asks to delete more than half the list it has
    stopped reviewing and started curating, and the request is discarded whole.
    """
    if not rows or track not in _TRACK_ROLE:
        return rows, []

    known = {r["tag"].lower(): r for r in rows}
    prompt = _PROMPT.format(
        role=_TRACK_ROLE[track],
        theme=theme or "(none given)",
        character_block=_character_block([t for t in (identity_tags or []) if t]),
        tags=", ".join(r["tag"] for r in rows),
        foreign=_TRACK_FOREIGN[track],
        foreign_examples=_TRACK_FOREIGN[f"{track}_examples"],
    )

    try:
        raw = await ollama.generate_text(
            prompt,
            model=model or None,
            options=llm_options(model=model, num_ctx=num_ctx),
            fmt="json",
        )
        parsed = parse_json_object(raw if isinstance(raw, str) else str(raw))
    except Exception as exc:
        logger.warning("[muse] tag cleanup failed for %s: %s", track, exc)
        return rows, []

    removed: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parsed.get("remove") or []:
        if isinstance(item, str):
            tag, reason = item, "wrong_track"
        elif isinstance(item, dict):
            tag = str(item.get("tag") or "")
            reason = str(item.get("reason") or "wrong_track")
        else:
            continue
        key = tag.strip().lower().replace(" ", "_")
        # Only tags that were actually on the list — a model that invents one
        # has drifted, and acting on it would drop nothing while looking like
        # it did something.
        if key not in known or key in seen:
            continue
        seen.add(key)
        removed.append({
            "tag": known[key]["tag"],
            "reason": reason if reason in _VALID_REASONS else "wrong_track",
        })

    if len(removed) > len(rows) * max_removed_ratio:
        logger.warning(
            "[muse] cleanup wanted %d of %d %s tags — ignoring the whole request",
            len(removed), len(rows), track,
        )
        return rows, []

    kept = [r for r in rows if r["tag"].lower() not in seen]
    return kept, removed
