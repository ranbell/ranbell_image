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
from ..tags.conflict import contradicts, contradicts_any
from ..tags.junk import is_junk_tag
from . import camera
from . import slots as slot_defs
from ..tags.subject_anchors import ensure_subject_anchor, insert_after_anchors

logger = logging.getLogger(__name__)

# Background sets the scene, the character is the subject. These are Refine's
# own role labels and they mean the same thing here.
TRACK_ROLES = {"background": "style", "person": "content"}


# How many of a track's drafts must have shown a tag for it to count as a fact
# about the picture rather than an accident of one seed.
#
# The slot budgets used to hide the difference by only letting the top few tags
# through: a stargazing run harvested 25 clothing tags and Outfit's cap of four
# cut it to four. Widening the cap to eight without this floor lets the
# disagreement in — the three drafts had put her in a sweater, a jacket, a skirt
# and a winter coat between them, none of which more than one of them agreed on.
#
#     agreement 1.00  scarf, boots, coat, long_sleeves      ← she is wearing these
#     agreement 0.67  brown_scarf, blue_coat, pantyhose     ← she is wearing these
#     agreement 0.33  sweater, jacket, skirt, winter_coat   ← one seed wandered off
#
# With one board image per track every tag scores 1.0 and the floor does
# nothing, which is the right behaviour: one draft cannot disagree with itself.
AGREEMENT_FLOOR = 0.5


