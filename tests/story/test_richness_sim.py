"""Simulate whether Chronicle prompts can reach reference-grade visual richness.

Benchmarks inspired by dense illustration references:
  - golden-hour bicycle street (rim light, cafe storefront, fluttering scarf)
  - stadium celebration toast (ecstatic expressions, medals, confetti, trophies)

Compares a *rich* LLM-quality tag line vs a *thin* typical failure mode, and
runs full quality_eval + richness breakdown.

Run:
  PYTHONPATH=backend python3 -m pytest tests/story/test_richness_sim.py -q -s
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.generator import (
    _chronicle_tags_degenerate,
    build_axis_tags_prompt,
)
from app.story.quality import (
    evaluate_chronicle_quality,
    score_prompt_richness,
)

# ── Reference-grade prompts (what a strong Pass-1 should emit) ────────────────

BICYCLE_GOLDEN_HOUR = (
    "1girl, solo, black_hair, long_hair, ponytail, hair_ribbon, brown_eyes, "
    "smile, looking_back, blush, open_mouth, "
    "school_uniform, white_shirt, pleated_skirt, red_scarf, fluttering_scarf, "
    "dark_pantyhose, loafers, "
    "riding_bicycle, bicycle, pedaling, leaning_forward, dynamic_pose, wind, "
    "street, outdoors, cityscape, shop, storefront, cafe, window, bottle, "
    "streetlamp, potted_plant, flower, mountain, sky, cloud, "
    "sunset, golden_hour, rim_light, backlight, long_shadow, warm_light, "
    "lens_flare, cinematic_lighting, depth_of_field, "
    "from_side, cowboy_shot"
)

STADIUM_TOAST = (
    "3girls, multiple_girls, "
    "silver_hair, short_hair, black_hair, blonde_hair, long_hair, "
    "laughing, closed_eyes, open_mouth, blush, joyful, excited, "
    "sportswear, swimsuit, medal, "
    "holding, beer_mug, toast, cheering, clinking, "
    "stadium, outdoors, crowd, audience, bleachers, sky, "
    "trophy, bottle, table, "
    "confetti, streamer, particle_effects, sparkle, "
    "daylight, bright, warm_light, lens_flare, cinematic_lighting, "
    "upper_body, depth_of_field"
)

# Thin prompts: subject + smile + vague place — common weak LLM output
THIN_BIKE = (
    "1girl, solo, black_hair, long_hair, brown_eyes, smile, "
    "school_uniform, outdoors, street, day, standing, looking_at_viewer, "
    "simple_background, white_shirt, skirt, detailed_background, "
    "depth_of_field, cinematic_lighting, highres, sharp_focus, dynamic_angle, "
    "cowboy_shot, upper_body, closed_mouth, soft_light, portrait"
)

THIN_PARTY = (
    "3girls, multiple_girls, smile, outdoors, day, standing, "
    "looking_at_viewer, sportswear, simple_background, "
    "detailed_background, depth_of_field, cinematic_lighting, highres, "
    "sharp_focus, dynamic_angle, upper_body, closed_mouth, soft_light, "
    "portrait, blonde_hair, black_hair, silver_hair, blush"
)


def _print_richness(name: str, tag_line: str) -> dict:
    r = score_prompt_richness(tag_line)
    deg, reason = _chronicle_tags_degenerate(tag_line)
    print(f"\n── {name} ──")
    print(
        f"  richness={r['score']:.2f}  tags={r['tag_count']}  "
        f"light={r['lighting']} env={r['environment']} props={r['props']} "
        f"motion={r['motion']} atmos={r['atmosphere']} expr={r['expression']} "
        f"deg={deg}({reason or 'ok'})"
    )
    print(f"  hits: {r['hits']}")
    return r


def test_reference_bicycle_scene_is_rich():
    r = _print_richness("bicycle golden hour (reference-grade)", BICYCLE_GOLDEN_HOUR)
    assert r["score"] >= 0.75
    assert r["lighting"] >= 3
    assert r["environment"] >= 4
    assert r["props"] >= 2
    assert r["motion"] >= 3
    assert r["expression"] is True
    assert not _chronicle_tags_degenerate(BICYCLE_GOLDEN_HOUR)[0]


def test_reference_stadium_toast_is_rich():
    r = _print_richness("stadium toast (reference-grade)", STADIUM_TOAST)
    assert r["score"] >= 0.70
    assert r["expression"] is True
    assert r["atmosphere"] >= 1
    assert r["props"] >= 2
    assert r["environment"] >= 3
    assert not _chronicle_tags_degenerate(STADIUM_TOAST)[0]


def test_thin_prompts_fail_richness_floor():
    bike = _print_richness("thin bike (weak LLM)", THIN_BIKE)
    party = _print_richness("thin party (weak LLM)", THIN_PARTY)
    assert bike["score"] < 0.55
    assert party["score"] < 0.55
    # standing-heavy thin lines should trip action densify
    assert bike["motion"] <= 1 or _chronicle_tags_degenerate(THIN_BIKE)[0]


def test_pass1_prompt_demands_scene_richness():
    prompt = build_axis_tags_prompt(
        story_text=(
            "She pedals through the sunset street, scarf fluttering, looking back "
            "with a bright smile past the cafe windows."
        ),
        character_tags=["1girl", "black_hair", "brown_eyes"],
        character_desc="schoolgirl",
        axis="present",
        base_axis="present",
        time_scale="hours",
    )
    assert "SCENE RICHNESS" in prompt
    assert "lighting_tags" in prompt
    assert "rim_light" in prompt or "golden" in prompt.lower()
    assert "background_tags" in prompt
    assert "object_tags" in prompt


def test_full_quality_eval_rich_vs_thin_matrix(capsys):
    rich_stories = {
        "past": "She unlocks her bicycle by the cafe as the sun dips low.",
        "present": (
            "Pedaling hard down the street she looks back smiling, scarf "
            "fluttering in the golden rim light past the storefront."
        ),
        "future": (
            "At the stadium she and two teammates clink beer mugs, medals "
            "swinging, confetti raining under the bright sky."
        ),
    }
    rich_acts = {
        "past": "Unlocking a bicycle beside a cafe storefront at sunset.",
        "present": "Riding a bicycle on the street, leaning forward, looking back.",
        "future": "Clinking beer mugs in a stadium toast with teammates.",
    }
    rich_prompts = {
        "past": (
            "1girl, solo, black_hair, ponytail, smile, school_uniform, "
            "holding, bicycle, key, cafe, storefront, window, street, outdoors, "
            "sunset, warm_light, long_shadow, rim_light, streetlamp, "
            "detailed_background, depth_of_field, cinematic_lighting, "
            "cowboy_shot, red_scarf, brown_eyes, loafers, potted_plant, sky"
        ),
        "present": BICYCLE_GOLDEN_HOUR,
        "future": STADIUM_TOAST,
    }
    thin_prompts = {
        "past": THIN_BIKE,
        "present": THIN_BIKE,
        "future": THIN_PARTY,
    }

    rich_q = evaluate_chronicle_quality(
        user_topic="放課後の自転車と試合の祝い",
        title="Golden Ride",
        overall="From a sunset commute to a stadium victory toast.",
        stories=rich_stories,
        activities=rich_acts,
        prompts=rich_prompts,
        time_scale="hours",
        lock_tags=["black_hair", "brown_eyes"],
    )
    thin_q = evaluate_chronicle_quality(
        user_topic="放課後の自転車と試合の祝い",
        title="Smile",
        overall="She stands outside.",
        stories={
            "past": "She stands outside smiling.",
            "present": "She stands outside smiling.",
            "future": "They stand outside smiling.",
        },
        activities={
            "past": "Standing outside smiling.",
            "present": "Standing outside smiling.",
            "future": "Standing outside smiling.",
        },
        prompts=thin_prompts,
        time_scale="hours",
        lock_tags=["black_hair", "brown_eyes"],
    )

    print("\n═══ RICHNESS MATRIX (reference-grade vs thin) ═══")
    print(f"{'dim':14} {'rich':>6} {'thin':>6}")
    for d in rich_q["dimensions"]:
        print(
            f"{d:14} {rich_q['dimensions'][d]:6.2f} {thin_q['dimensions'][d]:6.2f}"
        )
    print(f"{'OVERALL':14} {rich_q['overall']:6.2f} {thin_q['overall']:6.2f}")

    assert rich_q["dimensions"]["richness"] >= 0.65
    assert thin_q["dimensions"]["richness"] < rich_q["dimensions"]["richness"] - 0.15
    assert rich_q["overall"] > thin_q["overall"]
    # Reference-grade path should clear the "can we paint that rich?" bar.
    assert rich_q["dimensions"]["expression"] >= 0.7
    assert rich_q["dimensions"]["action"] >= 0.6


def test_draft_refine_lifts_thin_vocab_to_reference_richness(capsys):
    """Phase B: thin text-search + rich draft WD14 → image-model expression wins."""
    from app.story.generator import (
        draft_richness_delta,
        merge_draft_wd14_tags,
        should_use_draft_refine,
    )

    # Auto now drafts hours/days — only minutes skip.
    assert should_use_draft_refine(
        mode="auto", time_scale="hours", divergence=0.1, workflow_name="w.json",
    )
    assert not should_use_draft_refine(
        mode="auto", time_scale="minutes", divergence=0.1, workflow_name="w.json",
    )

    thin_vocab = [
        "outdoors", "day", "street", "standing", "smile", "looking_at_viewer",
        "simple_background", "school_uniform",
    ]
    # What a strong image model paints into a low-res draft (WD14 read-back).
    draft_wd14 = [
        "riding_bicycle", "bicycle", "pedaling", "leaning_forward",
        "red_scarf", "fluttering_scarf", "wind",
        "storefront", "cafe", "window", "streetlamp", "potted_plant",
        "sunset", "golden_hour", "rim_light", "backlight", "lens_flare",
        "looking_back", "open_mouth", "blush",
    ]
    lock = ["black_hair", "brown_eyes", "1girl"]
    focal = ["riding_bicycle", "looking_back"]

    before = ", ".join(focal + thin_vocab + lock)
    merged = merge_draft_wd14_tags(
        vocab_tags=thin_vocab,
        draft_tags=draft_wd14,
        lock_tags=lock,
        focal=focal,
    )
    after = ", ".join(merged)
    delta = draft_richness_delta(before_tag_line=before, after_tag_line=after)

    print("\n═══ DRAFT GROUNDING LIFT (thin vocab → rich draft WD14) ═══")
    print(f"  before richness={delta['before']:.2f}")
    print(f"  after  richness={delta['after']:.2f}  Δ={delta['delta']:+.2f}")
    print(f"  lighting={delta['draft_lighting']} env={delta['draft_environment']} "
          f"props={delta['draft_props']}")
    print(f"  merged head: {', '.join(merged[:14])}…")

    assert "golden_hour" in merged
    assert "rim_light" in merged
    assert "storefront" in merged or "cafe" in merged
    assert "bicycle" in merged or "riding_bicycle" in merged
    # Richness tags sit ahead of leftover thin vocab (e.g. simple_background).
    assert merged.index("golden_hour") < merged.index("simple_background")
    assert delta["delta"] >= 0.25
    assert delta["after"] >= 0.55

    q = evaluate_chronicle_quality(
        user_topic="放課後の自転車",
        title="Draft Lift",
        overall="A thin guess becomes a painted street.",
        stories={
            "past": "She unlocks the bike by the cafe.",
            "present": "She pedals into the sunset looking back.",
            "future": "She arrives home as streetlamps flicker on.",
        },
        activities={
            "past": "Unlocking a bicycle beside a cafe.",
            "present": "Riding a bicycle on the street at sunset.",
            "future": "Parking the bicycle under a streetlamp.",
        },
        prompts={
            "past": after,
            "present": after,
            "future": after,
        },
        time_scale="hours",
        lock_tags=lock,
        draft_deltas={"present": delta},
    )
    assert q["notes"].get("draft_grounding")
    assert q["draft_grounding"]["mean_delta"] == delta["delta"]
    assert q["dimensions"]["richness"] >= 0.50
    print(f"  quality richness={q['dimensions']['richness']:.2f} "
          f"note={q['notes']['draft_grounding']}")
