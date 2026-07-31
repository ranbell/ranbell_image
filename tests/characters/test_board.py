"""The reference board must render the character, not a mannequin.

The sheet is a composite — a centre figure with four polaroid vignettes — and
its shape is load-bearing: labelled lines rather than a flat tag list, and
`multiple_views` in the positive. Written as a plain `full_body, standing` tag
list it comes back as a shop-window pose indistinguishable from the portrait,
which is exactly what happened before this format was restored.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.characters.board import (
    SLOT_SIZE,
    compile_board_slot,
    sheet_vignettes,
)
from app.characters.presets import BOARD_SLOTS, load_seed_presets, preset_to_character

PRESET = load_seed_presets()[0]
CHARACTER = preset_to_character(PRESET)


def _sheet():
    return compile_board_slot(PRESET, "sheet")


def _portrait():
    return compile_board_slot(PRESET, "portrait")


# ── the sheet is a composite ────────────────────────────────────────────────
def test_sheet_uses_the_labelled_layout():
    positive, _ = _sheet()
    for label in ("Character:", "Accessories:", "** Chronicles of Character **",
                  "Center/Main :", "Shot:", "Effect:"):
        assert label in positive


def test_sheet_asks_for_four_vignettes():
    positive, _ = _sheet()
    bullets = [ln for ln in positive.splitlines() if ln.startswith(" - ")]
    assert len(bullets) == 4
    assert "polaroid frame" in positive


def test_sheet_puts_multiple_views_in_the_positive():
    """Every other prompt here bans it. The sheet is the one that needs it."""
    positive, negative = _sheet()
    assert "multiple_views" in positive
    assert "multiple_views" not in negative


def test_sheet_pins_hair_and_eye_colour_across_the_frames():
    positive, _ = _sheet()
    assert "same hair and eye color" in positive


def test_sheet_carries_the_whole_character():
    positive, _ = _sheet()
    for tag in CHARACTER["identity_tags"]:
        assert tag in positive
    for tag in CHARACTER["outfit_tags"]:
        assert tag in positive


def test_sheet_centre_holds_the_signature_prop():
    positive, _ = _sheet()
    sig = CHARACTER["signature_prop"]
    centre = next(ln for ln in positive.splitlines() if ln.startswith("Center/Main"))
    if sig:
        assert f"holding {sig}" in centre
    assert "dynamic posture" in centre


def test_vignettes_are_four_distinct_lives():
    vignettes = sheet_vignettes(CHARACTER)
    assert len(vignettes) == 4
    assert len(set(vignettes)) == 4, "a repeated slice wastes one of four frames"


def test_vignettes_prefer_the_character_over_the_fallback():
    made_up = {
        **CHARACTER,
        "gesture_vocab": ["painting", "swimming"],
        "personality": {**CHARACTER["personality"], "likes": ["warm parfait in winter"]},
    }
    vignettes = sheet_vignettes(made_up)
    assert "painting" in vignettes[0]
    assert "swimming" in vignettes[1], "the active slice must differ from the hobby one"
    assert "parfait" in vignettes[2]


# ── the portrait is a face ──────────────────────────────────────────────────
def test_portrait_is_a_close_up():
    positive, _ = _portrait()
    tags = [t.strip() for t in positive.split(",")]
    assert "close-up" in tags
    assert "detailed_face" in tags
    assert "full_body" not in tags


def test_portrait_drops_the_wardrobe_that_argues_for_a_full_figure():
    """`long_skirt` and `loafers` are each a vote for showing the legs."""
    positive, _ = _portrait()
    for tag in CHARACTER["outfit_tags"]:
        assert tag not in positive


def test_portrait_keeps_worn_head_accessories():
    positive, _ = _portrait()
    head = [t for t in CHARACTER["prop_tags"] if "glasses" in t or "hair" in t]
    for tag in head:
        assert tag in positive


def test_portrait_negative_blocks_a_second_full_body_render():
    _, negative = _portrait()
    for banned in ("full_body", "multiple_views", "reference_sheet", "wide_shot"):
        assert banned in negative


def test_portrait_keeps_the_identity():
    positive, _ = _portrait()
    for tag in CHARACTER["identity_tags"]:
        assert tag in positive


def test_portrait_has_no_duplicates():
    positive, _ = _portrait()
    tags = [t.strip() for t in positive.split(",") if t.strip()]
    assert len(tags) == len(set(tags))


# ── plumbing ────────────────────────────────────────────────────────────────
def test_the_two_slots_are_genuinely_different_shots():
    sheet, _ = _sheet()
    portrait, _ = _portrait()
    assert sheet != portrait
    assert "Chronicles of Character" not in portrait


def test_each_slot_has_its_own_canvas():
    assert SLOT_SIZE["portrait"] == (512, 512), "a square frame has nowhere for legs"
    assert SLOT_SIZE["sheet"][1] > SLOT_SIZE["sheet"][0], "five frames need height"
    assert set(SLOT_SIZE) == set(BOARD_SLOTS)


def test_unknown_slot_is_rejected():
    with pytest.raises(ValueError):
        compile_board_slot(PRESET, "closeup")
