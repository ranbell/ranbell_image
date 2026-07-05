"""Tests for the Chronicle story pipeline (prompt builders and parsers).

Covers:
  - parse_story_sections(): marker splitting, missing acts, case handling
  - parse_prompt_json(): clean / wrapped / broken JSON
  - build_story_prompt(): base-axis anchoring, worldview, mutation tags
  - build_axis_prompt(): prompt_style variants, identity source selection
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
    build_vision_prompt,
    character_tags_from_wd14,
    parse_prompt_json,
    parse_story_sections,
    remove_conflict_tags,
)


# ── parse_story_sections ──────────────────────────────────────────────────────

def test_parse_story_sections_full():
    raw = (
        "Here is the chronicle.\n"
        "[PAST]\nShe was a foundling in the lower city.\n\n"
        "[PRESENT]\nShe stands on the airship deck.\n"
        "[FUTURE]\nShe will command the fleet."
    )
    result = parse_story_sections(raw)
    assert result["past"] == "She was a foundling in the lower city."
    assert result["present"] == "She stands on the airship deck."
    assert result["future"] == "She will command the fleet."


def test_parse_story_sections_missing_axis():
    raw = "[PAST]\nonly past\n[PRESENT]\nand present"
    result = parse_story_sections(raw)
    assert result["past"] == "only past"
    assert result["present"] == "and present"
    assert result["future"] == ""


def test_parse_story_sections_case_insensitive():
    raw = "[past]\na\n[Present]\nb\n[FUTURE]\nc"
    result = parse_story_sections(raw)
    assert result == {"past": "a", "present": "b", "future": "c"}


def test_parse_story_sections_empty():
    assert parse_story_sections("no markers at all") == {
        "past": "", "present": "", "future": ""
    }


# ── parse_prompt_json ─────────────────────────────────────────────────────────

def test_parse_prompt_json_clean():
    raw = '{"positive": "1girl, silver_hair, airship deck", "negative": "lowres"}'
    positive, negative = parse_prompt_json(raw)
    assert positive == "1girl, silver_hair, airship deck"
    assert negative == "lowres"


def test_parse_prompt_json_wrapped():
    raw = 'Sure! Here is the JSON:\n```json\n{"positive": "p", "negative": "n"}\n```'
    assert parse_prompt_json(raw) == ("p", "n")


def test_parse_prompt_json_missing_negative():
    positive, negative = parse_prompt_json('{"positive": "p"}')
    assert positive == "p"
    assert negative == ""


def test_parse_prompt_json_broken():
    assert parse_prompt_json("not json at all") == ("", "")
    assert parse_prompt_json('["a", "b"]') == ("", "")


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
    assert "[PAST]" in prompt and "[PRESENT]" in prompt and "[FUTURE]" in prompt


def test_build_story_prompt_empty_worldview():
    prompt = build_story_prompt(
        character_desc="c", scene_desc="s", base_axis="past", worldview="  ",
    )
    assert "invent a fitting, evocative world" in prompt


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

def test_build_axis_prompt_styles():
    kwargs = dict(
        story_text="She walks the ruins.",
        character_tags=["1girl", "silver_hair"],
        character_desc="",
    )
    tags_only = build_axis_prompt(prompt_style="danbooru", **kwargs)
    assert "comma-separated danbooru tag list" in tags_only
    natural = build_axis_prompt(prompt_style="natural", **kwargs)
    assert "natural-language" in natural
    both = build_axis_prompt(prompt_style="danbooru+natural", **kwargs)
    assert "danbooru tag line" in both
    assert '{"positive"' in both


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
