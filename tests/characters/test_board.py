"""The reference board must render the character, not a scene."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.characters.board import compile_board_slot
from app.characters.presets import BOARD_SLOTS, load_seed_presets, preset_to_character

PRESET = load_seed_presets()[0]


@pytest.mark.parametrize("slot", BOARD_SLOTS)
def test_identity_tags_lead_the_prompt(slot):
    positive, _ = compile_board_slot(PRESET, slot)
    identity = preset_to_character(PRESET)["identity_tags"]
    tags = [t.strip() for t in positive.split(",")]
    assert identity, "the fixture preset should have identity tags"
    for tag in identity:
        assert tag in tags
    # Identity ahead of framing: the head of the prompt is where the model looks.
    last_identity = max(tags.index(t) for t in identity)
    assert last_identity < tags.index("looking_at_viewer")


@pytest.mark.parametrize("slot", BOARD_SLOTS)
def test_no_duplicate_tags(slot):
    positive, _ = compile_board_slot(PRESET, slot)
    tags = [t.strip() for t in positive.split(",") if t.strip()]
    assert len(tags) == len(set(tags))


def test_slots_differ_only_in_framing():
    sheet = set(t.strip() for t in compile_board_slot(PRESET, "sheet")[0].split(","))
    portrait = set(t.strip() for t in compile_board_slot(PRESET, "portrait")[0].split(","))
    assert "full_body" in sheet and "full_body" not in portrait
    assert "upper_body" in portrait and "upper_body" not in sheet
    identity = set(preset_to_character(PRESET)["identity_tags"])
    assert identity <= sheet and identity <= portrait


def test_negative_blocks_reference_sheet_layouts():
    _, negative = compile_board_slot(PRESET, "sheet")
    for banned in ("multiple_views", "reference_sheet", "collage"):
        assert banned in negative


def test_unknown_slot_is_rejected():
    with pytest.raises(ValueError):
        compile_board_slot(PRESET, "closeup")
