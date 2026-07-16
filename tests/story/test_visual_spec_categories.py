"""Tests for Chronicle Visual Spec category parsing / bucketing."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.generator import (
    bucket_danbooru_tags,
    merge_category_tags,
    parse_visual_script_category_tags,
)
from app.prompt.visual_spec import (
    ensure_pose_tags_min_words,
    pose_tags_are_thin,
    pose_word_count,
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


def test_clamp_prose_paragraphs():
    from app.prompt.visual_spec import clamp_prose_paragraphs
    assert clamp_prose_paragraphs(1) == 3
    assert clamp_prose_paragraphs(5) == 5
    assert clamp_prose_paragraphs(99) == 7
    assert clamp_prose_paragraphs(None) == 5
    assert clamp_prose_paragraphs("nope") == 5


def test_parse_body_parts_and_pose_footer():
    raw = (
        "She reaches.\n\n"
        "BODY_PARTS_TAGS: outstretched_arm, clenched_hand\n"
        "POSE_TAGS: reaching, leaning_forward, dynamic_pose\n"
    )
    prose, cats = parse_visual_script_category_tags(raw)
    assert "She reaches" in prose
    assert cats["body_parts_tags"] == ["outstretched_arm", "clenched_hand"]
    assert "reaching" in cats["pose_tags"]


def test_bucket_puts_body_parts_separate_from_pose():
    cats = bucket_danbooru_tags(
        "1girl, reaching, outstretched_arm, running, bare_shoulders"
    )
    assert "outstretched_arm" in cats.get("body_parts_tags", [])
    assert "bare_shoulders" in cats.get("body_parts_tags", [])
    assert "reaching" in cats.get("pose_tags", [])
    assert "running" in cats.get("pose_tags", [])


def test_ensure_pose_tags_min_words_expands_idle():
    thin = {"pose_tags": ["standing", "sitting"]}
    assert pose_tags_are_thin(thin["pose_tags"])
    filled = ensure_pose_tags_min_words(
        thin,
        min_words=5,
        fillers=["reaching", "outstretched", "leaning_forward", "holding_cup"],
    )
    assert pose_word_count(filled["pose_tags"]) >= 5
    assert not pose_tags_are_thin(filled["pose_tags"])
    assert "standing" not in {t.lower() for t in filled["pose_tags"]} or "reaching" in {
        t.lower() for t in filled["pose_tags"]
    }
