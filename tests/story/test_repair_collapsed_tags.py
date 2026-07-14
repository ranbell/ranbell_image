"""Tests for repair_collapsed_axis_tags."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.generator import repair_collapsed_axis_tags


def test_repair_injects_unique_focal_and_expression():
    prompts = {
        "past": {"positive": "1girl, solo, cafe, standing, smile"},
        "present": {"positive": "1girl, solo, cafe, standing, smile"},
        "future": {"positive": "1girl, solo, cafe, standing, smile"},
    }
    visual_plans = {
        "past": {
            "focal_action_tags": ["spilling", "holding"],
            "expression_tag": "surprised",
        },
        "present": {
            "focal_action_tags": ["pouring", "latte_art"],
            "expression_tag": "smile",
        },
        "future": {
            "focal_action_tags": ["teaching", "pointing"],
            "expression_tag": "serious",
        },
    }
    activities = {
        "past": "Tipping a milk pitcher over the counter",
        "present": "Pouring a heart latte into a cup",
        "future": "Wiping the espresso machine while teaching",
    }
    out = repair_collapsed_axis_tags(
        prompts,
        visual_plans=visual_plans,
        activities=activities,
        gen_axes=["past", "present", "future"],
    )
    assert "spilling" in out["past"]["positive"]
    assert "surprised" in out["past"]["positive"]
    assert "pouring" in out["present"]["positive"]
    assert "teaching" in out["future"]["positive"]
    assert "pointing" in out["future"]["positive"]


def test_repair_idempotent_no_duplicate_tags():
    prompts = {
        "past": {"positive": "1girl, spilling, surprised, cafe"},
        "present": {"positive": "1girl, pouring, smile, cafe"},
        "future": {"positive": "1girl, teaching, serious, cafe"},
    }
    visual_plans = {
        "past": {"focal_action_tags": ["spilling"], "expression_tag": "surprised"},
        "present": {"focal_action_tags": ["pouring"], "expression_tag": "smile"},
        "future": {"focal_action_tags": ["teaching"], "expression_tag": "serious"},
    }
    once = repair_collapsed_axis_tags(
        prompts,
        visual_plans=visual_plans,
        activities={},
        gen_axes=["past", "present", "future"],
    )
    twice = repair_collapsed_axis_tags(
        once,
        visual_plans=visual_plans,
        activities={},
        gen_axes=["past", "present", "future"],
    )
    for axis in ("past", "present", "future"):
        parts = [p.strip().lower() for p in twice[axis]["positive"].split(",") if p.strip()]
        assert len(parts) == len(set(parts))
