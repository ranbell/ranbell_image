"""Each track must stop doing the other track's job.

A background query for "library, rain, stained glass" returns `closed_eyes` and
`kimono` too, because people get photographed in libraries — and the background
board then rendered a cat-girl. A person query returns `simple_background` and
`blue_background`, and the character board came out on a plain studio backdrop.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.expand import (
    TRACK_AWAY_FROM,
    TRACK_SECTIONS,
    belongs_to_track,
    conflicts_with_identity,
    is_person_tag,
    is_scene_tag,
    split_tag_text,
)

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
    assert conflicts_with_identity(tag, IDENTITY)


@pytest.mark.parametrize("tag", ["pleated_skirt", "blouse", "ribbon", "umbrella", "socks"])
def test_wardrobe_is_not_a_conflict(tag):
    """The split is supposed to dress her for the theme — that must survive."""
    assert not conflicts_with_identity(tag, IDENTITY)


def test_no_identity_means_no_rejection():
    assert not conflicts_with_identity("blue_hair", [])


# ── plumbing ────────────────────────────────────────────────────────────────
def test_tracks_own_disjoint_sections():
    person, background = set(TRACK_SECTIONS["person"]), set(TRACK_SECTIONS["background"])
    assert not person & background


def test_background_has_a_negative_prompt_naming_people():
    negative = TRACK_AWAY_FROM["background_negative"]
    assert "1girl" in negative and "solo" in negative


def test_split_tag_text_normalizes_spacing():
    assert split_tag_text("long hair, blue eyes ,  ") == ["long_hair", "blue_eyes"]
