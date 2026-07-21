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
        "panel_1": (
            "1girl, spilling, holding, surprised, milk, pitcher, apron, cafe, "
            "morning, towel, indoors, counter, silver_hair, blue_eyes, solo, "
            "reaching, wet, day, daylight, detailed_background, depth_of_field, "
            "cinematic_lighting, highres, sharp_focus, dynamic_angle, steam, "
            "white_shirt, open_mouth, foam, metal_pitcher"
        ),
        "panel_2": (
            "1girl, pouring, holding, smile, latte_art, coffee_cup, cafe, day, "
            "window, indoors, counter, steam, ceramic, silver_hair, blue_eyes, "
            "solo, concentrating, daylight, detailed_background, depth_of_field, "
            "cinematic_lighting, highres, sharp_focus, dynamic_angle, apron, "
            "heart, warm_light"
        ),
        "panel_3": (
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
            "panel_1": "She spills the milk pitcher at the cafe counter in the morning.",
            "panel_2": "She pours latte art for a regular at the sunlit cafe.",
            "panel_3": "Years later she wipes the espresso machine and teaches a junior.",
        },
        activities={
            "panel_1": "Tipping a milk pitcher over the cafe counter, soaking her apron.",
            "panel_2": "Pouring a heart latte into a cup at the cafe counter.",
            "panel_3": "Wiping the espresso machine at the cafe while pointing to the steamer.",
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
            "panel_1": "She stands at the cafe counter smiling at the camera.",
            "panel_2": "She stands at the cafe counter smiling at the camera.",
            "panel_3": "She stands at the cafe counter smiling at the camera.",
        },
        activities={
            "panel_1": "Standing at the cafe counter smiling.",
            "panel_2": "Standing at the cafe counter smiling.",
            "panel_3": "Standing at the cafe counter smiling.",
        },
        prompts={"panel_1": same, "panel_2": same, "panel_3": same},
        time_scale="years",
        lock_tags=["silver_hair", "blue_eyes"],
    )
    assert q["dimensions"]["diversity"] <= 0.4
    assert q["dimensions"]["action"] <= 0.5
    assert q["overall"] < q["dimensions"]["identity"]  # identity can still be high


def test_evaluate_accepts_prompt_dicts():
    q = evaluate_chronicle_quality(
        prompts={
            "panel_1": {"positive": _good_prompts()["panel_1"], "negative": "blurry"},
            "panel_2": {"positive": _good_prompts()["panel_2"]},
            "panel_3": {"positive": _good_prompts()["panel_3"]},
        },
        stories={"panel_1": "a", "panel_2": "b", "panel_3": "c"},
        activities={"panel_1": "pour milk", "panel_2": "wipe counter", "panel_3": "teach junior"},
        time_scale="years",
    )
    assert "expression" in q["dimensions"]


def test_empty_prompt_axes_are_skipped_not_perfect():
    """Empty positives among scored axes must not score as perfect expression."""
    q = evaluate_chronicle_quality(
        prompts={
            "panel_1": "",
            "panel_2": _good_prompts()["panel_2"],
            "panel_3": _good_prompts()["panel_3"],
        },
        stories={
            "panel_1": "She spills milk.",
            "panel_2": "She pours latte art.",
            "panel_3": "She teaches a junior.",
        },
        activities={
            "panel_1": "spilling milk",
            "panel_2": "pouring latte",
            "panel_3": "teaching junior",
        },
        time_scale="years",
        scored_axes=["panel_1", "panel_2", "panel_3"],
    )
    assert q["per_axis"]["expression"]["panel_1"].get("skipped") is True
    assert q["per_axis"]["expression"]["panel_1"].get("score") is None
    # Non-empty axes still contribute; overall expression stays healthy.
    assert q["dimensions"]["expression"] >= 0.5
    assert q["scored_axes"] == ["panel_1", "panel_2", "panel_3"]


def test_scores_two_axes_when_base_image_supplies_one():
    """Regression: every image-seeded chronicle failed scoring.

    With a base image the runner regenerates only the OTHER two axes and passes
    scored_axes=[2 axes], but _mean_pairwise_similarity hardcoded the pairs
    (0,1)/(0,2)/(1,2) — beats[2] raised IndexError, surfacing in the UI as
    "品質採点に失敗しました: list index out of range".
    """
    for axes in (["panel_1", "panel_3"], ["panel_2", "panel_3"], ["panel_1", "panel_2"]):
        q = evaluate_chronicle_quality(
            prompts=_good_prompts(),
            stories={
                "panel_1": "She spills milk.",
                "panel_2": "She pours latte art.",
                "panel_3": "She teaches a junior.",
            },
            activities={
                "panel_1": "spilling milk",
                "panel_2": "pouring latte",
                "panel_3": "teaching junior",
            },
            time_scale="years",
            scored_axes=axes,
        )
        assert q.get("ok") is not False, q.get("error")
        assert q["scored_axes"] == axes
        assert 0.0 <= q["dimensions"]["diversity"] <= 1.0


def test_mean_pairwise_similarity_any_arity():
    from app.story.generator import _mean_pairwise_similarity as mps

    assert mps([]) == 0.0
    assert mps(["only one"]) == 0.0          # no pairs → no similarity
    assert mps(["same", "same"]) == 1.0
    assert mps(["same", "same", "same"]) == 1.0  # 3-beat behaviour preserved
    assert mps(["a totally different beat", "unrelated words here"]) < 1.0


def test_topic_fit_includes_prompts_and_directive():
    """topic_fit should consider prompt tag text and topic_directive aliases."""
    weak = evaluate_chronicle_quality(
        user_topic="カフェで働く",
        title="Walk",
        overall="A stroll.",
        stories={
            "panel_1": "She walks outside.",
            "panel_2": "She walks outside.",
            "panel_3": "She walks outside.",
        },
        activities={
            "panel_1": "walking",
            "panel_2": "walking",
            "panel_3": "walking",
        },
        prompts={
            "panel_1": "1girl, walking, outdoors",
            "panel_2": "1girl, walking, outdoors",
            "panel_3": "1girl, walking, outdoors",
        },
        time_scale="years",
    )
    strong = evaluate_chronicle_quality(
        user_topic="カフェで働く",
        title="First Pour",
        overall="Barista at the cafe.",
        stories={
            "panel_1": "She spills milk at the cafe.",
            "panel_2": "She pours latte art.",
            "panel_3": "She teaches espresso.",
        },
        activities={
            "panel_1": "spilling milk pitcher",
            "panel_2": "pouring latte",
            "panel_3": "teaching junior barista",
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
            "panel_1": ["She spills", "milk at the cafe counter"],
            "panel_2": ["She pours latte art"],
            "panel_3": ["She teaches", "a junior barista"],
        },
        activities={
            "panel_1": ["tipping pitcher", "over counter"],
            "panel_2": "pouring latte",
            "panel_3": {"positive": "wiping espresso machine"},
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
