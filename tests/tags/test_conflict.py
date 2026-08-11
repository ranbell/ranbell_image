"""Slot-based contradiction: same feature, incompatible value.

The looser token-overlap rule in `prompt.tag_merge` throws out `long_hair` and
`hair_ribbon` when asked to protect `purple_hair`. A character can have long
purple hair and wear a hair ribbon, so eviction needs a sharper question than
"do these share a word".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import identity
from app.tags.conflict import SLOTS, contradicts, contradicts_any, slot_of


@pytest.mark.parametrize("a,b", [
    ("blue_eyes", "brown_eyes"),
    ("blue_hair", "black_hair"),
    ("dark_blue_hair", "black_hair"),
    ("silver_hair", "pink_hair"),
    ("long_hair", "short_hair"),
])
def test_same_slot_different_value_contradicts(a, b):
    assert contradicts(a, b)
    assert contradicts(b, a), "contradiction is symmetric"


@pytest.mark.parametrize("a,b", [
    ("long_hair", "purple_hair"),        # length and colour are different slots
    ("very_long_hair", "black_hair"),
    ("hair_ribbon", "purple_hair"),      # different head noun entirely
    ("closed_eyes", "green_eyes"),       # state and colour
    ("blue_skirt", "black_hair"),
    ("glowing_eyes", "blue_eyes"),       # an effect, not a colour
])
def test_compatible_tags_do_not_contradict(a, b):
    assert not contradicts(a, b)
    assert not contradicts(b, a)


def test_a_tag_never_contradicts_itself():
    assert not contradicts("blue_eyes", "blue_eyes")


def test_bare_nouns_do_not_contradict():
    """`hair` alone says nothing exclusive about `black_hair`."""
    assert not contradicts("hair", "black_hair")


def test_blank_input_is_safe():
    assert not contradicts("", "black_hair")
    assert not contradicts("black_hair", "")


def test_contradicts_any_scans_the_locked_set():
    locked = ["1girl", "black_hair", "brown_eyes"]
    assert contradicts_any("blue_eyes", locked)
    assert not contradicts_any("cardigan", locked)
    assert not contradicts_any("long_hair", locked)


def test_matching_ignores_case_and_spacing():
    assert contradicts("Blue Eyes", "brown_eyes")


@pytest.mark.parametrize("a,b", [
    ("male_swimwear", "1girl"),
    ("1boy", "1girl"),
    ("mature_male", "adult_female"),
    ("male_focus", "multiple_girls"),
])
def test_subject_gender_contradicts_without_a_shared_noun(a, b):
    """`male_swimwear` and `1girl` share no word, and the board rendered
    trunks over a bikini top."""
    assert contradicts(a, b)
    assert contradicts(b, a)


@pytest.mark.parametrize("a,b", [
    ("swimsuit", "1girl"),
    ("male_swimwear", "1boy"),
    ("female_pervert", "1girl"),
])
def test_agreeing_or_neutral_tags_do_not_contradict(a, b):
    assert not contradicts(a, b)


@pytest.mark.parametrize("a,b", [
    ("night", "dawn"),
    ("dawn", "sunset"),
    ("morning", "midnight"),
    ("evening", "daytime"),
])
def test_a_picture_happens_at_one_hour(a, b):
    """The top-up step offered `night` to strengthen the "pre-dawn feeling" of
    a scene already lit by `dawn`, and the render obeyed the darker of the
    two. These share no head noun, so nothing else catches them."""
    assert contradicts(a, b)
    assert contradicts(b, a)


@pytest.mark.parametrize("a,b", [
    ("dawn", "warm_glow"),
    ("morning", "sunlight"),
    ("night", "neon_sign"),
    ("evening", "1girl"),
])
def test_an_hour_does_not_fight_what_merely_suits_it(a, b):
    assert not contradicts(a, b)


@pytest.mark.parametrize("a,b", [
    ("plaid_scarf", "striped_scarf"),
    ("denim_jacket", "leather_jacket"),
    ("knee_boots", "ankle_boots"),
    ("thigh_boots", "knee_boots"),
])
def test_one_garment_has_one_pattern_and_one_length(a, b):
    """Three drafts of one character came back with `knee_boots`,
    `ankle_boots` and `lace-up_boots`; only one pair is on her feet."""
    assert contradicts(a, b)
    assert contradicts(b, a)


@pytest.mark.parametrize("a,b", [
    ("brown_scarf", "plaid_scarf"),
    ("wool_coat", "blue_coat"),
    ("blue_coat", "open_coat"),
])
def test_a_colour_and_a_pattern_can_describe_one_garment(a, b):
    """A brown plaid scarf is a scarf, not two scarves."""
    assert not contradicts(a, b)


@pytest.mark.parametrize("a,b", [
    ("indigo_scarf", "blue_scarf"),
    ("dark_coat", "black_coat"),
    ("navy_skirt", "blue_skirt"),
])
def test_the_colours_a_model_actually_reaches_for(a, b):
    """A character's palette is written in words like indigo and navy, and the
    family had none of them — so one prompt asked for an `indigo_scarf`, a
    `blue_scarf` and a `plaid_scarf` at once, and a `dark_coat` over a
    `black_coat`."""
    assert contradicts(a, b)


@pytest.mark.parametrize("a,b", [
    ("kitchen", "bathroom"),
    ("classroom", "gymnasium"),
    ("bedroom", "office"),
])
def test_a_picture_happens_in_one_room(a, b):
    """A dishwashing theme had already named the kitchen and retrieval added
    `bathroom`. Like the hours, these share no head noun."""
    assert contradicts(a, b)
    assert contradicts(b, a)


@pytest.mark.parametrize("a,b", [
    ("library", "bookshelf"),
    ("kitchen", "window"),
    ("kitchen", "sink"),
])
def test_a_room_and_what_is_in_it_are_not_rivals(a, b):
    assert not contradicts(a, b)


@pytest.mark.parametrize("a,b", [
    ("from_above", "from_below"),
    ("high_angle", "low_angle"),
    ("from_above", "low_angle"),
    ("close-up", "full_body"),
    ("upper_body", "full_body"),        # shares a head noun, but no family
    ("from_front", "from_behind"),
])
def test_a_shot_is_taken_from_one_position(a, b):
    """The angle the Showrunner asked for arrived and the angle they were
    leaving stayed. These reduce to the head nouns "above" and "below", so the
    modifier-family rule never saw them."""
    assert contradicts(a, b)
    assert contradicts(b, a)


@pytest.mark.parametrize("a,b", [
    ("from_above", "from_side"),        # pitch and side are different slots
    ("from_below", "close-up"),         # pitch and distance
    ("from_above", "classroom"),
])
def test_the_axes_of_a_camera_do_not_fight_each_other(a, b):
    """A low three-quarter close-up is one real shot, not three."""
    assert not contradicts(a, b)
    assert not contradicts(b, a)


@pytest.mark.parametrize("angle,gaze", [
    ("from_below", "looking_up"),
    ("low_angle", "looking_up"),
    ("from_above", "looking_down"),
    ("high_angle", "looking_down"),
    ("overhead_shot", "looking_down"),
])
def test_a_lens_position_rules_out_the_gaze_it_makes_impossible(angle, gaze):
    """The reported failure: a shot moved from a high angle to a low one and
    `looking_up` survived, because nothing knew the two tags were related."""
    assert contradicts(angle, gaze)
    assert contradicts(gaze, angle)


@pytest.mark.parametrize("angle,gaze", [
    ("from_above", "looking_up"),
    ("from_below", "looking_down"),
    ("high_angle", "looking_up"),
])
def test_an_angle_agrees_with_the_gaze_that_belongs_to_it(angle, gaze):
    """The check that catches an over-eager fix. Looking up at a camera above
    her is the whole point of shooting from above — an exclusion slot holding
    both would delete the thing that makes the angle read."""
    assert not contradicts(angle, gaze)
    assert not contradicts(gaze, angle)


@pytest.mark.parametrize("a,b", [
    ("standing", "sitting"),
    ("kneeling", "lying"),
    ("open_mouth", "closed_mouth"),
    ("closed_eyes", "wide-eyed"),
    ("crossed_arms", "arms_up"),
    ("looking_at_viewer", "looking_away"),
])
def test_one_body_does_one_thing_at_a_time(a, b):
    assert contradicts(a, b)
    assert contradicts(b, a)


@pytest.mark.parametrize("a,b", [
    ("sitting", "wariza"),              # a modifier of a posture, not a rival
    ("smile", "open_mouth"),            # these co-occur constantly
    ("looking_at_viewer", "looking_back"),   # she turned her head; both true
    ("standing", "crossed_arms"),       # posture and arms are separate slots
    ("sitting", "hand_on_own_hip"),     # one-hand tags are not the arms slot
])
def test_the_body_tags_that_belong_together_are_left_alone(a, b):
    """Over-eviction costs more than under-eviction — the `long_hair` /
    `hair_ribbon` lesson at the top of the module, applied to the body."""
    assert not contradicts(a, b)
    assert not contradicts(b, a)


def test_the_slots_are_disjoint():
    """A tag in two slots would answer to whichever the dict iterated first,
    and the owning facet would change with an unrelated edit."""
    seen: dict[str, str] = {}
    for slot, members in SLOTS.items():
        for tag in members:
            assert tag not in seen, f"{tag} is in both {seen.get(tag)} and {slot}"
            seen[tag] = slot


def test_every_framing_tag_the_panel_can_emit_belongs_to_a_camera_slot():
    """`identity._FRAMING_TAGS` and this module name the same crops in two
    places. If they drift, the panel's framing dropdown writes a tag no slot
    guards, and the old crop rides along beside the new one."""
    for tags in identity._FRAMING_TAGS.values():
        for tag in tags:
            assert slot_of(tag) in ("camera_distance", "camera_side"), tag
