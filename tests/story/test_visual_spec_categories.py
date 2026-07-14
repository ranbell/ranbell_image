"""Tests for Chronicle Visual Spec category parsing / bucketing."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.generator import (
    bucket_danbooru_tags,
    build_axis_prose_prompt,
    merge_category_tags,
    parse_visual_script_category_tags,
)


def test_parse_visual_script_category_tags_splits_prose():
    raw = (
        "A girl runs on the beach (1girl, running, beach).\n\n"
        "SUBJECT_TAGS: 1girl, solo\n"
        "HAIR_TAGS: brown_hair, long_hair\n"
        "EXPRESSION_TAGS: smile, open_mouth\n"
        "POSE_TAGS: running, dynamic_pose\n"
        "BACKGROUND_TAGS: beach, ocean, outdoors\n"
        "LIGHTING_TAGS: daylight, sparkle\n"
    )
    prose, cats = parse_visual_script_category_tags(raw)
    assert "runs on the beach" in prose
    assert "SUBJECT_TAGS" not in prose
    assert cats["subject_tags"] == ["1girl", "solo"]
    assert "smile" in cats["expression_tags"]
    assert "daylight" in cats["lighting_tags"]


def test_bucket_danbooru_tags_basic():
    line = (
        "1girl, solo, brown_hair, blue_eyes, smile, white_dress, "
        "running, beach, ocean, daylight, rim_light, shell"
    )
    cats = bucket_danbooru_tags(line)
    assert "1girl" in cats["subject_tags"]
    assert "brown_hair" in cats["hair_tags"]
    assert "blue_eyes" in cats["subject_tags"]  # colour → identity cue
    assert "smile" in cats["expression_tags"]
    assert "running" in cats["pose_tags"]
    assert "beach" in cats["background_tags"]
    assert "rim_light" in cats["lighting_tags"]
    assert "shell" in cats["object_tags"] or "white_dress" in cats.get("clothing_tags", [])


def test_merge_category_tags_first_wins():
    a = {"hair_tags": ["blonde_hair"], "pose_tags": ["running"]}
    b = {"hair_tags": ["black_hair", "long_hair"], "lighting_tags": ["sunset"]}
    m = merge_category_tags(a, b)
    assert m["hair_tags"][0] == "blonde_hair"
    assert "black_hair" in m["hair_tags"]
    assert "long_hair" in m["hair_tags"]
    assert m["pose_tags"] == ["running"]
    assert m["lighting_tags"] == ["sunset"]


def test_build_axis_prose_prompt_asks_for_labeled_categories():
    prompt = build_axis_prose_prompt(
        story_text="She runs on the beach.",
        tag_line="1girl, running, beach, smile",
        character_tags=["brown_hair"],
        character_desc="girl",
        prompt_style="danbooru+natural",
    )
    assert "PASS 1 DANBOORU TAG LINE" in prompt
    assert "SUBJECT_TAGS:" in prompt
    assert "LIGHTING_TAGS:" in prompt
    assert "Work in DANBOORU TAGS first" in prompt
    assert "THREE parts" in prompt
