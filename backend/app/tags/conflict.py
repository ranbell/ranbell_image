"""Does one tag contradict another?

``prompt.tag_merge._tags_conflict`` answers a looser question — "do these two
tags overlap enough that the lower-weighted one should yield" — and it is right
for merging several images, where losing a borderline tag costs nothing.

It is too blunt for eviction. Told to protect ``purple_hair`` it also throws out
``long_hair`` and ``hair_ribbon``, because all three contain "hair". A character
can have long purple hair and a hair ribbon.

The distinction that matters is the *slot*: two tags contradict when they
describe the same feature with mutually exclusive values. ``blue_eyes`` and
``green_eyes`` are both an eye colour, so only one can be true; ``green_eyes``
and ``closed_eyes`` describe colour and state, so both can.
"""
from __future__ import annotations

_COLOURS = frozenset({
    "aqua", "black", "blonde", "blue", "brown", "green", "grey", "gray",
    "orange", "pink", "purple", "red", "silver", "white", "yellow",
    "violet", "teal", "cyan", "magenta", "beige", "tan", "gold", "golden",
    "platinum", "auburn", "ginger", "lavender", "crimson", "scarlet",
    "multicolored", "rainbow", "two-tone", "gradient", "streaked",
})

_LENGTHS = frozenset({
    "long", "short", "medium", "very", "absurdly", "waist", "knee",
    "shoulder", "chin", "ear", "hip", "floor",
})

# Modifier families that occupy one slot. Two tags on the same head noun clash
# only when their modifiers come from the same family.
_FAMILIES: tuple[frozenset[str], ...] = (_COLOURS, _LENGTHS)


def _parts(tag: str) -> tuple[str, frozenset[str]]:
    """``('hair', {'very', 'long'})`` for ``very_long_hair``."""
    tokens = [t for t in str(tag or "").strip().lower().replace(" ", "_").split("_") if t]
    if not tokens:
        return "", frozenset()
    return tokens[-1], frozenset(tokens[:-1])


def contradicts(tag: str, other: str) -> bool:
    """True when both tags fill the same attribute slot with different values."""
    a_noun, a_mod = _parts(tag)
    b_noun, b_mod = _parts(other)
    if not a_noun or a_noun != b_noun:
        return False
    if not a_mod or not b_mod or a_mod == b_mod:
        return False
    return any(
        (a_mod & family) and (b_mod & family)
        for family in _FAMILIES
    )


def contradicts_any(tag: str, locked: list[str]) -> bool:
    return any(contradicts(tag, t) for t in locked)
