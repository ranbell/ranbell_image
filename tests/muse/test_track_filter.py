"""Each track must stop doing the other track's job.

A model asked for thirty background tags will still slip `1girl` into the list,
and one asked for a character will still reach for `simple_background`. Both
render the wrong thing: a figure in a room that was meant to be empty, and a
character on a plain studio wall instead of in the scene.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.tracks import (
    BACKGROUND_NEGATIVE,
    belongs_to_track,
    is_person_tag,
    is_scene_tag,
)
from app.tags.conflict import contradicts_any

IDENTITY = ["1girl", "black_hair", "very_long_hair", "brown_eyes", "tall"]


# ── what leaked in the real run ─────────────────────────────────────────────
@pytest.mark.parametrize("tag", ["closed_eyes", "kimono", "blue_skirt", "kneeling", "chibi"])
def test_person_tags_are_kept_out_of_the_background(tag):
    assert not belongs_to_track(tag, "background")


@pytest.mark.parametrize("tag", ["simple_background", "blue_background", "library",
                                 "indoors", "window", "rain"])
def test_scene_tags_are_kept_out_of_the_person_track(tag):
    assert not belongs_to_track(tag, "person")


@pytest.mark.parametrize("tag", ["stained_glass", "umbrella", "curtains", "bookshelf"])
def test_props_and_scenery_details_survive_on_the_background(tag):
    assert belongs_to_track(tag, "background")


@pytest.mark.parametrize("tag", ["black_hair", "cardigan", "glasses", "smile", "1girl"])
def test_the_character_keeps_her_own_tags(tag):
    assert belongs_to_track(tag, "person")


def test_the_two_filters_are_not_the_same_filter():
    """A tag can belong to neither track's exclusion list — that is fine."""
    assert belongs_to_track("umbrella", "background")
    assert belongs_to_track("umbrella", "person")


def test_background_suffix_rule_catches_what_the_axis_map_misses():
    """`blue_background` has no axis and is not in ABSTRACT_BG."""
    assert is_scene_tag("blue_background")
    assert is_scene_tag("gradient_background")


def test_person_and_scene_classifiers_disagree_about_nothing_obvious():
    assert is_person_tag("closed_eyes") and not is_scene_tag("closed_eyes")
    assert is_scene_tag("library") and not is_person_tag("library")


# ── identity conflicts out of the split ─────────────────────────────────────
@pytest.mark.parametrize("tag", ["dark_blue_hair", "blue_hair", "blue_eyes", "short_hair"])
def test_appearance_that_contradicts_the_character_is_rejected(tag):
    assert contradicts_any(tag, IDENTITY)


@pytest.mark.parametrize("tag", ["pleated_skirt", "blouse", "ribbon", "umbrella", "socks"])
def test_wardrobe_is_not_a_conflict(tag):
    """The split is supposed to dress her for the theme — that must survive."""
    assert not contradicts_any(tag, IDENTITY)


def test_no_identity_means_no_rejection():
    assert not contradicts_any("blue_hair", [])


# ── plumbing ────────────────────────────────────────────────────────────────
def test_background_has_a_negative_prompt_naming_people():
    for word in ("1girl", "solo", "multiple_girls"):
        assert word in BACKGROUND_NEGATIVE
