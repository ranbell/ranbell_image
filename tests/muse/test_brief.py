"""The brief is the only thing holding identity together, so it is tested hard.

Muse dropped protected tags, conflict eviction and attribute-slot comparison in
favour of re-sending this block on every call. That trade is only sound if the
block actually contains the identity and actually fences the reference material
off — hence these.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.characters.presets import preset_to_character
from app.muse import brief

ASSET = (Path(__file__).parent.parent.parent / "backend" / "app" / "characters"
         / "assets" / "personality_presets.json")


@pytest.fixture(scope="module")
def stargazer() -> dict:
    """Any shipped character will do — the brief's shape is what is under test,
    and pinning one id meant the roster could not be rewritten without this
    file failing for reasons that had nothing to do with the brief."""
    presets = json.loads(ASSET.read_text(encoding="utf-8"))
    return preset_to_character(presets[0])


def test_identity_tags_are_all_present(stargazer):
    text = brief.build(stargazer, "on a hill at midnight", "Cute 2D Anime Style")
    for tag in stargazer["identity_tags"]:
        assert tag in text, f"{tag} would have to be re-derived by the model"


def test_reference_material_is_fenced(stargazer):
    text = brief.build(stargazer, "on a hill at midnight", "Cute 2D Anime Style")
    open_at = text.index(brief.REFERENCE_OPEN)
    close_at = text.index(brief.REFERENCE_CLOSE)
    assert open_at < close_at

    fenced = text[open_at:close_at]
    # Likes and dislikes are what contaminated prompts when they were loose:
    # `thermos coffee` in a prompt is an object the picture must contain.
    for phrase in stargazer["personality"]["likes"] + stargazer["personality"]["dislikes"]:
        assert phrase in fenced


def test_theme_is_last_and_outside_the_fence(stargazer):
    theme = "on a hill at midnight, breath fogging"
    text = brief.build(stargazer, theme, "Cute 2D Anime Style")
    assert text.rstrip().endswith(theme)
    assert text.index(brief.REFERENCE_CLOSE) < text.index(theme)


def test_style_leads(stargazer):
    text = brief.build(stargazer, "anywhere", "photorealistic")
    assert text.startswith("Style: photorealistic")


def test_empty_optional_fields_leave_no_dangling_labels():
    bare = {"identity_tags": ["1girl"], "personality": {}, "palette": [],
            "signature_prop": ""}
    text = brief.build(bare, "a theme", "anime")
    for label in (
        "taste cues (never props) — likes:",
        "taste cues (never props) — dislikes:",
        "favorite color:",
        "signature accessory (only if the theme names it):",
        "inner:",
    ):
        assert label not in text


def test_framing_is_in_the_brief_head():
    bare = {"identity_tags": ["1girl"], "personality": {}, "palette": [],
            "signature_prop": ""}
    text = brief.build(bare, "a theme", "anime", framing="face_closeup")
    assert "Framing: face_closeup" in text


def test_stage_b_gets_tags_pose_intent_and_later_stages_get_the_previous_prompt():
    # Stage A's full prose is not carried past the draft: a short pose intent
    # keeps the action, and WD14 describes what was drawn.
    assert brief.with_tags("BRIEF", "1girl, sky") == "BRIEF,1girl, sky"
    assert brief.with_tags("BRIEF", "1girl", pose="she leans on the rail") == (
        "BRIEF\n\nPose intent: she leans on the rail,1girl"
    )
    assert brief.with_tags("BRIEF", "  ") == "BRIEF"
    assert brief.with_prompt("BRIEF", "a prompt") == "BRIEF,a prompt"
    assert brief.with_prompt("BRIEF", "") == "BRIEF"


def test_the_plan_is_stated_before_the_reference_and_the_theme_still_closes(stargazer):
    plan = {
        "place": "a stairwell landing",
        "hour": "dawn",
        "light": "even daylight from one window, mid-key, normal exposure",
        "action": "she has stopped halfway up",
        "must_appear": ["railing", "step", "bulb"],
    }
    theme = "she stops on the way up"
    text = brief.build(stargazer, theme, "anime", plan=plan)

    assert "PLACE: a stairwell landing" in text
    assert "MUST APPEAR: railing, step, bulb" in text
    # Style still leads, reference still fenced, theme still closes.
    assert text.startswith("Style: anime")
    assert text.index("PLACE:") < text.index(brief.REFERENCE_OPEN)
    assert text.index(brief.REFERENCE_CLOSE) < text.index(theme)
    assert text.rstrip().endswith(theme)


def test_showrunner_orders_survive_every_later_call(stargazer):
    """A note used to live only in the turn that answered it, so the original
    theme outvoted it on every call after that."""
    text = brief.build(
        stargazer, "a theme", "anime",
        notes=["make it somewhere indoors", "she should be sitting"],
    )
    assert "make it somewhere indoors" in text
    assert "she should be sitting" in text
    assert text.index("make it somewhere indoors") < text.index(brief.REFERENCE_OPEN)


def test_a_plan_with_no_fields_adds_no_dangling_header(stargazer):
    text = brief.build(stargazer, "a theme", "anime", plan={}, notes=[])
    assert brief.PLAN_HEADER not in text
    assert brief.ORDERS_HEADER not in text


def test_only_the_acting_seats_are_handed_her_inner_life(stargazer):
    """Lighting and colour do not act, and her inner life was the most evocative
    text in their context — so it became the language of the whole script."""
    full = brief.build(stargazer, "a theme", "anime", reference="full")
    digest = brief.build(stargazer, "a theme", "anime", reference="digest")

    for phrase in stargazer["personality"]["likes"]:
        assert phrase in full
        assert phrase not in digest
    for phrase in stargazer["personality"]["inner"]:
        assert phrase in full
        assert phrase not in digest

    # Traits survive: how she carries herself is craft everyone can use.
    for trait in stargazer["personality"]["traits"]:
        assert trait in digest
    # And the fence is still a fence.
    assert digest.index(brief.REFERENCE_OPEN) < digest.index(brief.REFERENCE_CLOSE)
    # Identity and theme are untouched by the cut.
    for tag in stargazer["identity_tags"]:
        assert tag in digest
    assert digest.rstrip().endswith("a theme")
