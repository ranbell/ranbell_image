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
    presets = json.loads(ASSET.read_text(encoding="utf-8"))
    return preset_to_character(
        next(p for p in presets if p["id"] == "stargazer_girl")
    )


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
    for label in ("favorite:", "hate :", "favorite color:", "favorite accesory:", "inner:"):
        assert label not in text


def test_stage_b_gets_tags_and_later_stages_get_the_previous_prompt():
    # Stage A's prose is deliberately not carried past the draft: WD14 replaces
    # it, because the tags describe what was drawn rather than what was asked for.
    assert brief.with_tags("BRIEF", "1girl, sky") == "BRIEF,1girl, sky"
    assert brief.with_tags("BRIEF", "  ") == "BRIEF"
    assert brief.with_prompt("BRIEF", "a prompt") == "BRIEF,a prompt"
    assert brief.with_prompt("BRIEF", "") == "BRIEF"
