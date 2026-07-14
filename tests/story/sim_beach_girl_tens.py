"""Detailed Chronicle simulation: 海辺で遊ぶ少女 / tens_of_minutes.

No live LLM/Comfy — models a strong Pass-1 + Visual Script path through real
gates, tag merge, optional draft grounding, and quality_eval.

Run:
  PYTHONPATH=backend python3 -m pytest tests/story/sim_beach_girl_tens.py -q -s
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
    build_draft_grounding_block,
    candidates_degenerate,
    candidates_off_topic,
    candidates_ungrounded,
    draft_richness_delta,
    identity_lock_tags,
    infer_axis_scene_constraints,
    merge_chronicle_axis_tags,
    merge_draft_wd14_tags,
    should_differentiate_acts,
    should_use_draft_refine,
    topic_anchor_tokens,
)
from app.story.quality import evaluate_chronicle_quality, score_prompt_richness

# ── Scenario ─────────────────────────────────────────────────────────────────

TOPIC = "海辺で遊ぶ少女"
TIME_SCALE = "tens_of_minutes"
DIVERGENCE = 0.35  # slight micro-variation within one beach session
WORKFLOW = "chronicle_sim.json"

BASE_WD14 = [
    "1girl", "solo", "brown_hair", "long_hair", "hair_ribbon", "brown_eyes",
    "white_dress", "barefoot", "outdoors", "day", "smile",
]
LOCK = identity_lock_tags(BASE_WD14)

CANDIDATES = [
    {
        "id": "A",
        "title": "Tide Chase",
        "past": "On the beach she chases the retreating foam along the wet sand, laughing.",
        "present": "At the seaside she kicks a splash of seawater toward the horizon, dress hem dripping.",
        "future": "Still on the beach she crouches to pick up a spiral shell the wave just left.",
        "motif": "foam",
        "turn": "chase becomes collect",
        "grounded_tags": ["ocean", "beach", "barefoot", "white_dress"],
    },
    {
        "id": "B",
        "title": "Shell Pocket",
        "past": "On the beach she digs a shallow pit in the sand with both palms.",
        "present": "At the seaside she drops collected shells into the hem of her dress like a pouch.",
        "future": "On the beach she rinses sand from a shell under a clear wave.",
        "motif": "shell",
        "turn": "dig becomes rinse",
        "grounded_tags": ["sand", "shell", "ocean", "beach"],
    },
    {
        "id": "C",
        "title": "Wind Run",
        "past": "On the beach she stretches both arms wide into the sea wind on the shore.",
        "present": "Along the seaside she runs along the waterline, ribbon fluttering behind her.",
        "future": "On the beach she slows and looks back at her footprints filling with water.",
        "motif": "wind",
        "turn": "run becomes look back",
        "grounded_tags": ["ocean", "beach", "wind", "hair_ribbon"],
    },
]

SEED_TAGS = ["ocean", "beach", "sand", "barefoot", "white_dress", "shell"]

STORIES = {
    "past": (
        "Ten-odd minutes ago on the beach she was already barefoot on the wet sand, "
        "chasing the retreating foam with open-mouthed laughter, brown hair ribbon "
        "whipping in the sea wind under a bright midday sky by the seaside."
    ),
    "present": (
        "Now at the seaside she kicks a glittering splash toward the horizon; seawater "
        "beads on her white dress hem as she leans forward, smiling hard, sunlight "
        "sparkling on the beach waves behind her."
    ),
    "future": (
        "A short while later still on the beach she crouches at the waterline, picking "
        "up a spiral shell the wave just left, holding it close with both hands, sand "
        "still clinging to her calves at the seaside."
    ),
}

ACTIVITIES = {
    "past": "On the beach, chasing retreating foam along the wet sand, running barefoot.",
    "present": "At the seaside, kicking a splash of seawater toward the horizon, leaning forward.",
    "future": "On the beach, crouching at the waterline and picking up a spiral shell with both hands.",
}

SLOTS = {
    "past": {"place": "wet sand shoreline", "activity": "chases foam", "feeling": "playful"},
    "present": {"place": "shallow surf", "activity": "kicks splash", "feeling": "bright"},
    "future": {"place": "waterline", "activity": "picks shell", "feeling": "curious"},
}

# Pass-1 style axis builds (strong path — what we want the pipeline to emit)
AXIS_BUILD = {
    "past": {
        "focal": ["running", "chasing", "open_mouth", "laughing", "looking_ahead"],
        "search": [
            "ocean", "beach", "wet_sand", "foam", "wave", "outdoors", "daylight",
            "barefoot", "white_dress", "hair_ribbon", "wind", "dynamic_pose",
            "midday", "sparkle", "horizon",
        ],
    },
    "present": {
        "focal": ["kicking", "leaning_forward", "smile", "looking_at_viewer", "splash"],
        "search": [
            "ocean", "beach", "seawater", "splash", "wave", "outdoors", "sunlight",
            "barefoot", "white_dress", "wet_clothes", "lens_flare", "sparkle",
            "horizon", "summer", "bright",
        ],
    },
    "future": {
        "focal": ["crouching", "holding", "looking_down", "curious", "blush"],
        "search": [
            "ocean", "beach", "shell", "waterline", "sand", "outdoors", "day",
            "barefoot", "white_dress", "both_hands", "close-up", "wet_sand",
            "soft_light", "wave",
        ],
    },
}

# Image-model draft WD14 (what a beach checkpoint might paint) — used only if draft on
DRAFT_WD14 = {
    "past": [
        "running", "dynamic_pose", "wind", "ocean", "beach", "wet_sand", "foam",
        "wave", "daylight", "sparkle", "hair_ribbon", "barefoot", "open_mouth",
        "laughing", "outdoors", "horizon",
    ],
    "present": [
        "kicking", "splash", "seawater", "ocean", "beach", "sunlight", "lens_flare",
        "sparkle", "leaning_forward", "smile", "wet_clothes", "wave", "summer",
        "bright", "barefoot",
    ],
    "future": [
        "crouching", "shell", "holding", "ocean", "beach", "sand", "waterline",
        "wet_sand", "wave", "looking_down", "both_hands", "soft_light", "barefoot",
        "blush",
    ],
}

PROSE = {
    "past": (
        "A barefoot girl (1girl, solo, brown_hair, long_hair, hair_ribbon, "
        "brown_eyes) races along wet sand (beach, ocean, wet_sand), chasing "
        "white foam left by a retreating wave (foam, wave, chasing, running). "
        "Her white dress flutters; mouth open in laughter (open_mouth, laughing) "
        "as midday light sparkles on the water (daylight, sparkle, outdoors). "
        "Sea wind pulls her ribbon sideways (wind, dynamic_pose). Mid shot from "
        "a low beach angle, horizon bright behind her."
    ),
    "present": (
        "She kicks hard into shallow surf (kicking, splash, seawater), leaning "
        "forward with a bright smile toward the viewer (leaning_forward, smile, "
        "looking_at_viewer). Droplets catch sunlight in a short arc (sunlight, "
        "lens_flare, sparkle) while waves glitter behind her (ocean, beach, wave). "
        "The hem of her white dress is wet (white_dress, wet_clothes, barefoot). "
        "Summer brightness, cowboy shot, cinematic rim from the water."
    ),
    "future": (
        "Minutes later she crouches at the waterline (crouching, waterline, sand), "
        "both hands lifting a spiral shell just washed ashore (holding, shell, "
        "both_hands, looking_down). Curiosity softens her face (curious, blush). "
        "Wet sand clings to her calves; a gentle wave laps near her toes "
        "(wet_sand, wave, ocean, beach, barefoot). Soft daylight close-up, "
        "shallow depth of field on the shell."
    ),
}


def _assemble(axis: str, *, use_draft: bool) -> tuple[str, str, dict]:
    build = AXIS_BUILD[axis]
    constraints = infer_axis_scene_constraints(STORIES[axis])
    search = list(build["search"])
    if use_draft:
        search = merge_draft_wd14_tags(
            vocab_tags=search,
            draft_tags=DRAFT_WD14[axis],
            lock_tags=LOCK,
            focal=build["focal"],
        )
    # scene constraints may forbid mismatches
    from app.story.generator import apply_scene_constraints
    search = apply_scene_constraints(search, constraints)
    tag_line = merge_chronicle_axis_tags(
        focal=build["focal"], search_tags=search, lock_tags=LOCK,
    )
    # densify-ish pad to ~32 content tags
    parts = [t.strip() for t in tag_line.split(",") if t.strip()]
    pad = [
        "detailed_background", "depth_of_field", "cinematic_lighting",
        "highres", "summer", "from_side", "cowboy_shot",
    ]
    seen = {p.lower() for p in parts}
    for p in pad:
        if p.lower() not in seen and len(parts) < 34:
            parts.append(p)
            seen.add(p.lower())
    tag_line = ", ".join(parts)
    positive = f"{tag_line}\n\n{PROSE[axis]}"
    grounding = build_draft_grounding_block(DRAFT_WD14[axis]) if use_draft else ""
    meta = {
        "tag_line": tag_line,
        "grounding_preview": grounding[:160] + ("…" if grounding else ""),
        "richness": score_prompt_richness(positive),
    }
    return positive, "blurry, lowres, bad anatomy, static_pose, expressionless", meta


def _print_full(title: str, prompts: dict[str, dict], q: dict, gates: dict) -> None:
    print("\n" + "═" * 72)
    print(f"  SIM: {title}")
    print(f"  お題: {TOPIC}")
    print(f"  時間尺度: {TIME_SCALE}（前後数十分）  divergence={DIVERGENCE}")
    print("═" * 72)
    print("\n── Gates ──")
    for k, v in gates.items():
        print(f"  {k}: {v}")
    print("\n── Topic anchors ──")
    print(f"  {topic_anchor_tokens(TOPIC)}")
    print("\n── Quality radar ──")
    dims = q["dimensions"]
    for d, v in dims.items():
        bar = "█" * int(round(v * 10)) + "░" * (10 - int(round(v * 10)))
        print(f"  {d:12} {v:5.2f}  {bar}")
    print(f"  {'OVERALL':12} {q['overall']:5.2f}")
    if q.get("notes"):
        for nk, nv in q["notes"].items():
            print(f"  note[{nk}]: {nv}")
    if q.get("draft_grounding"):
        print(f"  draft_grounding: {q['draft_grounding']}")

    print("\n── Final prompts (POSITIVE) ──")
    for axis in AXES:
        p = prompts[axis]["positive"]
        n = prompts[axis]["negative"]
        r = score_prompt_richness(p)
        print(f"\n[{axis.upper()}]  richness={r['score']:.2f}  "
              f"light={r['lighting']} env={r['environment']} props={r['props']} "
              f"motion={r['motion']} expr={r['expression']}")
        print("POSITIVE:")
        print(p)
        print("NEGATIVE:")
        print(n)


def test_sim_beach_girl_tens_of_minutes(capsys):
    # ── candidate / gate layer ──
    draft_auto = should_use_draft_refine(
        mode="auto",
        time_scale=TIME_SCALE,
        divergence=DIVERGENCE,
        workflow_name=WORKFLOW,
    )
    # tens_of_minutes is micro → auto draft OFF even at 0.35 (< needs 0.25? wait 0.35 >= 0.25)
    # Actually divergence 0.35 >= 0.25 → draft SHOULD be True with new logic!
    # Check: if div >= 0.25 return True even for skip scales.
    draft_auto_low_div = should_use_draft_refine(
        mode="auto",
        time_scale=TIME_SCALE,
        divergence=0.15,
        workflow_name=WORKFLOW,
    )

    gates = {
        "candidates_degenerate": candidates_degenerate(CANDIDATES),
        "candidates_ungrounded": candidates_ungrounded(
            CANDIDATES, seed_tags=SEED_TAGS
        ),
        "candidates_off_topic": candidates_off_topic(
            CANDIDATES, user_topic=TOPIC
        ),
        "acts_temporally_distinct": acts_temporally_distinct(STORIES),
        "activities_temporally_distinct": activities_temporally_distinct(ACTIVITIES),
        "axis_slots_collapsed": axis_slots_collapsed(SLOTS),
        "should_differentiate_acts": should_differentiate_acts(TIME_SCALE),
        "draft_refine_auto_div0.35": draft_auto,
        "draft_refine_auto_div0.15": draft_auto_low_div,
    }

    # Path A: realistic auto (this divergence 0.35 → draft ON despite micro scale)
    prompts_a: dict[str, dict] = {}
    deltas_a: dict[str, dict] = {}
    for axis in AXES:
        before_tags = merge_chronicle_axis_tags(
            focal=AXIS_BUILD[axis]["focal"],
            search_tags=AXIS_BUILD[axis]["search"],
            lock_tags=LOCK,
        )
        pos, neg, meta = _assemble(axis, use_draft=draft_auto)
        prompts_a[axis] = {"positive": pos, "negative": neg}
        if draft_auto:
            deltas_a[axis] = draft_richness_delta(
                before_tag_line=before_tags,
                after_tag_line=meta["tag_line"],
            )

    tag_only = {
        a: (prompts_a[a]["positive"].split("\n\n")[0]) for a in AXES
    }
    gates["axis_tag_lines_collapsed"] = axis_tag_lines_collapsed(tag_only)

    q_a = evaluate_chronicle_quality(
        user_topic=TOPIC,
        title="Tide Chase",
        overall=(
            "Across a few dozen minutes on the same shore, a barefoot girl "
            "moves from chasing foam to kicking spray to collecting a shell."
        ),
        stories=STORIES,
        activities=ACTIVITIES,
        prompts=prompts_a,
        time_scale=TIME_SCALE,
        lock_tags=LOCK,
        draft_deltas=deltas_a or None,
    )
    _print_full(
        "Path A — Auto draft (divergence=0.35)",
        prompts_a, q_a, gates,
    )

    # Path B: forced Off (micro session without draft) for comparison
    prompts_b: dict[str, dict] = {}
    for axis in AXES:
        pos, neg, _ = _assemble(axis, use_draft=False)
        prompts_b[axis] = {"positive": pos, "negative": neg}
    q_b = evaluate_chronicle_quality(
        user_topic=TOPIC,
        title="Tide Chase",
        overall=q_a.get("notes", {}).get("topic_fit", "") and (
            "Across a few dozen minutes on the same shore, a barefoot girl "
            "moves from chasing foam to kicking spray to collecting a shell."
        ) or (
            "Across a few dozen minutes on the same shore, a barefoot girl "
            "moves from chasing foam to kicking spray to collecting a shell."
        ),
        stories=STORIES,
        activities=ACTIVITIES,
        prompts=prompts_b,
        time_scale=TIME_SCALE,
        lock_tags=LOCK,
    )
    print("\n── Comparison: draft Off vs Auto ──")
    print(f"  {'dim':12} {'auto':>6} {'off':>6}")
    for d in q_a["dimensions"]:
        print(f"  {d:12} {q_a['dimensions'][d]:6.2f} {q_b['dimensions'][d]:6.2f}")
    print(f"  {'OVERALL':12} {q_a['overall']:6.2f} {q_b['overall']:6.2f}")
    if deltas_a:
        print("\n── Draft richness Δ (per axis) ──")
        for axis, d in deltas_a.items():
            print(
                f"  [{axis}] {d['before']:.2f} → {d['after']:.2f} "
                f"(Δ={d['delta']:+.2f})"
            )

    # Soft asserts — this is a sim report, but keep floors honest
    assert not gates["candidates_degenerate"]
    assert not gates["candidates_off_topic"]
    assert gates["should_differentiate_acts"] is False  # micro scale
    assert gates["draft_refine_auto_div0.15"] is False
    assert gates["draft_refine_auto_div0.35"] is True  # div≥0.25 lifts micro
    assert q_a["dimensions"]["topic_fit"] >= 0.55
    assert q_a["dimensions"]["expression"] >= 0.5
    assert q_a["dimensions"]["richness"] >= 0.45
    assert q_a["overall"] >= 0.60
    for axis in AXES:
        blob = prompts_a[axis]["positive"].lower()
        assert "ocean" in blob or "beach" in blob
        assert any(x in blob for x in ("smile", "laughing", "open_mouth", "blush"))
