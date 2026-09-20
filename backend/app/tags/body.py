"""Which body tags may be locked to a character, and which may never be.

A character's identity tags are stapled to the front of every positive prompt
and cannot be dropped downstream, so whatever lands here is in every picture she
is ever in. That makes the bucket worth guarding.

The rule that earned this module: a preset described a character as "tall,
straight-backed even at the end of a long day" and its ``tags.body`` said
``mature_female``. The prose was about posture; the tag was about age, and it
rendered a woman two decades older than the character reads. Thirteen of the
hundred presets carried it, nothing on the young side balanced it, and nothing
downstream could remove it.

So body tags are allowlisted, not denylisted. A tag reaches identity only if it
names a build the sampler should hold steady — chest, height, frame. Anything
that implies an age is refused outright, in both directions: a character's age
belongs in her written setting, where a person can read it, not in a tag that
silently ages or de-ages every render.
"""
from __future__ import annotations

# Mutually exclusive chest tags. One in identity makes every other a conflict.
BREAST_TAGS: tuple[str, ...] = (
    "flat_chest",
    "small_breasts",
    "medium_breasts",
    "large_breasts",
    "huge_breasts",
    "gigantic_breasts",
    "perky_breasts",
)

# Build slots. Members of one slot contradict each other; a draft's guess must
# not upgrade what the character sheet already fixed.
BODY_SLOTS: tuple[tuple[str, ...], ...] = (
    BREAST_TAGS,
    ("petite", "tall", "short"),
    ("slim", "slender", "skinny", "curvy", "plump", "fat", "muscular",
     "athletic", "toned", "abs"),
)

# Everything a preset's ``tags.body`` bucket is allowed to contribute.
ALLOWED_BODY_TAGS: frozenset[str] = frozenset(
    tag for slot in BODY_SLOTS for tag in slot
) | frozenset({"tan", "pale_skin", "dark_skin", "freckles"})

# Age is never a tag. Listed in both directions on purpose — "young" locks a
# character just as hard as "mature", and a preset that reads as a teenager
# does not need a tag to say so.
AGE_TAGS: frozenset[str] = frozenset({
    "mature_female", "mature_male", "milf", "dilf", "old", "old_woman",
    "old_man", "elderly", "aged_up", "age_difference", "middle_aged",
    "loli", "shota", "child", "toddler", "baby", "kindergarten",
    "teenage", "teenager", "teen", "young_adult", "adult", "adult_female",
    "adult_male", "onee-shota", "younger", "older",
})


# Builds that are never wanted, whoever is in frame. `petite` stays in
# BODY_SLOTS so it is still refused when a model reaches for it, but it renders
# a character markedly smaller than her sheet says and is never authored.
# The extreme chest tags are handled separately, in `opposing_negative`, which
# already pushes against them whenever any chest tag is locked.
UNWANTED_TAGS: frozenset[str] = frozenset({"petite"})

# Everything refused outright, in identity and in a model's answer alike.
REFUSED_TAGS: frozenset[str] = AGE_TAGS | UNWANTED_TAGS


def is_allowed_body_tag(tag: str) -> bool:
    """True when a tag may be locked into a character's identity."""
    t = str(tag or "").strip().lower().replace(" ", "_")
    return bool(t) and t not in REFUSED_TAGS and t in ALLOWED_BODY_TAGS


def filter_body_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """Split a body bucket into (kept, refused). Order preserved."""
    kept: list[str] = []
    refused: list[str] = []
    for raw in tags or []:
        (kept if is_allowed_body_tag(raw) else refused).append(raw)
    return kept, refused
