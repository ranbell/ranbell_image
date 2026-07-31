"""Which track a tag belongs to.

The two tracks describe different halves of one picture, and a tag that wanders
across renders the wrong thing: person tags in a background list put a figure in
an empty room, backdrop tags in a character list put her on a plain studio wall.

`get_tag_axis` covers most of it. The raw frozensets cover what the axis map
lumps into `always_fixed`, which mixes person attributes with props and
composition and so cannot be dropped wholesale.
"""
from __future__ import annotations

from ..tags import catalog as tag_catalog

# Removing person tags from a background positive is not enough to keep people
# out of it: a checkpoint puts a figure in a library because libraries have
# figures in them. The background board has to say so out loud.
BACKGROUND_NEGATIVE = (
    "1girl, 1boy, solo, multiple_girls, multiple_boys, portrait, face, "
    "person, character"
)


# `get_tag_axis` covers most of it; the raw frozensets cover what the axis map
# lumps into `always_fixed`, which mixes person attributes with props and
# composition and so cannot be dropped wholesale.
_PERSON_AXES = frozenset({"hair", "emotion", "action", "clothing", "parts"})
_SCENE_AXES = frozenset({"location", "time_weather"})

_PERSON_SETS = (
    tag_catalog.COUNT, tag_catalog.EYE_SHAPES, tag_catalog.BODY,
    tag_catalog.SKIN_FACE, tag_catalog.RACE,
)
_SCENE_SETS = (
    tag_catalog.ENVIRONMENT, tag_catalog.BACKGROUND, tag_catalog.ABSTRACT_BG,
)


def is_person_tag(tag: str) -> bool:
    name = str(tag or "").lower()
    if tag_catalog.get_tag_axis(name) in _PERSON_AXES:
        return True
    return any(name in s for s in _PERSON_SETS)


def is_scene_tag(tag: str) -> bool:
    name = str(tag or "").lower()
    if tag_catalog.get_tag_axis(name) in _SCENE_AXES:
        return True
    if any(name in s for s in _SCENE_SETS):
        return True
    # `blue_background` and friends are not in ABSTRACT_BG and have no axis, yet
    # they are exactly what turns a character board into a plain studio shot.
    return name.endswith("_background")


def belongs_to_track(tag: str, track: str) -> bool:
    """False when this tag is the other track's job."""
    if track == "background":
        return not is_person_tag(tag)
    return not is_scene_tag(tag)


# Sections that describe what is happening rather than what is worn. Repeated
# in the person query because the character section is wardrobe-only once a
# character is locked, and a query of pure clothing words retrieves pure
# clothing — one run came back as kimono, maid_headdress, chinese_clothes and
# rendered a costume chart instead of a girl in a library.
_QUERY_EMPHASIS: dict[str, tuple[str, ...]] = {
    "person": ("action",),
    "background": ("background",),
}


