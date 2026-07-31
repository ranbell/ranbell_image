"""One framing, chosen on purpose.

Three board renders at three seeds produce three framings and the merge kept
whichever survived the budget — one prompt held `full_body`, `cowboy_shot` and
`close-up` at once. A model handed contradictory framing tags does not average
them; it picks one, and which one is not something the prompt decides.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse.camera import SHOTS, apply, is_framing_tag, negative_for

MIXED = ["1girl", "pool", "close-up", "full_body", "cowboy_shot", "palm_tree"]


def test_auto_changes_nothing():
    """No preference expressed means the drafts keep theirs."""
    tags, dropped = apply(MIXED, "auto")
    assert tags == MIXED
    assert dropped == []


def test_an_unknown_shot_is_treated_as_auto():
    assert apply(MIXED, "banana")[0] == MIXED


@pytest.mark.parametrize("shot", [s for s in SHOTS if s != "auto"])
def test_every_shot_states_itself_at_the_head(shot):
    tags, _ = apply(["1girl", "pool"], shot)
    assert tags[0] not in ("1girl", "pool"), f"{shot} must lead with its framing"


def test_choosing_wide_removes_the_close_framings():
    tags, dropped = apply(MIXED, "wide_shot")
    assert "close-up" not in tags and "cowboy_shot" not in tags
    assert set(dropped) == {"close-up", "cowboy_shot"}
    assert "wide_shot" in tags and "scenery" in tags


def test_choosing_close_removes_the_wide_framings():
    tags, dropped = apply(MIXED, "close_up")
    assert "full_body" not in tags and "cowboy_shot" not in tags
    assert "close-up" in tags and "detailed_face" in tags


def test_the_subject_and_the_scenery_survive_either_way():
    for shot in ("wide_shot", "close_up"):
        tags, _ = apply(MIXED, shot)
        assert "1girl" in tags and "pool" in tags


def test_a_framing_already_present_is_not_duplicated():
    tags, _ = apply(["wide_shot", "1girl"], "wide_shot")
    assert tags.count("wide_shot") == 1


def test_matching_ignores_hyphen_and_underscore_spelling():
    tags, dropped = apply(["close_up", "pool"], "wide_shot")
    assert "close_up" in dropped


def test_the_wide_shot_also_says_it_in_the_negative():
    """Checkpoints love pulling the subject forward until it is a portrait."""
    assert "close-up" in negative_for("wide_shot")
    assert negative_for("auto") == ""


@pytest.mark.parametrize("tag", ["close-up", "full_body", "upper_body", "wide_shot"])
def test_framing_tags_are_recognised(tag):
    assert is_framing_tag(tag)


@pytest.mark.parametrize("tag", ["1girl", "pool", "black_hair", "umbrella"])
def test_content_tags_are_not_framing(tag):
    assert not is_framing_tag(tag)
