"""Identity lock, WD14 body conflicts, hybrid assemble, framing tags."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import identity
from app.tags.body import BREAST_TAGS as _BREAST_SIZES


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


def test_the_shot_sheet_renders_identity_first_then_prose_in_slot_order():
    """Anima reads tags, natural language or a mix, and was trained with tag
    dropout — it does not want a wall of tags. Identity stays tagged because it
    is the part that may not drift; the crew's own writing becomes prose in an
    order the model cannot scramble."""
    from app.muse import crew

    shot = {
        "subject": "a girl at a table", "pose": "chin on hand",
        "place": "a narrow room", "light": "even daylight, normal exposure",
        "camera": "(medium shot:1.4)", "mood": "quietly pleased",
    }
    out = identity.render_shot(
        shot, identity_tags=["navy_hair", "small_breasts"],
        subject=["1girl", "solo"], style="anime illustration",
        framing="upper_body", slot_order=crew.SLOT_ORDER,
    )
    assert out.startswith("1girl, solo, navy_hair, small_breasts")
    assert out.index("a girl at a table") < out.index("chin on hand")
    assert out.index("chin on hand") < out.index("a narrow room")
    # The weight cap lived in a prompt before, and two 1.4s shipped anyway.
    assert "(medium shot:1.35)" in out
    assert "1.4" not in out


def test_a_seat_that_only_deletes_can_reach_a_slot_it_does_not_own():
    shot = {"light": "even daylight, deep shadows", "objects": ["mug", "napkin"]}
    out = identity.apply_delta(shot, remove=["deep shadows", "napkin"])
    assert out["light"] == "even daylight"
    assert out["objects"] == ["mug"]


def test_writing_outside_your_slots_is_dropped_not_trusted():
    out = identity.apply_delta(
        {"light": "even daylight"}, add={"light": "pitch black"}, allowed=("camera",),
    )
    assert out["light"] == "even daylight"


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


def test_the_style_reaches_the_prompt_not_only_the_brief():
    """The panel's Style box used to stop at the brief.

    It was handed to the LLM as a request and never became a tag, so a run
    asking for cute 2D anime rendered at whatever the checkpoint defaults to.
    """
    positive = identity.assemble_positive(
        ["black_hair", "blue_eyes"], "standing, workshop", "She stands.",
        framing="upper_body", style="Cute 2D Anime Style",
    )
    assert "cute_2d_anime_style" in positive
    # Directly after identity: the look colours everything that follows.
    assert positive.index("blue_eyes") < positive.index("cute_2d_anime_style")
    assert positive.index("cute_2d_anime_style") < positive.index("standing")


def test_a_comma_written_style_becomes_several_tags():
    positive = identity.assemble_positive(
        [], "standing", "", style="flat colour, bold outlines",
    )
    assert "flat_colour" in positive and "bold_outlines" in positive


def test_subject_count_comes_from_the_cast_not_the_character():
    one = identity.subject_tags([{"subject_tag": "1girl"}])
    assert one == ["1girl", "solo"]

    two = identity.subject_tags([{"subject_tag": "1girl"}, {"subject_tag": "1girl"}])
    assert two == ["2girls"]
    assert "solo" not in two

    mixed = identity.subject_tags([{"subject_tag": "1girl"}, {"subject_tag": "1boy"}])
    assert set(mixed) == {"1girl", "1boy"}

    assert identity.subject_tags([]) == []


def test_the_count_leads_the_prompt_and_never_repeats_identity():
    positive = identity.assemble_positive(
        ["black_hair"], "standing", "She stands.",
        subject=identity.subject_tags([{"subject_tag": "1girl"}]),
    )
    assert positive.startswith("1girl, solo, black_hair")


def test_the_figure_lock_strips_a_size_the_model_invented():
    """The whole point of putting a chest tag in identity: with the bucket empty
    the slot has nothing 'present', so nothing gets banned and a draft's guess
    walks straight into the prompt."""
    locked = ["black_hair", "small_breasts", "slim"]
    positive = identity.assemble_positive(
        locked, "large_breasts, curvy, standing, workshop", "She stands.",
    )
    assert "small_breasts" in positive and "slim" in positive
    assert "large_breasts" not in positive
    assert "curvy" not in positive
    assert "standing" in positive and "workshop" in positive


def test_the_opposites_reach_the_negative():
    negative = identity.opposing_negative(["black_hair", "small_breasts"])
    assert "large_breasts" in negative and "flat_chest" in negative
    assert "small_breasts" not in negative, "her own figure must not be negated"
    # The extremes are pushed against whenever any chest tag is locked.
    assert "huge_breasts" in negative and "gigantic_breasts" in negative


def test_an_empty_figure_locks_nothing():
    """Documents why the roster needed one: this is the state it was in."""
    banned = identity.conflicting_body_tags(["black_hair", "blue_eyes"])
    assert not (banned & set(_BREAST_SIZES)), sorted(banned & set(_BREAST_SIZES))


def test_petite_is_refused_whatever_she_is():
    """A slot only bans its other members when something is in it, and most
    characters name no height at all — so `petite` needed the unconditional
    treatment rather than a slot mate."""
    for locked in (["black_hair"], ["black_hair", "tall"], ["black_hair", "small_breasts"]):
        positive = identity.assemble_positive(locked, "petite, standing", "She stands.")
        assert "petite" not in positive, locked
    assert "petite" in identity.opposing_negative(["black_hair", "small_breasts"])
