"""The reference board must render the character, not a mannequin.

The sheet is a composite — a centre figure with four polaroid vignettes — and
its shape is load-bearing: labelled lines rather than a flat tag list, and
`multiple_views` in the positive. Written as a plain `full_body, standing` tag
list it comes back as a shop-window pose indistinguishable from the portrait,
which is exactly what happened before this format was restored.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.characters.board import (
    _ACTIVE_HINTS,
    _is_a_gaze,
    _shows_the_legs,
    centre_pose,
    plan_sheet,
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
    for label in ("Character:", "Accessories:", "** Character Sheet **",
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
    # Her own posture, not the house one. Every character used to be handed
    # "casual, leaning_forward, dynamic posture", so thirty sheets came back
    # with thirty people leaning at the viewer in different clothes.
    assert CHARACTER["gesture_vocab"][0] in centre
    assert "leaning_forward" not in centre or "leaning_forward" in CHARACTER["gesture_vocab"]


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
    # Roles in order — the same four the plan prompt asks a model for:
    # what she is known for, off duty, moving, eating.
    assert "painting" in vignettes[0]
    assert "swimming" in vignettes[2], "the moving slice takes her own sport"
    assert "sportswear" in vignettes[2], "and dresses for it, because it is one"
    assert "swimming" not in vignettes[1], "off duty must not swallow her only sport"
    assert "parfait" in vignettes[3]
    assert len(set(vignettes)) == 4


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
    # **本体と同じ判定を使う。** 手書きの語リストを持っていたら、みなもが
    # 制服（pleated_skirt）から大人の服（linen_trousers, work_boots）に
    # 変わった時点で、下半身の服を「上半身」と数えて落ちた
    lower = [t for t in CHARACTER["outfit_tags"] if _shows_the_legs(str(t))]
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
    assert "Character Sheet" not in portrait


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
    assert CHARACTER["gesture_vocab"][0] in positive, "the derived centre is still used"


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


ALL = load_seed_presets()


def test_no_two_characters_get_the_same_centre_pose():
    """Every sheet used to open with `casual, leaning_forward, dynamic posture`,
    so thirty reference boards came back as thirty people leaning at the viewer
    in different clothes, and the slot could not tell you whether the character
    rendered."""
    poses = [centre_pose(preset_to_character(p)).split(",")[0] for p in ALL]
    assert len(set(poses)) >= len(ALL) * 0.8, sorted(set(poses))
    # And where it does appear it is because she actually does it.
    for preset in ALL:
        character = preset_to_character(preset)
        pose = centre_pose(character)
        if "leaning_forward" in pose:
            assert "leaning_forward" in character["gesture_vocab"], preset["id"]


def test_the_centre_pose_prefers_her_hands_over_her_furniture():
    """`holding_book` says who she is; `sitting` says she has a chair."""
    character = {
        **CHARACTER,
        "gesture_vocab": ["sitting", "holding_book", "reading"],
    }
    assert centre_pose(character).startswith("holding_book")


def test_both_slots_put_her_somewhere():
    """With no scene the board renders against seamless white, which proves the
    tags parse and nothing about whether she belongs anywhere."""
    for preset in ALL:
        for slot in ("sheet", "portrait"):
            positive, _ = compile_board_slot(preset, slot)
            assert "Scene:" in positive, f"{preset['id']}/{slot}"
        vibe = (preset.get("default_scene") or {}).get("vibe_keywords") or []
        assert vibe[0] in compile_board_slot(preset, "sheet")[0], preset["id"]


def test_the_portrait_shows_a_face_doing_something():
    """It used to carry no expression at all, so thirty characters came back
    with the same neutral stare."""
    for preset in ALL:
        positive, _ = compile_board_slot(preset, "portrait")
        faces = preset["tags"]["expression"]
        assert any(f in positive for f in faces), preset["id"]


def test_only_an_actual_athlete_is_dressed_for_sport():
    """`walking` is on the active hint list and was also the fallback, so the
    fallback matched it and twenty-nine of thirty went jogging."""
    dressed = 0
    for preset in ALL:
        character = preset_to_character(preset)
        moving = sheet_vignettes(character)[2]
        if "sportswear" in moving:
            dressed += 1
            assert any(
                h in g for g in character["gesture_vocab"] for h in _ACTIVE_HINTS
            ), f"{preset['id']} is in sportswear without a sport"
    assert dressed < len(ALL) // 2, "most of the roster is not athletic"


def test_the_moving_frame_is_not_one_stock_frame_for_everyone():
    frames = [sheet_vignettes(preset_to_character(p))[2] for p in ALL]
    assert len(set(frames)) >= len(ALL) * 0.8, sorted(set(frames))


def test_the_planner_is_shown_the_whole_personality():
    """A character is a personality before she is a tag list, and a
    user-authored one may be almost nothing but personality. Half of it was
    never reaching the prompt."""
    captured = {}

    class Recording:
        async def generate_text(self, prompt, **kw):
            captured["prompt"] = prompt
            return "{}"

    asyncio.run(plan_sheet(PRESET, Recording()))
    prompt = captured["prompt"]
    personality = CHARACTER["personality"]
    for field in ("charm", "signature_moment"):
        assert personality[field][:24] in prompt, field
    assert personality["inner"][0][:24] in prompt
    assert (personality["appearance"]["habit"])[:24] in prompt
    assert (personality["appearance"]["first_impression"])[:24] in prompt
    # And it is told what shape to answer in, so the format stays ours.
    assert '"center"' in prompt and '"portrait"' in prompt


def test_the_portrait_takes_its_face_from_the_plan():
    """Two expression tags off a preset cannot say 'her ears go red before her
    face catches up'. A model reading her charm can."""
    plan = {"portrait": "blush, covering_mouth", "portrait_scene": "red safelight"}
    positive, _ = compile_board_slot(PRESET, "portrait", plan)
    assert "covering_mouth" in positive
    assert "Scene: red safelight" in positive
    # Identity is never the plan's to decide.
    for tag in CHARACTER["identity_tags"]:
        assert tag in positive


def test_a_planned_gaze_replaces_the_stock_one():
    """The framing block asks for eye contact; a portrait built from her charm
    often looks away. Both in one prompt is two portraits."""
    positive, _ = compile_board_slot(
        PRESET, "portrait", {"portrait": "blush, looking_away"},
    )
    assert "looking_away" in positive
    assert "looking_at_viewer" not in positive


def test_an_eyelid_is_not_a_gaze():
    """`half_closed_eyes` matched the `closed_eyes` substring and stripped eye
    contact from every character whose default face was sleepy."""
    assert not _is_a_gaze("half_closed_eyes")
    assert not _is_a_gaze("narrowed_eyes")
    assert _is_a_gaze("looking_away")
    assert _is_a_gaze("closed_eyes")


def test_a_plan_without_a_portrait_still_renders_one():
    """The bust shot is optional in the plan: a model that got the sheet right
    and the face wrong should not cost us the sheet."""
    positive, _ = compile_board_slot(PRESET, "portrait", {"center": "x", "portrait": ""})
    faces = PRESET["tags"]["expression"]
    assert any(f in positive for f in faces)
    assert "Scene:" in positive
