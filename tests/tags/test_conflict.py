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

from app.tags.conflict import contradicts, contradicts_any


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
