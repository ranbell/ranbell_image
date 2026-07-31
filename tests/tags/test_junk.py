"""Tags that must never reach a prompt.

Every case here was observed in a real run: `no_humans` and `1girl` reached the
same final prompt, `no_eyes` came out of a theme split describing a character
who has eyes, and `black_border` / `fisheye` put the final image inside an oval
lens mask.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.tags.junk import is_junk_tag, strip_junk


@pytest.mark.parametrize("tag", ["no_humans", "no_eyes", "no_lineart", "no_shoes"])
def test_negated_tags_are_junk(tag):
    assert is_junk_tag(tag)


@pytest.mark.parametrize("tag", ["nose_blush", "noodles", "notebook", "north"])
def test_words_that_merely_start_with_no_are_kept(tag):
    """The guard is on the underscore, not the two letters."""
    assert not is_junk_tag(tag)


@pytest.mark.parametrize(
    "tag", ["border", "black_border", "letterboxed", "fisheye", "isometric",
            "reference_sheet", "multiple_views", "watermark"],
)
def test_frame_artifacts_are_junk(tag):
    assert is_junk_tag(tag)


@pytest.mark.parametrize("tag", ["general", "sensitive"])
def test_meaningless_rating_tags_are_junk(tag):
    """`general` says nothing about the picture. `explicit` does, so it is not here."""
    assert is_junk_tag(tag)


@pytest.mark.parametrize("tag", ["explicit", "questionable"])
def test_content_rating_tags_stay_under_the_nsfw_switch(tag):
    assert not is_junk_tag(tag)


@pytest.mark.parametrize(
    "tag", ["1girl", "black_hair", "rooftop", "stained_glass", "umbrella", "rain"],
)
def test_ordinary_tags_survive(tag):
    assert not is_junk_tag(tag)


def test_blank_is_junk():
    assert is_junk_tag("")
    assert is_junk_tag("   ")


def test_matching_ignores_case_and_spacing():
    assert is_junk_tag("No Humans")
    assert is_junk_tag("BLACK BORDER")


def test_strip_junk_keeps_order():
    assert strip_junk(["1girl", "no_humans", "rooftop", "black_border", "rain"]) == [
        "1girl", "rooftop", "rain",
    ]
