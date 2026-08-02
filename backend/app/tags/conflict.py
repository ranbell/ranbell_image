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
    # The ones a model reaches for when told to name a colour, and the ones a
    # character's palette is written in. Without them a prompt asked for an
    # `indigo_scarf`, a `blue_scarf` and a `plaid_scarf` at once, and for a
    # `dark_coat` over a `black_coat` — one garment, described until it became
    # three.
    "indigo", "navy", "dark", "light", "pale", "deep", "bright",
    "maroon", "burgundy", "olive", "khaki", "turquoise", "mint", "peach",
    "charcoal", "cream", "ivory", "bronze", "copper", "rose", "coral",
    "amber", "emerald", "sapphire", "ruby", "pastel", "neon", "monochrome",
})

_LENGTHS = frozenset({
    "long", "short", "medium", "very", "absurdly", "waist", "knee",
    "shoulder", "chin", "ear", "hip", "floor",
    # Where a boot or a sock stops. Three drafts of one character came back
    # with `knee_boots`, `ankle_boots` and `lace-up_boots`; only one pair is
    # on her feet.
    "ankle", "calf", "thigh", "mid", "over-the-knee", "crotch",
})

# What a garment is made of or patterned with. One scarf has one pattern, and
# `brown_scarf` beside `plaid_scarf` survived the colour test because `plaid`
# is not a colour — so the prompt asked for two scarves.
_PATTERNS = frozenset({
    "plaid", "striped", "vertical-striped", "horizontal-striped",
    "checkered", "polka_dot", "floral", "argyle", "houndstooth",
    "denim", "leather", "knit", "wool", "lace", "fur", "silk", "satin",
    "velvet", "corduroy", "tweed", "mesh", "sheer", "camouflage",
})

# Modifier families that occupy one slot. Two tags on the same head noun clash
# only when their modifiers come from the same family.
_FAMILIES: tuple[frozenset[str], ...] = (_COLOURS, _LENGTHS, _PATTERNS)

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


# A picture happens at one hour. These share no head noun — `night` and `dawn`
# have no word in common — so nothing above catches them, and a top-up step
# offered `night` to "strengthen the pre-dawn feeling" of a scene whose light
# was already `dawn`. The render obeyed the darker of the two.
_TIME_OF_DAY = frozenset({
    "dawn", "sunrise", "early_morning", "morning", "daybreak",
    "noon", "midday", "daytime", "day", "afternoon",
    "evening", "sunset", "dusk", "twilight", "golden_hour",
    "night", "midnight", "late_at_night", "nighttime",
})


# A picture happens in one room. Like the hours these share no head noun, so
# nothing caught `bathroom` arriving next to the `kitchen` a dishwashing theme
# had already named. Only rooms — `library` and `bookshelf` are the same place
# at two scales, and `kitchen` and `window` are not rivals at all.
_ROOMS = frozenset({
    "kitchen", "bathroom", "bedroom", "living_room", "dining_room",
    "classroom", "library", "office", "gymnasium", "hallway", "corridor",
    "basement", "attic", "garage", "laundry_room", "locker_room",
    "infirmary", "cafeteria", "restaurant", "cafe", "bar", "shop",
    "hospital", "church", "train_interior", "car_interior", "elevator",
})


def _room(tag: str) -> str | None:
    name = str(tag or "").strip().lower().replace(" ", "_")
    return name if name in _ROOMS else None


def _hour(tag: str) -> str | None:
    """The tag itself when it names an hour of the day, else None."""
    name = str(tag or "").strip().lower().replace(" ", "_")
    return name if name in _TIME_OF_DAY else None


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

    a_hour, b_hour = _hour(tag), _hour(other)
    if a_hour and b_hour and a_hour != b_hour:
        return True

    a_room, b_room = _room(tag), _room(other)
    if a_room and b_room and a_room != b_room:
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
