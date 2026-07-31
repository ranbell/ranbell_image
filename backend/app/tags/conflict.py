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

# Subject gender is its own kind of contradiction: it does not need a shared
# head noun. A theme composed `male_swimwear` for a character whose identity
# says `1girl`, and the board rendered men's trunks over a bikini top.
_FEMALE_MARKERS = frozenset({
    "1girl", "2girls", "3girls", "multiple_girls", "girl", "girls",
    "female", "woman", "women", "adult_female", "mature_female",
})
_MALE_MARKERS = frozenset({
    "1boy", "2boys", "3boys", "multiple_boys", "boy", "boys",
    "male", "man", "men", "adult_male", "mature_male",
})


# How many subjects the picture has is the other slot that needs no shared
# noun. `solo` and `2girls` disagree about the whole picture, and a prompt
# holding both renders whichever the checkpoint prefers.
_SINGLE_MARKERS = frozenset({"solo", "solo_focus", "1girl", "1boy", "1other"})
_MULTI_MARKERS = frozenset({
    "2girls", "3girls", "4girls", "5girls", "6+girls", "multiple_girls",
    "2boys", "3boys", "4boys", "multiple_boys", "multiple_others",
    "group", "crowd", "couple", "duo", "trio",
})


def _count(tag: str) -> str | None:
    """"one" / "many" when this tag says how many subjects there are."""
    name = str(tag or "").strip().lower().replace(" ", "_")
    if name in _SINGLE_MARKERS:
        return "one"
    if name in _MULTI_MARKERS:
        return "many"
    return None


def _gender(tag: str) -> str | None:
    """"female" / "male" when this tag asserts one, else None."""
    tokens = set(str(tag or "").strip().lower().replace(" ", "_").split("_"))
    name = str(tag or "").strip().lower().replace(" ", "_")
    if name in _FEMALE_MARKERS or tokens & _FEMALE_MARKERS:
        return "female"
    if name in _MALE_MARKERS or tokens & _MALE_MARKERS:
        return "male"
    return None


def _parts(tag: str) -> tuple[str, frozenset[str]]:
    """``('hair', {'very', 'long'})`` for ``very_long_hair``."""
    tokens = [t for t in str(tag or "").strip().lower().replace(" ", "_").split("_") if t]
    if not tokens:
        return "", frozenset()
    return tokens[-1], frozenset(tokens[:-1])


def contradicts(tag: str, other: str) -> bool:
    """True when both tags fill the same attribute slot with different values."""
    a_gender, b_gender = _gender(tag), _gender(other)
    if a_gender and b_gender and a_gender != b_gender:
        return True

    a_count, b_count = _count(tag), _count(other)
    if a_count and b_count and a_count != b_count:
        return True

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
