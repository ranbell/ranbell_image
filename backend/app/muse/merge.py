"""S6: merge the background and character tag sets into one prompt.

This reuses Refine's weighted merge wholesale — the common/unique split, the
per-image budget, and the token-overlap conflict pass that stops
``blonde_hair`` and ``purple_hair`` both surviving. What is new here is either
side of it:

*before* — each track's three board images are folded into one synthetic
document (``harvest.fold_track``), so the weight dial is background-vs-character
rather than image-vs-image;

*after* — the character's locked tags are forced back in. That part is not
optional. ``_build_weighted_wd14_context`` gives each side a budget of
``unique_count × weight``, and it spends that budget on ``must`` tags first but
still truncates to it. Push the dial to 0.9 background and the character's hair
and eye colour fall off the end of the list — which is exactly the drift the old
pipeline was abandoned over.
"""
from __future__ import annotations

import logging
from typing import Any

from ..prompt.tag_merge import (
    _build_all_must,
    _build_weighted_wd14_context,
    _resolve_weights,
    filter_tag_list,
)
from ..tags.conflict import contradicts_any
from ..tags.junk import is_junk_tag
from . import camera
from . import slots as slot_defs
from ..tags.subject_anchors import ensure_subject_anchor, insert_after_anchors

logger = logging.getLogger(__name__)

# Background sets the scene, the character is the subject. These are Refine's
# own role labels and they mean the same thing here.
TRACK_ROLES = {"background": "style", "person": "content"}


