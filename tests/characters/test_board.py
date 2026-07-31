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
def test_portrait_is_a_bust_shot_not_a_face_crop():
    """A tight `close-up` cropped above the collarbone and the cardigan never
    made it into frame — the render came back bare-shouldered."""
    positive, negative = _portrait()
    tags = [t.strip() for t in positive.split(",")]
    assert "upper_body" in tags
    assert "detailed_face" in tags
    assert "full_body" not in tags
    assert "close-up" not in tags
    assert "extreme_close-up" in negative and "bare_shoulders" in negative


def test_portrait_drops_only_the_wardrobe_that_shows_the_legs():
    """`long_skirt` and `loafers` are each a vote for showing the legs. Her top
    half still needs clothes — dropping the wardrobe wholesale came back
    bare-shouldered."""
    positive, _ = _portrait()
    lower = [t for t in CHARACTER["outfit_tags"]
             if any(h in t for h in ("skirt", "shoes", "loafers", "socks", "pantyhose"))]
    upper = [t for t in CHARACTER["outfit_tags"] if t not in lower]
    assert lower, "the fixture character should own something below the waist"
    for tag in lower:
        assert tag not in positive
    for tag in upper:
        assert tag in positive


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


# ── the LLM plan ────────────────────────────────────────────────────────────
class _PlanLLM:
    """Returns a canned plan and records the prompt it was handed."""

    def __init__(self, payload):
        self.payload = payload
        self.prompt = ""

    async def generate_text(self, prompt, model=None, options=None, fmt=None):
        import json
        self.prompt = prompt
        return json.dumps(self.payload) if not isinstance(self.payload, str) else self.payload


GOOD_PLAN = {
    "center": "standing straight, calm expression, holding book_cart",
    "vignettes": ["walking down a street, trench coat",
                  "reading near window, cardigan",
                  "carrying cart up stairs, tired expression",
                  "sitting in corner, pajamas, blanket"],
}


def _plan(payload):
    import asyncio

    from app.characters.board import plan_sheet
    return asyncio.run(plan_sheet(PRESET, _PlanLLM(payload)))


def test_a_good_plan_is_accepted():
    plan = _plan(GOOD_PLAN)
    assert plan["center"] == GOOD_PLAN["center"]
    assert len(plan["vignettes"]) == 4


def test_the_plan_replaces_the_fixed_slots_in_the_sheet():
    positive, _ = compile_board_slot(PRESET, "sheet", GOOD_PLAN)
    assert "sitting in corner, pajamas, blanket" in positive
    assert "eating, crepe" not in positive, "the fixed food slot must be gone"
    centre = next(ln for ln in positive.splitlines() if ln.startswith("Center/Main"))
    assert "calm expression" in centre


def test_no_plan_still_renders_the_fixed_slots():
    """A board that renders something beats a board that renders nothing."""
    positive, _ = compile_board_slot(PRESET, "sheet", None)
    bullets = [ln[3:] for ln in positive.splitlines() if ln.startswith(" - ")]
    assert bullets == sheet_vignettes(CHARACTER)
    assert "dynamic posture" in positive, "the fixed centre is still used"


def test_a_plan_that_repeats_itself_is_refused():
    """Four identical frames are worse than the fixed slots, which at least vary."""
    assert _plan({**GOOD_PLAN, "vignettes": ["reading, cardigan"] * 4}) is None


def test_a_short_plan_is_refused():
    assert _plan({**GOOD_PLAN, "vignettes": GOOD_PLAN["vignettes"][:3]}) is None


def test_a_plan_with_no_centre_is_refused():
    assert _plan({**GOOD_PLAN, "center": ""}) is None


def test_unparseable_output_falls_back():
    assert _plan("not json") is None


def test_the_plan_prompt_carries_the_personality_and_bans_appearance():
    import asyncio

    from app.characters.board import plan_sheet
    llm = _PlanLLM(GOOD_PLAN)
    asyncio.run(plan_sheet(PRESET, llm))
    assert CHARACTER["personality"]["summary"][:20] in llm.prompt
    assert "Never mention hair colour" in llm.prompt
    assert CHARACTER["signature_prop"] in llm.prompt


def test_plan_lines_are_tidied():
    plan = _plan({**GOOD_PLAN, "center": '  "- standing,  smile ,"  '})
    assert plan["center"] == "standing, smile"
