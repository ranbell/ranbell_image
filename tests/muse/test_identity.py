"""Identity lock, WD14 body conflicts, hybrid assemble, framing tags."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import identity


def test_conflicting_breast_tags_are_banned_when_small_is_locked():
    banned = identity.conflicting_body_tags(["1girl", "small_breasts", "blue_hair"])
    assert "large_breasts" in banned
    assert "huge_breasts" in banned
    assert "small_breasts" not in banned


def test_drop_conflicting_tags_strips_wd14_body_guesses():
    tags = "1girl, blue_hair, large_breasts, rooftop, skirt"
    got = identity.drop_conflicting_tags(tags, ["1girl", "small_breasts", "blue_hair"])
    assert "large_breasts" not in got
    assert "rooftop" in got
    assert "skirt" in got


def test_opposing_negative_pushes_against_extreme_upgrades():
    neg = identity.opposing_negative(["small_breasts"])
    assert "large_breasts" in neg
    assert "huge_breasts" in neg
    assert "small_breasts" not in neg


def test_assemble_positive_leads_with_identity_and_appends_framing():
    positive = identity.assemble_positive(
        ["1girl", "small_breasts", "blue_hair"],
        "standing, rooftop, large_breasts",
        "She waits in the rain.",
        framing="face_closeup",
    )
    assert positive.startswith("1girl, small_breasts, blue_hair")
    assert "large_breasts" not in positive
    assert "close_up" in positive
    assert "She waits in the rain." in positive


def test_parse_hybrid_and_prose_fallback():
    tags, scene = identity.parse_hybrid(
        "TAGS: standing, rain\n\nSCENE: She leans on the rail."
    )
    assert tags == "standing, rain"
    assert scene == "She leans on the rail."
    tags, scene = identity.parse_hybrid("just a paragraph of prose")
    assert tags == ""
    assert scene == "just a paragraph of prose"


def test_pose_summary_keeps_two_sentences_from_scene():
    raw = "TAGS: x\n\nSCENE: She waits. Rain ticks on the rail. A third line."
    assert identity.pose_summary(raw) == "She waits. Rain ticks on the rail."


def test_framing_tags_and_normalize():
    assert identity.normalize_framing("Face Close-Up") == "face_closeup"
    assert identity.normalize_framing("nope") == "auto"
    assert identity.parse_framing("from_behind") == "from_behind"
    try:
        identity.parse_framing("nope")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert "from_behind" in identity.framing_tags("from_behind")
    assert "full_body" in identity.framing_negative("face_closeup")


def test_pose_intent_comes_from_scene_not_identity_prefix():
    from app.muse import chain

    raw = "TAGS: standing, rooftop\n\nSCENE: She waits in the rain."
    result = chain._finish_turn(
        raw, muse_id="beat", identity_tags=["1girl", "small_breasts"],
        framing="auto", brief="B",
    )
    assert result.prompt.startswith("1girl, small_breasts")
    assert result.pose_intent == "She waits in the rain."
    assert "small_breasts" not in result.pose_intent


def test_parse_table_read_keeps_say_separate_from_craft():
    say, tags, scene = identity.parse_table_read(
        "SAY: 総監督、寄りましょう。\n\n"
        "TAGS: close_up, rain\n\n"
        "SCENE: She leans into the frame."
    )
    assert "総監督" in say
    assert tags == "close_up, rain"
    assert scene == "She leans into the frame."


def test_craft_is_thin_flags_short_scene():
    assert identity.craft_is_thin("1girl, smile", "She sits.")
    rich_scene = " ".join(["word"] * 160)
    rich_prompt = "1girl, aqua_hair, sitting, window, " + rich_scene
    assert identity.word_count(rich_prompt) >= 160
    assert not identity.craft_is_thin(rich_prompt, rich_scene)


def test_merge_negative_dedupes():
    assert identity.merge_negative(
        "bad quality, large_breasts",
        "large_breasts, huge_breasts",
    ) == "bad quality, large_breasts, huge_breasts"


def test_reference_leak_detects_fenced_likes():
    from app.muse import brief as brief_mod

    character = {
        "identity_tags": ["1girl"],
        "personality": {
            "traits": ["calm"],
            "summary": "",
            "likes": ["thermos coffee", "night walks"],
            "dislikes": [],
            "inner": [],
        },
        "palette": [],
        "signature_prop": "",
    }
    text = brief_mod.build(character, "on a rooftop", "anime")
    leaked = identity.warn_reference_leak(
        text, "a girl with a thermos coffee on the roof",
    )
    assert any("thermos coffee" in x for x in leaked)
