"""Tests for the Chronicle story pipeline (prompt builders and parsers).

Covers:
  - parse_story_sections(): marker splitting incl. TITLE/OVERALL, missing acts
  - build_story_prompt(): base-axis anchoring, worldview, time scale, mutation tags
  - build_axis_prompt(): Visual Script guide, prompt_style variants, identity source
  - build_translation_prompt() / parse_translation_json(): JA translation stage
  - build_vision_prompt(): full vs. tags-assisted extraction
  - character_tags_from_wd14(): meta-tag filtering
  - remove_conflict_tags(): tag-line filtering, prose preservation
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.generator import (
    build_axis_prompt,
    build_story_prompt,
    build_translation_prompt,
    build_vision_prompt,
    character_tags_from_wd14,
    parse_story_sections,
    parse_translation_json,
    remove_conflict_tags,
)


# ── parse_story_sections ──────────────────────────────────────────────────────

def test_parse_story_sections_full():
    raw = (
        "Here is the chronicle.\n"
        "[TITLE]\nThe Clockwork Ascent\n"
        "[OVERALL]\nA foundling rises to command the skies.\n"
        "[PAST]\nShe was a foundling in the lower city.\n\n"
        "[PRESENT]\nShe stands on the airship deck.\n"
        "[FUTURE]\nShe will command the fleet."
    )
    result = parse_story_sections(raw)
    assert result["title"] == "The Clockwork Ascent"
    assert result["overall"] == "A foundling rises to command the skies."
    assert result["past"] == "She was a foundling in the lower city."
    assert result["present"] == "She stands on the airship deck."
    assert result["future"] == "She will command the fleet."


def test_parse_story_sections_missing_title_overall():
    raw = "[PAST]\nonly past\n[PRESENT]\nand present"
    result = parse_story_sections(raw)
    assert result["title"] == ""
    assert result["overall"] == ""
    assert result["past"] == "only past"
    assert result["present"] == "and present"
    assert result["future"] == ""


def test_parse_story_sections_case_insensitive():
    raw = "[Title]\nT\n[past]\na\n[Present]\nb\n[FUTURE]\nc"
    result = parse_story_sections(raw)
    assert result["title"] == "T"
    assert result["past"] == "a"
    assert result["present"] == "b"
    assert result["future"] == "c"


def test_parse_story_sections_title_single_line():
    raw = '[TITLE]\n"Two Lines"\nextra junk\n[PAST]\na\n[PRESENT]\nb\n[FUTURE]\nc'
    assert parse_story_sections(raw)["title"] == "Two Lines"


def test_parse_story_sections_empty():
    result = parse_story_sections("no markers at all")
    assert all(v == "" for v in result.values())


# ── build_story_prompt ────────────────────────────────────────────────────────

def test_build_story_prompt_base_axis_anchored():
    prompt = build_story_prompt(
        character_desc="danbooru tags: 1girl, silver_hair",
        scene_desc="SCENE: airship deck at dusk",
        base_axis="present",
        worldview="steampunk",
    )
    assert "THE PRESENT looks exactly like this scene" in prompt
    assert '"steampunk"' in prompt
    for marker in ("[TITLE]", "[OVERALL]", "[PAST]", "[PRESENT]", "[FUTURE]"):
        assert marker in prompt


def test_build_story_prompt_empty_worldview():
    prompt = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="past", worldview="  ",
    )
    assert "invent a fitting, evocative world" in prompt


def test_build_story_prompt_time_scale():
    minutes = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present",
        worldview="", time_scale="minutes",
    )
    assert "about a few minutes apart" in minutes
    decades = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present",
        worldview="", time_scale="decades",
    )
    assert "about several decades apart" in decades
    # unknown scale falls back to years
    fallback = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present",
        worldview="", time_scale="bogus",
    )
    assert "about a few years apart" in fallback


def test_build_story_prompt_mutation_tags():
    prompt = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present",
        worldview="", mutation_tags=["ruins", "bioluminescence"],
    )
    assert "ruins, bioluminescence" in prompt
    no_mutation = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="present", worldview="",
    )
    assert "Unexpected elements" not in no_mutation


# ── build_axis_prompt ─────────────────────────────────────────────────────────

def test_build_axis_prompt_visual_script_structure():
    prompt = build_axis_prompt(
        story_text="She walks the ruins.",
        character_tags=["1girl", "silver_hair"],
        character_desc="",
        prompt_style="danbooru+natural",
        wd14_context="[common tags] 1girl, silver_hair",
    )
    # 5-paragraph internal guide
    for para in ("APPEARANCE", "ACTION", "ENVIRONMENT", "DETAIL", "MOOD"):
        assert para in prompt
    assert "5 flowing paragraphs" in prompt
    # POSITIVE/NEGATIVE labeled output, not JSON
    assert "POSITIVE:" in prompt and "NEGATIVE:" in prompt
    assert '{"positive"' not in prompt
    # wd14 context embedded
    assert "[WD14 tag analysis of the base image]" in prompt


def test_build_axis_prompt_styles():
    kwargs = dict(
        story_text="She walks the ruins.",
        character_tags=["1girl", "silver_hair"],
        character_desc="",
    )
    tags_only = build_axis_prompt(prompt_style="danbooru", **kwargs)
    assert "comma-separated danbooru tag list (30-50 tags)" in tags_only
    assert "No prose" in tags_only
    natural = build_axis_prompt(prompt_style="natural", **kwargs)
    assert "5-paragraph Visual Script prose" in natural
    both = build_axis_prompt(prompt_style="danbooru+natural", **kwargs)
    assert "two parts separated by a blank line" in both


def test_build_axis_prompt_identity_source():
    with_tags = build_axis_prompt(
        story_text="s", character_tags=["1girl", "red_eyes"],
        character_desc="ignored", prompt_style="danbooru",
    )
    assert "1girl, red_eyes" in with_tags
    without_tags = build_axis_prompt(
        story_text="s", character_tags=[],
        character_desc="a girl with red eyes", prompt_style="danbooru",
    )
    assert "a girl with red eyes" in without_tags


# ── translation stage ─────────────────────────────────────────────────────────

def test_build_translation_prompt():
    prompt = build_translation_prompt(
        "The Ascent", "An arc.", {"past": "p", "present": "n", "future": "f"},
    )
    assert "TITLE: The Ascent" in prompt
    assert "PAST: p" in prompt
    assert '"title_ja"' in prompt and '"future_ja"' in prompt


def test_parse_translation_json_clean():
    raw = ('{"title_ja": "昇天", "overall_ja": "全体", "past_ja": "過去", '
           '"present_ja": "現在", "future_ja": "未来"}')
    result = parse_translation_json(raw)
    assert result["title_ja"] == "昇天"
    assert result["future_ja"] == "未来"


def test_parse_translation_json_wrapped_and_partial():
    raw = 'Here:\n```json\n{"title_ja": "題", "past_ja": "過去"}\n```'
    result = parse_translation_json(raw)
    assert result["title_ja"] == "題"
    assert result["past_ja"] == "過去"
    assert result["overall_ja"] == ""


def test_parse_translation_json_broken():
    result = parse_translation_json("not json")
    assert all(v == "" for v in result.values())
    assert set(result) == {"title_ja", "overall_ja", "past_ja", "present_ja", "future_ja"}


# ── build_vision_prompt ───────────────────────────────────────────────────────

def test_build_vision_prompt_modes():
    full = build_vision_prompt(full_extraction=True)
    assert "CHARACTER:" in full and "OUTFIT:" in full
    partial = build_vision_prompt(full_extraction=False)
    assert "Do NOT describe the character's appearance" in partial
    assert "CHARACTER:" not in partial


# ── character_tags_from_wd14 ──────────────────────────────────────────────────

def test_character_tags_filters_meta():
    tags = ["1girl", "masterpiece", "silver_hair", "highres", "sensitive",
            "school_uniform", "artist_username"]
    result = character_tags_from_wd14(tags)
    assert result == ["1girl", "silver_hair", "school_uniform"]


def test_character_tags_limit():
    tags = [f"tag_{i}" for i in range(60)]
    assert len(character_tags_from_wd14(tags, limit=40)) == 40


# ── remove_conflict_tags ──────────────────────────────────────────────────────

def test_remove_conflict_tags_tag_line():
    positive = "1girl, silver_hair, day, outdoors\n\nShe stands in the sun. Warm, calm."
    cleaned = remove_conflict_tags(positive, {"day"})
    assert "day" not in cleaned.split("\n")[0]
    assert "silver_hair" in cleaned
    # prose sentences (with periods) are untouched
    assert "She stands in the sun. Warm, calm." in cleaned


def test_remove_conflict_tags_noop():
    positive = "1girl, night"
    assert remove_conflict_tags(positive, set()) == positive