def _as_document(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A folded track, shaped like an image document for the Refine merge."""
    return {
        "wd14_tags": [r["tag"] for r in rows],
        "wd14_tags_scores": [float(r.get("score") or 0.0) for r in rows],
    }


def _agreement_of(folded: dict[str, list[dict[str, Any]]]) -> dict[str, float]:
    """``{tag: agreement}`` across both tracks, best score wins."""
    out: dict[str, float] = {}
    for rows in folded.values():
        for row in rows or []:
            key = str(row.get("tag") or "").lower()
            value = float(row.get("agreement", 1.0) or 0.0)
            if key and value > out.get(key, 0.0):
                out[key] = value
    return out


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
    angle: str = "auto",
    user_slots: dict[str, list[str]] | None = None,
    composed_slots: dict[str, list[str]] | None = None,
    texts: list[dict[str, str]] | None = None,
    prose: str = "",
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
        # Refine's own rule — any shared word of three letters or more — is
        # right for six photographs of one subject and wrong for these two
        # documents, which describe a place and a person and are *supposed* to
        # differ. `wet_ground` on the pavement deleted `wet_legs` on the girl,
        # in the one theme where the wet legs are the whole point.
        conflicts=contradicts,
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
    tags, framing_dropped = camera.apply(tags, shot, angle)

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

    protected_set = {t.lower() for t in protected}

    def _slot_of(tag: str) -> str | None:
        # Anything no slot claims still belongs in the picture; Object is where
        # a loose noun does least harm. A loose *adjective* does harm there,
        # though — the line asserts that whatever is on it is a thing in the
        # room — so those are left out rather than mislabelled.
        key = slot_defs.place_tag(tag)
        if key:
            return key
        # A framing word has a slot of its own, it is just not a routable one:
        # Shot is user-owned, so `place_tag` never targets it. Sending it there
        # rather than to Object keeps `wide_shot` in the prompt as a framing
        # instead of announcing it as furniture, and a Shot the user has chosen
        # still overwrites the whole line afterwards.
        if slot_defs.is_framing(tag):
            return "shot"
        return "object" if slot_defs.is_thing(tag) else None

    def _is_the_character(tag: str) -> bool:
        # `1girl` and `pink_hair` are claimed by no routable slot, because
        # Character is locked and excluded from routing. Without this they fell
        # into Object and the prompt named her twice.
        return tag.lower() in protected_set

    # Reinforcements first, so the cap trims the harvested tail rather than the
    # handful somebody chose *because* the picture lacked them.
    for tag in tags:
        if tag.lower() in reinforced:
            key = _slot_of(tag)
            if key:
                filled.setdefault(key, []).append(tag)
            else:
                unplaced.append(tag)
    agreement = _agreement_of(folded)
    weak: list[str] = []
    for tag in tags:
        if tag.lower() in reinforced or _is_the_character(tag):
            continue
        # One draft in three is not evidence. The forced tags and the character
        # are already past; the composed slots never went through a draft at all
        # and are carried in further down, so this only judges what the canvas
        # claimed to have seen.
        if agreement.get(tag.lower(), 1.0) < AGREEMENT_FLOOR:
            weak.append(tag)
            continue
        key = slot_defs.place_tag(tag)
        if key is None:
            unplaced.append(tag)
            key = _slot_of(tag)
            if key is None:
                continue
        filled.setdefault(key, []).append(tag)

    if protected:
        # The character's own words split the same way the preset did: `toned`
        # is a body word and belongs in Body. Leading Character with the whole
        # identity list put it in both, and the prompt said it twice.
        body_slot = slot_defs.BY_KEY["body"]
        head = [t for t in protected if not slot_defs.accepts(body_slot, t)]
        body = [t for t in protected if t not in head]
        filled["character"] = head + (filled.get("character") or [])
        if body:
            filled["body"] = body + [
                t for t in (filled.get("body") or []) if t not in body
            ]

    # The theme's own answer leads the slots that carry intent, ahead of what
    # the drafts showed. Everywhere else the canvas still wins outright.
    #
    # These face the junk filter too. It ran over the harvested tags only, so
    # `white_background` — composed into Light, never rendered, never harvested
    # — walked straight past it into the finished prompt.
    for key, values in (composed_slots or {}).items():
        slot = slot_defs.BY_KEY.get(key)
        values = [t for t in (values or []) if not is_junk_tag(t)]
        if not values or slot is None:
            continue
        if not filled.get(key):
            filled[key] = list(values)
        elif slot.intent:
            # Half the budget, not all of it. The theme's verb has to survive,
            # but the drafts did see the picture and their half is worth
            # keeping — the failure was the overwrite, not the observation.
            lead = list(values)[: max(1, slot.cap // 2)]
            filled[key] = lead + [t for t in filled[key] if t not in lead]

    # One tag, one line. Routing already places each tag once, but a composed
    # slot can name something the drafts put somewhere else: `bus_stop` is in
    # no catalog, so the harvested copy landed in Object while the composed one
    # led Place, and the prompt listed the bus stop twice. The earlier slot
    # keeps it, which is the more specific one — Place before Object.
    claimed: set[str] = set()
    for slot in slot_defs.SLOTS:
        rows = filled.get(slot.key)
        if not rows:
            continue
        filled[slot.key] = [t for t in rows if t.lower() not in claimed]
        claimed |= {t.lower() for t in filled[slot.key]}

    for key, slot in slot_defs.BY_KEY.items():
        if key in filled:
            filled[key] = slot_defs.dedupe_slot(filled[key], slot.cap)
    for key, values in (user_slots or {}).items():
        if values:
            filled[key] = list(values)

    return {
        "tags": slot_defs.flatten(filled),
        "slots": filled,
        "positive": slot_defs.render_prompt(filled, texts=texts, prose=prose),
        "unplaced": unplaced,
        "protected": protected,
        "forced": forced,
        "reinforcements": list(reinforcements or []),
        "shot": shot,
        "angle": angle,
        "framing_dropped": framing_dropped,
        "evicted": evicted,
        "removed": removed,
        "outvoted": weak,
        "context": context,
        "analysis": analysis,
        "weights": {"background": weights[0], "person": weights[1]},
    }


def _prepend(tag_line: str, lead: list[str]) -> str:
    """Put ``lead`` at the head, keeping the rest in order and deduped."""
    existing = [t.strip() for t in tag_line.split(",") if t.strip()]
    seen = {t.lower() for t in lead}
    return ", ".join(lead + [t for t in existing if t.lower() not in seen])