def _as_document(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A folded track, shaped like an image document for the Refine merge."""
    return {
        "wd14_tags": [r["tag"] for r in rows],
        "wd14_tags_scores": [float(r.get("score") or 0.0) for r in rows],
    }


def merge_tracks(
    folded: dict[str, list[dict[str, Any]]],
    *,
    character_weight: float = 0.5,
    common_ratio: float = 0.5,
    unique_count: int = 30,
    protected_tags: list[str] | None = None,
    removal: set[str] | None = None,
    reinforcements: list[str] | None = None,
    must_tags: list[str] | None = None,
    shot: str = "auto",
    user_slots: dict[str, list[str]] | None = None,
    theme: str = "",
) -> dict[str, Any]:
    """Merge both tracks into a slotted prompt.

    ``user_slots`` are the aspects the user owns outright (style, shot, effect)
    and they overwrite whatever landed in the same key.

    ``character_weight`` is the dial: 0.0 is all background (a wide establishing
    shot), 1.0 is all character (a portrait).
    """
    weight = min(max(float(character_weight), 0.0), 1.0)
    docs = [
        (_as_document(folded.get("background") or []), 0),
        (_as_document(folded.get("person") or []), 1),
    ]
    weights = _resolve_weights(["background", "person"], [1.0 - weight, weight])
    roles = [TRACK_ROLES["background"], TRACK_ROLES["person"]]

    context, analysis = _build_weighted_wd14_context(
        docs, weights, set(),
        common_ratio=common_ratio,
        unique_count=unique_count,
        roles=roles,
    )

    ordered = list(analysis.get("common_tags") or [])
    for tag in _build_all_must(analysis):
        if tag not in ordered:
            ordered.append(tag)

    line = ", ".join(ordered)
    # The character's own tags go back in regardless of what the budget did to
    # them, and ahead of everything else.
    # The user's own must-keeps rank with the character's identity. `solo` is
    # the case that made this necessary: it was in the prompt and lost anyway
    # to a poolside scene that a checkpoint knows is full of people.
    forced = [t.strip() for t in (must_tags or []) if str(t).strip()]
    protected = forced + [
        t for t in (protected_tags or []) if t and t not in forced
    ]
    if protected:
        line = insert_after_anchors(line, [])
        line = _prepend(line, protected)
    line = ensure_subject_anchor(line, docs)

    tags = [t.strip() for t in line.split(",") if t.strip()]
    # What the picture was missing, chosen after it existed. Appended rather
    # than budgeted: five tags cannot outweigh a hundred read off the canvas,
    # and they are the only ones here nobody has seen rendered yet.
    for tag in (reinforcements or []):
        if tag and tag.lower() not in {t.lower() for t in tags}:
            tags.append(tag)
    tags = [t for t in tags if not is_junk_tag(t)]

    # One framing, chosen deliberately. Three seeds produce three framings and
    # the merge would otherwise keep all of them.
    tags, framing_dropped = camera.apply(tags, shot)

    # Putting `brown_eyes` at the head does nothing while `blue_eyes` is still
    # in the list — the model sees both and picks one. Protection has to evict,
    # not just lead. `_build_weighted_wd14_context` already drops conflicts, but
    # only across images: two contradictory tags harvested from the same track
    # both survive it.
    evicted: list[str] = []
    if protected:
        protected_set = {t.lower() for t in protected}
        keep: list[str] = []
        for tag in tags:
            if tag.lower() in protected_set:
                keep.append(tag)
            elif contradicts_any(tag, protected):
                evicted.append(tag)
            else:
                keep.append(tag)
        tags = keep

    removed: list[str] = []
    if removal:
        kept = filter_tag_list(tags, removal)
        removed = [t for t in tags if t not in kept]
        # A protected tag survives the removal list too — the user picked this
        # character on purpose, and Admin's list is about prompt hygiene. It has
        # to go back where it was, not on the end: leading the prompt is the
        # whole point of protecting it, and a rescued tag appended to the tail
        # is in the part of the prompt attention no longer reaches.
        rescued = {t for t in removed if t in protected}
        tags = [t for t in tags if t in kept or t in rescued]
        removed = [t for t in removed if t not in rescued]

    # Re-slot the survivors so the prompt comes out aspect by aspect. A flat
    # comma list lets one aspect dominate by sheer repetition, which is what
    # put three swimsuit tags in one prompt and a swimsuit across one frame.
    filled: dict[str, list[str]] = {}
    unplaced: list[str] = []
    reinforced = {t.lower() for t in (reinforcements or [])}

    def _slot_of(tag: str) -> str:
        # Anything no slot claims still belongs in the picture; Object is where
        # a loose noun does least harm.
        return slot_defs.place_tag(tag) or "object"

    # Reinforcements first, so the cap trims the harvested tail rather than the
    # handful somebody chose *because* the picture lacked them.
    for tag in tags:
        if tag.lower() in reinforced:
            filled.setdefault(_slot_of(tag), []).append(tag)
    for tag in tags:
        if tag.lower() in reinforced:
            continue
        key = slot_defs.place_tag(tag)
        if key is None:
            unplaced.append(tag)
            key = "object"
        filled.setdefault(key, []).append(tag)

    if protected:
        filled["character"] = protected + (filled.get("character") or [])
    for key, slot in slot_defs.BY_KEY.items():
        if key in filled:
            filled[key] = slot_defs.dedupe_slot(filled[key], slot.cap)
    for key, values in (user_slots or {}).items():
        if values:
            filled[key] = list(values)

    return {
        "tags": slot_defs.flatten(filled),
        "slots": filled,
        "positive": slot_defs.render_prompt(filled, theme=theme),
        "unplaced": unplaced,
        "protected": protected,
        "forced": forced,
        "reinforcements": list(reinforcements or []),
        "shot": shot,
        "framing_dropped": framing_dropped,
        "evicted": evicted,
        "removed": removed,
        "context": context,
        "analysis": analysis,
        "weights": {"background": weights[0], "person": weights[1]},
    }


def _prepend(tag_line: str, lead: list[str]) -> str:
    """Put ``lead`` at the head, keeping the rest in order and deduped."""
    existing = [t.strip() for t in tag_line.split(",") if t.strip()]
    seen = {t.lower() for t in lead}
    return ", ".join(lead + [t for t in existing if t.lower() not in seen])
