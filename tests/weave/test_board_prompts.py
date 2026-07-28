from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.weave.character.presets import load_seed_presets, preset_to_character
from app.weave.render.prompts import compile_board_slot
from app.weave.schema import new_session_payload


def _preset_session(preset_id: str):
    session = new_session_payload()
    preset = next(p for p in load_seed_presets() if p["id"] == preset_id)
    session["character"].update(preset_to_character(preset))
    return session


def test_mood_slot_is_one_multi_view_sheet():
    """The mood board is a character sheet, not a fifth single portrait."""
    out = compile_board_slot(_preset_session("shy_bookworm"), "mood")
    text = out["positive"]

    assert "** Chronicles of Character **" in text
    assert "multiple_views" in text
    assert "polaroid frame" in text
    assert text.startswith("Character: 1girl, black_hair")
    assert "Accessories: book" in text

    vignettes = [line for line in text.splitlines() if line.startswith(" - ")]
    assert len(vignettes) == 4
    assert len(set(vignettes)) == 4
    # Drawn from her own repertoire, not four generic stock frames.
    assert "holding_book" in vignettes[0]
    assert "cardigan" in vignettes[0]


def test_mood_sheet_varies_the_four_frames_for_every_preset():
    for preset in load_seed_presets():
        session = new_session_payload()
        session["character"].update(preset_to_character(preset))
        lines = [
            line for line in compile_board_slot(session, "mood")["positive"].splitlines()
            if line.startswith(" - ")
        ]
        assert len(lines) == 4, preset["id"]
        assert len(set(lines)) == 4, f"{preset['id']} repeats a frame: {lines}"


def test_mood_sheet_does_not_repeat_the_hobby_as_the_active_frame():
    # Her hobby already is running; the active frame must show something else.
    out = compile_board_slot(_preset_session("morning_runner"), "mood")
    vignettes = [line for line in out["positive"].splitlines() if line.startswith(" - ")]
    assert vignettes[0].startswith(" - running")
    assert not vignettes[1].startswith(" - running")


def test_mood_sheet_takes_the_food_word_not_the_sentence():
    out = compile_board_slot(_preset_session("shy_bookworm"), "mood")
    # "tea gone cold" is prose; only the food noun belongs in a prompt.
    assert " - eating, tea" in out["positive"]
    assert "gone cold" not in out["positive"]


def test_board_portrait_close_full_long():
    session = new_session_payload()
    session["character"]["identity_tags"] = ["1girl", "brown_hair"]
    # Board shows her in her own clothes; the story's per-topic wardrobe does not
    # reach the reference sheet.
    session["character"]["outfit_tags"] = ["cardigan"]
    session["character"]["prop_tags"] = ["cloth_bookmark"]
    session["character"]["signature_prop"] = "cloth_bookmark"
    portrait = compile_board_slot(session, "portrait")
    full = compile_board_slot(session, "full")
    prop = compile_board_slot(session, "prop")
    assert "brown_hair" in portrait["positive"]
    assert "cardigan" in portrait["positive"]
    assert "close-up" in portrait["positive"] or "close_up" in portrait["positive"]
    assert "long_shot" in full["positive"]
    assert "full_body" in full["positive"]
    assert "cloth_bookmark" in prop["positive"]
    # prop should not dominate the full-body identity lock frame
    assert "holding" not in full["positive"]
