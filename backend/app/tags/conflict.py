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


# ── Slots that need no shared head noun ─────────────────────────────────────
# `from_above` and `from_below` reduce to the head nouns "above" and "below",
# so the modifier-family rule at the bottom of this file cannot see them at
# all. Like the hours and the rooms above, these are whole-tag membership sets:
# two members of one slot are two answers to a question that has one answer.

# One lens position. A shot is taken from above or from below, and the pair
# rode along together every time the Showrunner asked to move the camera: the
# angle they asked for arrived and the angle they were leaving stayed.
_CAMERA_PITCH = frozenset({
    "from_above", "from_below", "high_angle", "low_angle", "straight-on",
    "eye-level", "eye_level", "overhead_shot", "bird's-eye_view",
    "worm's-eye_view", "top-down_view",
})

# `_CAMERA_PITCH` partitioned into the three answers a pitch question actually
# has. Members of one family are synonyms meant to ride together in one write
# (`from_below, low_angle, looking_down` is the ordinary way to say one
# angle); members of different families are the same question answered two
# ways at once (`high_angle` and `eye-level` cannot both be the shot). Kept
# separate from `_ANGLE_FORBIDS_GAZE` below: that table only has entries for
# the two pitches with a gaze consequence, so `eye-level`/`straight-on` are
# absent from it and were reading as "no family" — and so as compatible with
# everything — when self-consistency needs them read as their own family.
_PITCH_FAMILIES: tuple[frozenset[str], ...] = (
    frozenset({"from_above", "high_angle", "overhead_shot", "bird's-eye_view", "top-down_view"}),
    frozenset({"from_below", "low_angle", "worm's-eye_view"}),
    frozenset({"straight-on", "eye-level", "eye_level"}),
)

# Which side the lens is on. Deliberately a separate slot from pitch — a low
# three-quarter is a real shot, and one slot holding both would evict half of
# every angle worth asking for.
_CAMERA_SIDE = frozenset({
    "from_front", "from_side", "from_behind", "profile", "three-quarter_view",
    "rear_view", "back_view", "front_view",
})

# How far away. `upper_body` and `full_body` do share a head noun, but "upper"
# and "full" are in none of the modifier families, so nothing caught them.
# Both spellings of the close-up are here because danbooru writes `close-up`
# and `identity._FRAMING_TAGS` writes `close_up`; a slot that knows only one
# of them leaves the panel's own framing tag unguarded.
_CAMERA_DISTANCE = frozenset({
    "extreme_close-up", "extreme_close_up", "close-up", "close_up",
    "face_focus", "portrait", "bust", "upper_body", "cowboy_shot",
    "half-body", "full_body", "wide_shot", "very_wide_shot", "long_shot",
    "extreme_long_shot",
})

# Where the eyes point on the vertical. This is the one the camera drags with
# it, and the reason this whole slot table exists — see _ANGLE_FORBIDS_GAZE.
_GAZE_PITCH = frozenset({"looking_up", "looking_down", "looking_ahead"})

# What the eyes are on. `looking_back` is deliberately NOT here: it is a head
# turn, not a target, and looking back at the camera is one of the most common
# real combinations there is. Evicting half of it would cost more than leaving
# both standing.
_GAZE_TARGET = frozenset({
    "looking_at_viewer", "looking_away", "looking_afar", "looking_to_the_side",
    "averting_eyes", "looking_at_another", "looking_elsewhere",
})

# What the whole body is doing. Short on purpose. `wariza`, `seiza`, `on_back`
# and friends are modifiers of a posture already in this list, so `sitting,
# wariza` has to survive; the job of this slot is to stop a stale `sitting`
# riding beside a fresh `standing`.
_POSTURE = frozenset({
    "standing", "sitting", "kneeling", "squatting", "lying", "crouching",
    "walking", "running", "jumping", "all_fours",
})

# Both arms at once. One-hand tags (`hand_on_own_hip`, `holding_*`) are not
# here — she has two hands, and the `long_hair`/`hair_ribbon` lesson at the top
# of this file says over-eviction costs more than under-eviction.
_ARMS = frozenset({
    "arms_up", "arms_at_sides", "arms_behind_back", "arms_behind_head",
    "crossed_arms", "spread_arms", "outstretched_arms", "arms_under_breasts",
})

