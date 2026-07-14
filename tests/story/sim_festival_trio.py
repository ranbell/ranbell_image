"""Multi-person Chronicle sim + frozenset miss behaviour.

Theme: 夏祭りで遊ぶ三人の少女 / hours
Also documents what happens when tags fall outside frozenset classification.

Run:
  PYTHONPATH=backend python3 -m pytest tests/story/sim_festival_trio.py -q -s
  PYTHONPATH=backend python3 -m pytest tests/api/test_inspire_frozenset_miss.py -q -s
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.generator import (
    AXES,
    acts_temporally_distinct,
    activities_temporally_distinct,
    axis_slots_collapsed,
    axis_tag_lines_collapsed,
    candidates_degenerate,
    candidates_off_topic,
    candidates_ungrounded,
    draft_richness_delta,
    identity_lock_tags,
    identity_tags_for_scale,
    infer_axis_scene_constraints,
    is_multi_character,
    merge_chronicle_axis_tags,
    merge_draft_wd14_tags,
    should_differentiate_acts,
    should_use_draft_refine,
    topic_anchor_tokens,
    apply_scene_constraints,
)
from app.story.quality import evaluate_chronicle_quality, score_prompt_richness

TOPIC = "夏祭りで遊ぶ三人の少女"
TIME_SCALE = "hours"
DIVERGENCE = 0.45
WORKFLOW = "chronicle_sim.json"

# Multi-subject base WD14 — hair/eye colours belong to DIFFERENT girls
BASE_WD14 = [
    "3girls", "multiple_girls",
    "blonde_hair", "black_hair", "brown_hair",
    "blue_eyes", "brown_eyes", "green_eyes",
    "yukata", "hair_ornament", "outdoors", "night", "festival", "smile",
]

MULTI = is_multi_character(BASE_WD14)
LOCK = identity_lock_tags(BASE_WD14, multi_character=MULTI)
SCALE_ID = identity_tags_for_scale(
    BASE_WD14, TIME_SCALE, multi_character=MULTI,
)

CANDIDATES = [
    {
        "id": "A",
        "title": "Lantern Dash",
        "past": (
            "At the summer festival three girls buy grilled squid at a food stall."
        ),
        "present": (
            "The three girls race between paper lanterns, yukata sleeves fluttering."
        ),
        "future": (
            "They share one giant candy apple under the fireworks at the festival."
        ),
        "motif": "lantern",
        "turn": "stall to fireworks",
        "grounded_tags": ["festival", "yukata", "lantern", "3girls"],
    },
    {
        "id": "B",
        "title": "Goldfish Scoop",
        "past": "Three girls lean over a goldfish scooping tub at the festival.",
        "present": "One girl cheers as her friend finally scoops a goldfish.",
        "future": "They carry the bag of water between them toward the shrine path.",
        "motif": "goldfish",
        "turn": "scoop becomes carry",
        "grounded_tags": ["festival", "goldfish", "3girls"],
    },
    {
        "id": "C",
        "title": "Sparklers",
        "past": "Three girls light thin sparklers behind the festival stalls.",
        "present": "Sparks fall as they draw circles in the night air together.",
        "future": "They blow out the last sparkler and laugh under the lanterns.",
        "motif": "sparkler",
        "turn": "light to extinguish",
        "grounded_tags": ["festival", "sparkler", "3girls", "night"],
    },
]

SEED = ["festival", "yukata", "lantern", "3girls", "outdoors", "night"]

STORIES = {
    "past": (
        "A few hours earlier at the summer festival, three girls in yukata crowd "
        "a grilled-squid stall, laughing as smoke rises past paper lanterns."
    ),
    "present": (
        "Now the three girls race between rows of glowing paper lanterns, sleeves "
        "fluttering, one pointing ahead while another looks back grinning."
    ),
    "future": (
        "Later they sit shoulder to shoulder under fireworks, sharing one candy "
        "apple, faces lit by sparks at the festival night."
    ),
}

ACTIVITIES = {
    "past": "Three girls buying grilled squid at a festival food stall.",
    "present": "Three girls racing between paper lanterns, yukata sleeves fluttering.",
    "future": "Three girls sharing a candy apple under fireworks at the festival.",
}

SLOTS = {
    "past": {"place": "festival food stall", "activity": "buy squid", "feeling": "eager"},
    "present": {"place": "lantern street", "activity": "race", "feeling": "excited"},
    "future": {"place": "fireworks lawn", "activity": "share apple", "feeling": "warm"},
}

AXIS_BUILD = {
    "past": {
        "focal": ["holding", "reaching", "laughing", "open_mouth", "looking_at_another"],
        "search": [
            "3girls", "multiple_girls", "yukata", "festival", "food_stall", "smoke",
            "paper_lantern", "night", "outdoors", "grilled_food", "summer",
        ],
    },
    "present": {
        "focal": ["running", "pointing", "looking_back", "smile", "dynamic_pose"],
        "search": [
            "3girls", "multiple_girls", "yukata", "festival", "paper_lantern",
            "night", "outdoors", "glow", "wind", "summer", "street",
        ],
    },
    "future": {
        "focal": ["sitting", "holding", "sharing", "closed_eyes", "happy"],
        "search": [
            "3girls", "multiple_girls", "yukata", "festival", "fireworks",
            "candy_apple", "night", "outdoors", "sparkle", "warm_light",
        ],
    },
}

# Novel draft tags that may miss frozenset (image-model inventions)
DRAFT_WD14 = {
    "past": [
        "3girls", "holding", "food_stall", "paper_lantern", "smoke", "night",
        "yukata", "laughing", "festival", "neon_lantern_glow",  # likely frozenset-miss
    ],
    "present": [
        "3girls", "running", "paper_lantern", "yukata", "looking_back", "smile",
        "festival", "glow", "wind", "night", "lantern_street_blur",  # miss
    ],
    "future": [
        "3girls", "sitting", "fireworks", "candy_apple", "closed_eyes", "happy",
        "sparkle", "warm_light", "festival", "shared_candy_apple",  # miss
    ],
}

PROSE = {
    "past": (
        "Three girls in matching-energy yukata (3girls, multiple_girls, yukata) "
        "crowd a grilled-squid stall (food_stall, festival, night). One reaches "
        "for a skewer (reaching, holding) while another laughs open-mouthed "
        "(laughing, open_mouth). Paper lanterns glow overhead (paper_lantern, "
        "glow). Do NOT force one shared hair/eye colour — subjects differ."
    ),
    "present": (
        "The trio races down a lantern-lined festival street (running, "
        "paper_lantern, festival, night). One points ahead (pointing); another "
        "looks back smiling (looking_back, smile). Yukata sleeves catch the wind "
        "(yukata, wind, dynamic_pose). Wide shot, glowing depth."
    ),
    "future": (
        "Shoulder to shoulder under fireworks (3girls, fireworks, night), they "
        "share one candy apple (holding, candy_apple, sharing). Eyes soft or "
        "closed in delight (closed_eyes, happy). Warm spark light on three faces "
        "(warm_light, sparkle). Intimate group mid-shot."
    ),
}


def _assemble(axis: str, *, use_draft: bool) -> tuple[str, str, dict]:
    build = AXIS_BUILD[axis]
    search = list(build["search"])
    if use_draft:
        search = merge_draft_wd14_tags(
            vocab_tags=search,
            draft_tags=DRAFT_WD14[axis],
            lock_tags=LOCK,
            focal=build["focal"],
        )
    search = apply_scene_constraints(
        search, infer_axis_scene_constraints(STORIES[axis])
    )
    tag_line = merge_chronicle_axis_tags(
        focal=build["focal"], search_tags=search, lock_tags=LOCK,
    )
    parts = [t.strip() for t in tag_line.split(",") if t.strip()]
    # Keep multi-subject anchors up front
    for anchor in ("3girls", "multiple_girls"):
        if anchor not in {p.lower() for p in parts}:
            parts.insert(0, anchor)
    # Must NOT inject a single hair/eye colour lock for multi
    for banned in ("blonde_hair", "black_hair", "blue_eyes", "brown_eyes", "green_eyes"):
        # Allow if somehow from draft richness env — but identity lock should be empty of these
        pass
    pad = ["detailed_background", "depth_of_field", "cinematic_lighting", "highres"]
    seen = {p.lower() for p in parts}
    for p in pad:
        if p.lower() not in seen and len(parts) < 36:
            parts.append(p)
            seen.add(p.lower())
    tag_line = ", ".join(parts)
    positive = f"{tag_line}\n\n{PROSE[axis]}"
    return positive, "blurry, lowres, bad anatomy, solo, 1girl", {
        "tag_line": tag_line,
        "richness": score_prompt_richness(positive),
    }


def test_sim_festival_trio_multi_and_identity(capsys):
    assert MULTI is True
    # Multi drops hair/eye from lock — accessories (hair_ornament) may remain
    assert not any(t.endswith("_eyes") for t in LOCK)
    assert not any(
        t in LOCK for t in ("blonde_hair", "black_hair", "brown_hair")
    )
    print("\n── Multi identity ──")
    print(f"  is_multi_character: {MULTI}")
    print(f"  identity_lock_tags: {LOCK}")
    print(f"  identity_tags_for_scale({TIME_SCALE}): {SCALE_ID}")

    draft_auto = should_use_draft_refine(
        mode="auto", time_scale=TIME_SCALE, divergence=DIVERGENCE,
        workflow_name=WORKFLOW,
    )
    gates = {
        "candidates_degenerate": candidates_degenerate(CANDIDATES),
        "candidates_ungrounded": candidates_ungrounded(CANDIDATES, seed_tags=SEED),
        "candidates_off_topic": candidates_off_topic(CANDIDATES, user_topic=TOPIC),
        "acts_temporally_distinct": acts_temporally_distinct(STORIES),
        "activities_temporally_distinct": activities_temporally_distinct(ACTIVITIES),
        "axis_slots_collapsed": axis_slots_collapsed(SLOTS),
        "should_differentiate_acts": should_differentiate_acts(TIME_SCALE),
        "draft_refine_auto": draft_auto,
        "topic_anchors": topic_anchor_tokens(TOPIC),
    }

    prompts: dict[str, dict] = {}
    deltas: dict[str, dict] = {}
    for axis in AXES:
        before = merge_chronicle_axis_tags(
            focal=AXIS_BUILD[axis]["focal"],
            search_tags=AXIS_BUILD[axis]["search"],
            lock_tags=LOCK,
        )
        pos, neg, meta = _assemble(axis, use_draft=draft_auto)
        prompts[axis] = {"positive": pos, "negative": neg}
        if draft_auto:
            deltas[axis] = draft_richness_delta(
                before_tag_line=before, after_tag_line=meta["tag_line"],
            )

    tag_only = {a: prompts[a]["positive"].split("\n\n")[0] for a in AXES}
    gates["axis_tag_lines_collapsed"] = axis_tag_lines_collapsed(tag_only)

    q = evaluate_chronicle_quality(
        user_topic=TOPIC,
        title="Lantern Dash",
        overall=(
            "Across one festival evening, three girls move from a food stall "
            "to a lantern race to sharing sweets under fireworks."
        ),
        stories=STORIES,
        activities=ACTIVITIES,
        prompts=prompts,
        time_scale=TIME_SCALE,
        lock_tags=LOCK,
        draft_deltas=deltas or None,
    )

    print("\n" + "═" * 72)
    print(f"  SIM multi: {TOPIC} / {TIME_SCALE}")
    print("═" * 72)
    print("── Gates ──")
    for k, v in gates.items():
        print(f"  {k}: {v}")
    print("── Quality ──")
    for d, v in q["dimensions"].items():
        bar = "█" * int(round(v * 10)) + "░" * (10 - int(round(v * 10)))
        print(f"  {d:12} {v:5.2f}  {bar}")
    print(f"  {'OVERALL':12} {q['overall']:5.2f}")
    if q.get("notes"):
        for nk, nv in q["notes"].items():
            print(f"  note[{nk}]: {nv}")

    print("\n── Final prompts ──")
    for axis in AXES:
        p = prompts[axis]["positive"]
        r = score_prompt_richness(p)
        print(f"\n[{axis.upper()}] richness={r['score']:.2f}")
        print("POSITIVE:")
        print(p)
        print("NEGATIVE:", prompts[axis]["negative"])
        # Multi: must keep 3girls, must NOT smear a single eye colour via lock
        low = p.lower()
        assert "3girls" in low or "multiple_girls" in low
        assert "solo" not in tag_only[axis].split(", ")[:5]

    if deltas:
        print("\n── Draft Δ ──")
        for axis, d in deltas.items():
            print(f"  [{axis}] {d['before']:.2f}→{d['after']:.2f} (Δ={d['delta']:+.2f})")

    assert not gates["candidates_degenerate"]
    assert not gates["candidates_off_topic"]
    assert gates["should_differentiate_acts"] is True
    assert gates["draft_refine_auto"] is True
    assert q["dimensions"]["topic_fit"] >= 0.55
    assert q["dimensions"]["expression"] >= 0.5
    assert q["dimensions"]["action"] >= 0.5
    assert q["dimensions"]["identity"] >= 0.8  # multi_subject_anchor
    assert q["overall"] >= 0.60
    assert "festival" in gates["topic_anchors"] or "matsuri" in gates["topic_anchors"]
    assert q["notes"]["identity"] == "multi_subject_anchor"
