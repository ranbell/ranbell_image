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
) -> dict[str, Any]:
    """Merge both tracks into ``{tags, positive, protected, removed, analysis}``.

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
    protected = [t for t in (protected_tags or []) if t]
    if protected:
        line = insert_after_anchors(line, [])
        line = _prepend(line, protected)
    line = ensure_subject_anchor(line, docs)

    tags = [t.strip() for t in line.split(",") if t.strip()]
    tags = [t for t in tags if not is_junk_tag(t)]

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
        # character on purpose, and Admin's list is about prompt hygiene.
        keep_back = [t for t in removed if t in protected]
        tags = kept + keep_back
        removed = [t for t in removed if t not in protected]

    return {
        "tags": tags,
        "positive": ", ".join(tags),
        "protected": protected,
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