# Only the aperture is exclusive. `smile` is not here: `smile` and `open_mouth`
# co-occur constantly and a slot holding both would delete the smile.
_MOUTH = frozenset({"open_mouth", "closed_mouth", "parted_lips"})
_EYES = frozenset({
    "closed_eyes", "half-closed_eyes", "wide-eyed", "narrowed_eyes",
})


# Slot name → its members. Callers outside this module name slots rather than
# re-listing tags: `muse.facets.FACET_OWNS` says the camera facet owns
# `camera_pitch` and `gaze_pitch`, which is how a tag written by the wrong
# facet gets dropped before it ever reaches the prompt.
SLOTS: dict[str, frozenset[str]] = {
    "time_of_day": _TIME_OF_DAY,
    "room": _ROOMS,
    "camera_pitch": _CAMERA_PITCH,
    "camera_side": _CAMERA_SIDE,
    "camera_distance": _CAMERA_DISTANCE,
    "gaze_pitch": _GAZE_PITCH,
    "gaze_target": _GAZE_TARGET,
    "posture": _POSTURE,
    "arms": _ARMS,
    "mouth": _MOUTH,
    "eyes": _EYES,
}


# The gaze a lens position makes impossible. She cannot look up at a camera
# that is already under her chin. This is not exclusion but implication, so it
# cannot be a slot: `from_above` does not fight `looking_up`, it *requires* it.
# The reported failure was a shot moved from a high angle to a low one where
# `looking_up` survived, because nothing in the codebase knew the two tags had
# anything to do with each other.
_ANGLE_FORBIDS_GAZE: dict[str, frozenset[str]] = {
    "from_above": frozenset({"looking_down"}),
    "high_angle": frozenset({"looking_down"}),
    "overhead_shot": frozenset({"looking_down"}),
    "bird's-eye_view": frozenset({"looking_down"}),
    "top-down_view": frozenset({"looking_down"}),
    "from_below": frozenset({"looking_up"}),
    "low_angle": frozenset({"looking_up"}),
    "worm's-eye_view": frozenset({"looking_up"}),
}

# What this file cannot answer: `from_behind` rules out `looking_at_viewer`
# only when `looking_back` is absent, and `contradicts(a, b)` is pairwise — it
# never sees a third tag. That check belongs where the whole camera facet is
# visible at once (`muse.facets.write`), not here.


def _norm_tag(tag: str) -> str:
    return str(tag or "").strip().lower().replace(" ", "_")


def _slot(tag: str) -> str | None:
    """The slot this tag fills, when it fills one on its own."""
    name = _norm_tag(tag)
    if not name:
        return None
    for slot, members in SLOTS.items():
        if name in members:
            return slot
    return None


def slot_of(tag: str) -> str | None:
    """Public form of `_slot`, for callers deciding who owns a tag."""
    return _slot(tag)


def pitch_family(tag: str) -> frozenset[str] | None:
    """Which of the three pitch answers (high / low / level) this tag is, or
    None when the tag is not a `_CAMERA_PITCH` member at all.

    Two pitch tags found together are synonyms of one angle when this comes
    back equal for both (`from_above` and `high_angle` are both the "high"
    family) and directly opposed when it does not (`high_angle` vs
    `low_angle`, or either vs `eye-level`). Same-family synonyms are meant to
    coexist in one write, the way `from_below, low_angle, looking_down` do
    together.
    """
    name = _norm_tag(tag)
    for family in _PITCH_FAMILIES:
        if name in family:
            return family
    return None


def pitch_forbidden_gaze(tag: str) -> frozenset[str]:
    """The `gaze_pitch` values this camera-pitch tag rules out (empty when it
    rules out none). Public form of `_ANGLE_FORBIDS_GAZE`, for a caller that
    needs to apply the rule inside one write rather than between two."""
    return _ANGLE_FORBIDS_GAZE.get(_norm_tag(tag), frozenset())


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

    # One slot, two different members. This covers the hours and the rooms it
    # always covered, and now the camera and the body as well.
    a_name, b_name = _norm_tag(tag), _norm_tag(other)
    a_slot, b_slot = _slot(a_name), _slot(b_name)
    if a_slot and a_slot == b_slot and a_name != b_name:
        return True

    # An angle and the gaze it rules out. Checked both ways round so the
    # contradiction is symmetric whichever tag is being offered.
    if b_name in _ANGLE_FORBIDS_GAZE.get(a_name, frozenset()):
        return True
    if a_name in _ANGLE_FORBIDS_GAZE.get(b_name, frozenset()):
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
