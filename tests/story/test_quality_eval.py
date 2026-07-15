"""Tests for chronicle quality_eval radar scoring."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.quality import (
    QUALITY_DIMS,
    evaluate_chronicle_quality,
    quality_eval_failure,
)


def _good_prompts():
    return {
        "past": (
            "1girl, spilling, holding, surprised, milk, pitcher, apron, cafe, "
            "morning, towel, indoors, counter, silver_hair, blue_eyes, solo, "
            "reaching, wet, day, daylight, detailed_background, depth_of_field, "
            "cinematic_lighting, highres, sharp_focus, dynamic_angle, steam, "
            "white_shirt, open_mouth, foam, metal_pitcher"
        ),
        "present": (
            "1girl, pouring, holding, smile, latte_art, coffee_cup, cafe, day, "
            "window, indoors, counter, steam, ceramic, silver_hair, blue_eyes, "
            "solo, concentrating, daylight, detailed_background, depth_of_field, "
            "cinematic_lighting, highres, sharp_focus, dynamic_angle, apron, "
            "heart, warm_light"
        ),
        "future": (
            "1girl, wiping, pointing, teaching, serious, espresso_machine, cafe, "
            "indoors, evening, cloth, steam, back_bar, warm_light, silver_hair, "
            "blue_eyes, solo, detailed_background, depth_of_field, "
            "cinematic_lighting, highres, sharp_focus, dynamic_angle, apron, "
            "junior, steamer_wand"
        ),
    }


def test_evaluate_good_cafe_story_scores_high():
    q = evaluate_chronicle_quality(
        user_topic="この子がカフェで働く話",
        title="First Pour",
        overall="A barista grows from spilled milk to mentoring juniors at the cafe.",
        stories={
            "past": "She spills the milk pitcher at the cafe counter in the morning.",
            "present": "She pours latte art for a regular at the sunlit cafe.",
            "future": "Years later she wipes the espresso machine and teaches a junior.",
        },
        activities={
            "past": "Tipping a milk pitcher over the cafe counter, soaking her apron.",
            "present": "Pouring a heart latte into a cup at the cafe counter.",
            "future": "Wiping the espresso machine at the cafe while pointing to the steamer.",
        },
        prompts=_good_prompts(),
        time_scale="years",
        lock_tags=["silver_hair", "blue_eyes"],
    )
    assert q["version"] == 1
    assert q["method"] == "rules"
    assert set(q["dimensions"]) == set(QUALITY_DIMS)
    assert q["overall"] >= 0.55
    assert q["dimensions"]["topic_fit"] >= 0.5
    assert q["dimensions"]["expression"] >= 0.7
    assert q["dimensions"]["diversity"] >= 0.4
    assert q["dimensions"]["identity"] >= 0.9


def test_evaluate_collapsed_idle_scores_low_diversity_and_expression_or_action():
    same = (
        "1girl, standing, smile, cafe, counter, indoors, day, silver_hair, "
        "blue_eyes, solo, looking_at_viewer, detailed_background, "
        "depth_of_field, cinematic_lighting, highres, sharp_focus, "
        "dynamic_angle, white_shirt, apron, portrait, static_pose, "
        "arms_at_sides, closed_mouth, upper_body, cowboy_shot, soft_light"
    )
    q = evaluate_chronicle_quality(
        user_topic="カフェの話",
        title="Smile",
        overall="She stands and smiles.",
        stories={
            "past": "She stands at the cafe counter smiling at the camera.",
            "present": "She stands at the cafe counter smiling at the camera.",
            "future": "She stands at the cafe counter smiling at the camera.",
        },
        activities={
            "past": "Standing at the cafe counter smiling.",
            "present": "Standing at the cafe counter smiling.",
            "future": "Standing at the cafe counter smiling.",
        },
        prompts={"past": same, "present": same, "future": same},
        time_scale="years",
        lock_tags=["silver_hair", "blue_eyes"],
    )
    assert q["dimensions"]["diversity"] <= 0.4
    assert q["dimensions"]["action"] <= 0.5
    assert q["overall"] < q["dimensions"]["identity"]  # identity can still be high


def test_evaluate_accepts_prompt_dicts():
    q = evaluate_chronicle_quality(
        prompts={
            "past": {"positive": _good_prompts()["past"], "negative": "blurry"},
            "present": {"positive": _good_prompts()["present"]},
            "future": {"positive": _good_prompts()["future"]},
        },
        stories={"past": "a", "present": "b", "future": "c"},
        activities={"past": "pour milk", "present": "wipe counter", "future": "teach junior"},
        time_scale="years",
    )
    assert "expression" in q["dimensions"]


def test_empty_prompt_axes_are_skipped_not_perfect():
    """Empty positives among scored axes must not score as perfect expression."""
    q = evaluate_chronicle_quality(
        prompts={
            "past": "",
            "present": _good_prompts()["present"],
            "future": _good_prompts()["future"],
        },
        stories={
            "past": "She spills milk.",
            "present": "She pours latte art.",
            "future": "She teaches a junior.",
        },
        activities={
            "past": "spilling milk",
            "present": "pouring latte",
            "future": "teaching junior",
        },
        time_scale="years",
        scored_axes=["past", "present", "future"],
    )
    assert q["per_axis"]["expression"]["past"].get("skipped") is True
    assert q["per_axis"]["expression"]["past"].get("score") is None
    # Non-empty axes still contribute; overall expression stays healthy.
    assert q["dimensions"]["expression"] >= 0.5
    assert q["scored_axes"] == ["past", "present", "future"]


def test_topic_fit_includes_prompts_and_directive():
    """topic_fit should consider prompt tag text and topic_directive aliases."""
    weak = evaluate_chronicle_quality(
        user_topic="カフェで働く",
        title="Walk",
        overall="A stroll.",
        stories={
            "past": "She walks outside.",
            "present": "She walks outside.",
            "future": "She walks outside.",
        },
        activities={
            "past": "walking",
            "present": "walking",
            "future": "walking",
        },
        prompts={
            "past": "1girl, walking, outdoors",
            "present": "1girl, walking, outdoors",
            "future": "1girl, walking, outdoors",
        },
        time_scale="years",
    )
    strong = evaluate_chronicle_quality(
        user_topic="カフェで働く",
        title="First Pour",
        overall="Barista at the cafe.",
        stories={
            "past": "She spills milk at the cafe.",
            "present": "She pours latte art.",
            "future": "She teaches espresso.",
        },
        activities={
            "past": "spilling milk pitcher",
            "present": "pouring latte",
            "future": "teaching junior barista",
        },
        prompts=_good_prompts(),
        time_scale="years",
        topic_directive="cafe barista coffee latte",
    )
    assert strong["dimensions"]["topic_fit"] >= weak["dimensions"]["topic_fit"]
    assert strong["dimensions"]["topic_fit"] >= 0.5


def test_evaluate_handles_list_valued_stories():
    """List-valued story axes must coerce to strings without throwing."""
    q = evaluate_chronicle_quality(
        user_topic="cafe",
        title="First Pour",
        overall="A barista grows at the cafe.",
        stories={
            "past": ["She spills", "milk at the cafe counter"],
            "present": ["She pours latte art"],
            "future": ["She teaches", "a junior barista"],
        },
        activities={
            "past": ["tipping pitcher", "over counter"],
            "present": "pouring latte",
            "future": {"positive": "wiping espresso machine"},
        },
        prompts=_good_prompts(),
        time_scale="years",
    )
    assert q["ok"] is True
    assert q["overall"] is not None
    assert set(q["dimensions"]) == set(QUALITY_DIMS)


def test_quality_eval_failure_shape():
    stub = quality_eval_failure(ValueError("bad axis text"))
    assert stub["ok"] is False
    assert stub["version"] == 1
    assert stub["method"] == "rules"
    assert stub["error"] == "bad axis text"
    assert stub["overall"] is None
    assert stub["dimensions"] is None
    assert stub["per_axis"] == {}
    assert stub["notes"] == {"error": "bad axis text"}
    assert stub["scored_axes"] == []
    assert "evaluated_at" in stub

    stub_str = quality_eval_failure("plain message")
    assert stub_str["error"] == "plain message"
