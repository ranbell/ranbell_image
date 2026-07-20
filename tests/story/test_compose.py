"""Unit tests for Chronicle allowlist compose + labeled positives."""
from __future__ import annotations

from app.story.compose import (
    filter_compose_result,
    format_labeled_positive,
    format_summary,
    soft_normalize_tag,
)
from app.story.generator import default_act_labels, repair_act_labels


def test_soft_normalize_strips_prefixes():
    assert soft_normalize_tag("white_blouse") == "blouse"
    assert soft_normalize_tag("school_blazer") == "blazer"
    assert soft_normalize_tag("student_cardigan") == "cardigan"
    assert soft_normalize_tag("close_up") == "close-up"


def test_format_summary_no_double_at():
    s = format_summary("now", "pouring coffee", "behind the cafe counter")
    assert s == "now — pouring coffee, behind the cafe counter"
    assert "at behind" not in s


def test_repair_act_labels_always_canonical():
    cand = {
        "acts": {
            "past": {"label": "2 hours earlier", "activity": "x"},
            "present": {"label": "now", "activity": "y"},
            "future": {"label": "weeks later", "activity": "z"},
        }
    }
    repair_act_labels(cand, base_axis="present", time_scale="days", locale="en")
    defaults = default_act_labels("present", "days", "en")
    assert cand["acts"]["past"]["label"] == defaults["past"]
    assert cand["acts"]["future"]["label"] == defaults["future"]
    assert cand["acts"]["present"]["label"] == defaults["present"]


def test_filter_compose_present_exclusive_and_shots():
    acts = {
        "past": {
            "label": "a few days earlier",
            "activity": "sketching",
            "place": "classroom",
            "feeling": "worried",
            "outfit": "blazer, jeans",
        },
        "present": {
            "label": "now",
            "activity": "pouring coffee behind the counter",
            "place": "cafe counter",
            "feeling": "focused",
            "outfit": "apron, blouse",
        },
        "future": {
            "label": "a few days later",
            "activity": "presenting portfolio",
            "place": "gallery indoors",
            "feeling": "proud",
            "outfit": "cardigan, skirt",
        },
    }
    composed = {
        "past": {
            "pose": ["sitting"],
            "outfit": ["blazer", "jeans"],
            "shot": ["upper_body"],
            "effect": ["classroom", "desk", "pouring", "steam"],
        },
        "present": {
            "pose": ["pouring"],
            "outfit": ["apron", "blouse"],
            "shot": ["close-up"],
            "effect": ["cafe", "counter"],
        },
        "future": {
            "pose": ["standing"],
            "outfit": ["cardigan", "skirt"],
            "shot": ["upper_body"],  # duplicate → forced unique
            "effect": ["indoors", "light_rays"],
        },
    }
    out = filter_compose_result(
        composed,
        acts,
        identity=["1girl", "solo", "grey_hair", "red_eyes"],
        base_axis="present",
    )
    assert "pouring" not in out["past"]["effect"]
    assert "steam" not in out["past"]["effect"]
    assert out["present"]["pose"][0] == "pouring"
    assert "apron" in out["present"]["outfit"]
    shots = {out[a]["shot"][0] for a in ("past", "present", "future")}
    assert len(shots) == 3
    pos = out["present"]["positive"]
    assert pos.startswith("Summary:")
    assert "Character:" in pos
    assert "Pose: pouring" in pos


def test_format_labeled_positive_shape():
    text = format_labeled_positive(
        summary="now — pour",
        character=["1girl", "solo", "serious"],
        outfit=["apron"],
        pose=["pouring"],
        shot=["close-up"],
        effect=["cafe"],
    )
    lines = text.splitlines()
    assert len(lines) == 6
    assert lines[0].startswith("Summary:")
