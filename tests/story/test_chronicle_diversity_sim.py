"""End-to-end Chronicle diversity / concreteness simulation (no LLM, no Comfy).

Walks canned Stage outputs through the *real* gates and tag-assembly helpers,
then scores whether past / present / future stay diverse and drawable.

Run:
  PYTHONPATH=backend python3 -m pytest tests/story/test_chronicle_diversity_sim.py -q -s
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.generator import (
    AXES,
    _BEAT_SIMILAR_THRESHOLD,
    _mean_pairwise_similarity,
    activities_temporally_distinct,
    acts_temporally_distinct,
    apply_scene_constraints,
    assign_dramatic_modes,
    axis_slots_collapsed,
    axis_tag_lines_collapsed,
    candidates_degenerate,
    candidates_off_topic,
    candidates_ungrounded,
    identity_lock_tags,
    infer_axis_scene_constraints,
    merge_chronicle_axis_tags,
    should_differentiate_acts,
    should_use_draft_refine,
    topic_anchor_tokens,
    _chronicle_tags_degenerate,
)

# ── Scoring helpers (simulation metrics) ──────────────────────────────────────

_IDLE_RE = re.compile(
    r"\b(standing|sitting|posing|gazing|looking|staring|lounging|"
    r"thinking|relaxing|waiting)\b",
    re.I,
)
_ACTION_RE = re.compile(
    r"\b("
    r"reach|grab|pour|writ|run|knead|open|clos|hold|throw|climb|"
    r"push|pull|wip|stir|ty|cut|paint|typ|fold|wav|catch|kick|"
    r"tip|spill|point|teach|slid|lift|tamp|unlock|lock|shap|"
    r"guid|soak|train|stack"
    r")\w*",
    re.I,
)
_PLACE_RE = re.compile(
    r"\b(cafe|kitchen|station|platform|park|rooftop|classroom|bedroom|"
    r"street|alley|library|bridge|harbor|train|bus|shop|counter|"
    r"board|bar|machine|steamer)\b",
    re.I,
)


def _tag_set(tag_line: str) -> set[str]:
    return {t.strip().lower() for t in tag_line.split(",") if t.strip()}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _idle_ratio(text: str) -> float:
    words = re.findall(r"[A-Za-z0-9_]+", text or "")
    if not words:
        return 1.0
    idle = sum(1 for w in words if _IDLE_RE.fullmatch(w))
    return idle / len(words)


def _has_action(text: str) -> bool:
    return bool(_ACTION_RE.search(text or ""))


def _has_place(text: str) -> bool:
    return bool(_PLACE_RE.search(text or ""))


def score_axis_bundle(
    stories: dict[str, str], activities: dict[str, str], prompts: dict[str, str]
) -> dict:
    story_beats = [stories.get(a, "") for a in AXES]
    act_beats = [activities.get(a, "") for a in AXES]
    tag_sets = {a: _tag_set(prompts.get(a, "")) for a in AXES}

    pair_tag = {}
    for i, a in enumerate(AXES):
        for b in AXES[i + 1:]:
            pair_tag[f"{a}|{b}"] = round(_jaccard(tag_sets[a], tag_sets[b]), 3)

    return {
        "story_mean_sim": round(_mean_pairwise_similarity(story_beats), 3),
        "activity_mean_sim": round(_mean_pairwise_similarity(act_beats), 3),
        "acts_distinct": acts_temporally_distinct(stories),
        "activities_distinct": activities_temporally_distinct(activities),
        "tag_lines_collapsed": axis_tag_lines_collapsed(prompts),
        "tag_pair_jaccard": pair_tag,
        "max_tag_jaccard": max(pair_tag.values()) if pair_tag else 0.0,
        "per_axis": {
            a: {
                "story_idle_ratio": round(_idle_ratio(stories.get(a, "")), 3),
                "activity_has_action": _has_action(activities.get(a, "")),
                "activity_has_place": _has_place(activities.get(a, "")),
                "tag_count": len(tag_sets[a]),
                "tag_degenerate": _chronicle_tags_degenerate(prompts.get(a, ""))[0],
                "tag_deg_reason": _chronicle_tags_degenerate(prompts.get(a, ""))[1],
                "tag_idle_hits": len(_IDLE_RE.findall(prompts.get(a, ""))),
            }
            for a in AXES
        },
    }


def assemble_axis_prompt(
    *,
    story: str,
    focal: list[str],
    search_tags: list[str],
    lock_tags: list[str],
    filler_to: int = 28,
) -> str:
    """Simulate per-axis tag merge + scene constraints (+ pad like Pass-1 densify)."""
    constraints = infer_axis_scene_constraints(story)
    merged = merge_chronicle_axis_tags(
        focal=focal, search_tags=search_tags, lock_tags=lock_tags
    )
    parts = [t.strip() for t in merged.split(",") if t.strip()]
    parts = apply_scene_constraints(parts, constraints)
    pad = [
        "solo", "looking_at_viewer", "detailed_background", "depth_of_field",
        "cinematic_lighting", "highres", "sharp_focus", "dynamic_angle",
    ]
    i = 0
    seen = {p.lower() for p in parts}
    while len(parts) < filler_to and i < 40:
        cand = pad[i % len(pad)] if i < len(pad) else f"detail_{i}"
        if cand.lower() not in seen:
            parts.append(cand)
            seen.add(cand.lower())
        i += 1
    if not ({p.lower() for p in parts[:5]} & {"1girl", "1boy", "solo"}):
        parts = ["1girl", *parts]
    return ", ".join(parts)


# ── Scenarios ────────────────────────────────────────────────────────────────

BASE_WD14 = [
    "1girl", "solo", "silver_hair", "blue_eyes", "hair_ornament",
    "white_shirt", "apron", "cafe", "indoors", "day", "counter", "smile",
]
LOCK = identity_lock_tags(BASE_WD14)

SCENARIOS = {
    "good_years_cafe": {
        "time_scale": "years",
        "divergence": 0.55,
        "user_topic": "この子がカフェで働く話",
        "candidates": [
            {
                "id": "A",
                "title": "First Pour",
                "past": "As a trainee she fumbles the milk pitcher and soaks her apron at the cafe.",
                "present": "She pours latte art for a regular at the sunlit cafe counter.",
                "future": "Years later she trains a junior while wiping the espresso machine.",
                "motif": "milk_foam",
                "turn": "spilled milk becomes her signature pour",
                "grounded_tags": ["apron", "coffee_cup", "counter"],
            },
            {
                "id": "B",
                "title": "Closing Bell",
                "past": "She stacks chairs after midnight cafe practice shifts.",
                "present": "She locks the cafe door and pockets the spare key.",
                "future": "She opens her own tiny coffee shop two blocks away.",
                "motif": "key",
                "turn": "closing duty becomes ownership",
                "grounded_tags": ["key", "counter", "apron"],
            },
            {
                "id": "C",
                "title": "Rainy Regular",
                "past": "She forgets an umbrella and dashes under the cafe awning.",
                "present": "She hands a towel to a soaked customer by the cafe door.",
                "future": "She pins a thank-you note from that customer on the board.",
                "motif": "umbrella",
                "turn": "shared rain becomes loyalty",
                "grounded_tags": ["umbrella", "coffee_cup", "counter"],
            },
        ],
        "seed_tags": ["apron", "coffee_cup", "counter", "key", "umbrella"],
        "selected": "A",
        "activities": {
            "past": "Clumsily tipping a metal milk pitcher over the counter, soaking her apron.",
            "present": "Pouring a heart latte into a ceramic cup at the sunlit cafe counter.",
            "future": "Wiping the espresso machine at the cafe bar while pointing a junior toward the steamer.",
        },
        "axis_slots": {
            "past": {"place": "cafe counter", "activity": "spills milk pitcher"},
            "present": {"place": "cafe counter", "activity": "pours latte art"},
            "future": {"place": "cafe back bar", "activity": "trains junior, wipes machine"},
        },
        "stories": {
            "past": (
                "Morning light hits the steel pitcher as she over-tilts it; white foam "
                "floods her apron and she laughs, startled, grabbing a towel."
            ),
            "present": (
                "At the sunlit counter she steadies the cup and pours a clean heart "
                "of latte art for the regular who always sits by the window."
            ),
            "future": (
                "Years on, under warmer lamps, she wipes the espresso machine and "
                "guides a junior's grip on the steamer wand without looking away."
            ),
        },
        "axis_build": {
            "past": {
                "focal": ["spilling", "holding", "surprised"],
                "search": [
                    "milk", "pitcher", "apron", "cafe", "morning", "towel",
                    "indoors", "counter", "wet", "reaching",
                ],
            },
            "present": {
                "focal": ["pouring", "holding", "concentrating"],
                "search": [
                    "latte_art", "coffee_cup", "cafe", "day", "window",
                    "indoors", "counter", "steam", "ceramic",
                ],
            },
            "future": {
                "focal": ["wiping", "pointing", "teaching"],
                "search": [
                    "espresso_machine", "cafe", "indoors", "evening",
                    "cloth", "steam", "back_bar", "warm_light",
                ],
            },
        },
        "expect": {
            "candidates_ok": True,
            "ungrounded": False,
            "off_topic": False,
            "acts_distinct": True,
            "activities_distinct": True,
            "slots_collapsed": False,
            "tag_lines_collapsed": False,
            "max_tag_jaccard_lt": 0.75,
            "all_have_action": True,
            "draft_refine_auto": True,
            "any_axis_degenerate": False,
        },
    },
    "collapsed_same_shot": {
        "time_scale": "years",
        "divergence": 0.3,
        "user_topic": "カフェの話",
        "candidates": [
            {
                "id": "A",
                "title": "Smile",
                "past": "She stands at the cafe counter smiling at the camera.",
                "present": "She stands at the cafe counter smiling softly.",
                "future": "She stands at the cafe counter smiling warmly.",
                "motif": "smile",
                "turn": "she keeps smiling",
                "grounded_tags": ["smile"],
            },
            {
                "id": "B",
                "title": "Pose",
                "past": "Standing by the counter looking ahead.",
                "present": "Standing by the counter looking ahead calmly.",
                "future": "Standing by the counter looking ahead again.",
                "motif": "counter",
                "turn": "still standing",
                "grounded_tags": [],
            },
            {
                "id": "C",
                "title": "Idle",
                "past": "She sits and gazes out the cafe window quietly.",
                "present": "She sits and gazes out the cafe window quietly.",
                "future": "She sits and gazes out the cafe window quietly.",
                "motif": "window",
                "turn": "gazing",
                "grounded_tags": [],
            },
        ],
        "seed_tags": ["apron", "coffee_cup", "counter"],
        "selected": "A",
        "activities": {
            "past": "Standing at the cafe counter smiling.",
            "present": "Standing at the cafe counter smiling.",
            "future": "Standing at the cafe counter smiling.",
        },
        "axis_slots": {
            "past": {"place": "cafe counter", "activity": "standing smiling"},
            "present": {"place": "cafe counter", "activity": "standing smiling"},
            "future": {"place": "cafe counter", "activity": "standing smiling"},
        },
        "stories": {
            "past": "She stands at the cafe counter smiling at the camera in soft light.",
            "present": "She stands at the cafe counter smiling at the camera in soft light.",
            "future": "She stands at the cafe counter smiling at the camera in soft light.",
        },
        "axis_build": {
            "past": {
                "focal": ["standing", "smile"],
                "search": ["cafe", "counter", "indoors", "day"],
            },
            "present": {
                "focal": ["standing", "smile"],
                "search": ["cafe", "counter", "indoors", "day"],
            },
            "future": {
                "focal": ["standing", "smile"],
                "search": ["cafe", "counter", "indoors", "day"],
            },
        },
        "expect": {
            "candidates_ok": False,
            "acts_distinct": False,
            "activities_distinct": False,
            "slots_collapsed": True,
            "tag_lines_collapsed": True,
            "max_tag_jaccard_lt": 1.01,
            "all_have_action": False,
            "draft_refine_auto": True,  # years scale → auto draft
            "expect_high_tag_overlap": True,
            "any_axis_degenerate": True,  # idle pose caught
        },
    },
    "paraphrase_same_kitchen": {
        "time_scale": "days",
        "divergence": 0.4,
        "user_topic": "キッチンで料理",
        "candidates": [
            {
                "id": "A",
                "title": "Dough",
                "past": "In the kitchen she kneads dough on the wooden board.",
                "present": "In the kitchen she folds the dough on the wooden board.",
                "future": "In the kitchen she shapes dough on the wooden board.",
                "motif": "dough",
                "turn": "dough becomes bread",
                "grounded_tags": ["kitchen", "dough", "stove"],
            },
            {
                "id": "B",
                "title": "Soup",
                "past": "She stirs tomato soup on the stove in the kitchen.",
                "present": "She tastes the tomato soup with a wooden spoon.",
                "future": "She ladles tomato soup into two bowls.",
                "motif": "soup",
                "turn": "one pot becomes shared meal",
                "grounded_tags": ["stove", "kitchen", "spoon"],
            },
            {
                "id": "C",
                "title": "Chop",
                "past": "She chops green onions on a cutting board in the kitchen.",
                "present": "She scrapes chopped onions into a bowl.",
                "future": "She rinses the knife under running water.",
                "motif": "knife",
                "turn": "prep becomes cleanup",
                "grounded_tags": ["knife", "kitchen", "dough"],
            },
        ],
        "seed_tags": ["kitchen", "dough", "stove", "knife"],
        "selected": "A",
        "activities": {
            "past": "Kneading dough with both palms on a flour-dusted wooden board.",
            "present": "Folding the same dough over itself on the flour-dusted wooden board.",
            "future": "Shaping the dough into a round loaf on the flour-dusted wooden board.",
        },
        "axis_slots": {
            "past": {"place": "kitchen board", "activity": "kneading dough"},
            "present": {"place": "kitchen board", "activity": "folding dough"},
            "future": {"place": "kitchen board", "activity": "shaping dough"},
        },
        "stories": {
            "past": "Flour dusts her wrists as she kneads the sticky dough on the wooden board.",
            "present": "She folds the sticky dough over itself on the wooden board, flour on her wrists.",
            "future": "On the wooden board she shapes the sticky dough into a round loaf, flour on her wrists.",
        },
        "axis_build": {
            "past": {
                "focal": ["kneading", "both_hands"],
                "search": ["kitchen", "dough", "flour", "wooden_board", "indoors"],
            },
            "present": {
                "focal": ["folding", "both_hands"],
                "search": ["kitchen", "dough", "flour", "wooden_board", "indoors"],
            },
            "future": {
                "focal": ["shaping", "holding"],
                "search": [
                    "kitchen", "dough", "flour", "wooden_board", "indoors", "loaf",
                ],
            },
        },
        "expect": {
            "candidates_ok": True,
            "off_topic": False,
            "acts_distinct": False,  # story bigram catches paraphrase
            "activities_distinct": True,  # activities alone can slip
            "slots_collapsed": True,  # slots catch same place+activity family
            "tag_lines_collapsed": True,  # content-tag gate after pad strip
            "max_tag_jaccard_lt": 1.01,
            "all_have_action": True,
            "draft_refine_auto": False,
            "probe_paraphrase_gap": True,
        },
    },
    "micro_minutes_ok_similar": {
        "time_scale": "minutes",
        "divergence": 0.2,
        "user_topic": "",
        "candidates": [
            {
                "id": "A",
                "title": "Steam",
                "past": "She lifts the pitcher just before the pour.",
                "present": "She begins pouring the milk into the espresso.",
                "future": "She finishes the heart and slides the cup forward.",
                "motif": "steam",
                "turn": "pour completes",
                "grounded_tags": ["pitcher", "coffee_cup"],
            },
            {
                "id": "B",
                "title": "Glance",
                "past": "She glances at the clock above the machine.",
                "present": "She wipes a drip from the pitcher rim.",
                "future": "She calls the order number aloud.",
                "motif": "clock",
                "turn": "rush settles",
                "grounded_tags": ["clock", "pitcher"],
            },
            {
                "id": "C",
                "title": "Reach",
                "past": "She reaches for a clean cup on the shelf.",
                "present": "She sets the cup under the group head.",
                "future": "She tamps the next portafilter firmly.",
                "motif": "cup",
                "turn": "next shot starts",
                "grounded_tags": ["cup", "portafilter"],
            },
        ],
        "seed_tags": ["pitcher", "coffee_cup", "clock"],
        "selected": "A",
        "activities": {
            "past": "Lifting the milk pitcher above the espresso cup.",
            "present": "Pouring steamed milk into the espresso in a thin stream.",
            "future": "Sliding the finished latte cup across the counter.",
        },
        "axis_slots": {
            "past": {"place": "counter", "activity": "lifts pitcher"},
            "present": {"place": "counter", "activity": "pours milk"},
            "future": {"place": "counter", "activity": "slides cup"},
        },
        "stories": {
            "past": "She lifts the pitcher just above the cup, steam curling.",
            "present": "She pours the milk in a thin stream into the espresso.",
            "future": "She finishes the heart and slides the cup toward the guest.",
        },
        "axis_build": {
            "past": {
                "focal": ["lifting", "holding"],
                "search": ["pitcher", "steam", "cafe", "cup"],
            },
            "present": {
                "focal": ["pouring"],
                "search": ["milk", "espresso", "cafe", "stream"],
            },
            "future": {
                "focal": ["sliding", "holding"],
                "search": ["coffee_cup", "counter", "latte_art"],
            },
        },
        "expect": {
            "candidates_ok": True,
            "acts_distinct": True,
            "activities_distinct": True,
            "slots_collapsed": False,
            "tag_lines_collapsed": False,
            "max_tag_jaccard_lt": 0.85,
            "all_have_action": True,
            "draft_refine_auto": False,
            "skip_differentiate": True,
        },
    },
}


def _run_scenario(name: str, sc: dict) -> dict:
    cands = sc["candidates"]
    prompts = {
        a: assemble_axis_prompt(
            story=sc["stories"][a],
            focal=sc["axis_build"][a]["focal"],
            search_tags=sc["axis_build"][a]["search"],
            lock_tags=LOCK,
        )
        for a in AXES
    }
    score = score_axis_bundle(sc["stories"], sc["activities"], prompts)
    return {
        "name": name,
        "time_scale": sc["time_scale"],
        "gates": {
            "candidates_degenerate": candidates_degenerate(cands),
            "candidates_ungrounded": candidates_ungrounded(
                cands, seed_tags=sc["seed_tags"]
            ),
            "candidates_off_topic": candidates_off_topic(
                cands, user_topic=sc.get("user_topic", "")
            ),
            "acts_temporally_distinct": acts_temporally_distinct(sc["stories"]),
            "activities_temporally_distinct": activities_temporally_distinct(
                sc["activities"]
            ),
            "axis_slots_collapsed": axis_slots_collapsed(sc["axis_slots"]),
            "should_differentiate_acts": should_differentiate_acts(sc["time_scale"]),
            "should_use_draft_refine": should_use_draft_refine(
                mode=sc.get("use_draft_refine", "auto"),
                time_scale=sc["time_scale"],
                divergence=sc["divergence"],
                workflow_name=sc.get("workflow_name", "chronicle_test.json"),
            ),
            "axis_tag_lines_collapsed": axis_tag_lines_collapsed(prompts),
        },
        "score": score,
        "prompts": prompts,
        "dramatic_modes": assign_dramatic_modes(tone="bright"),
    }


def _print_report(r: dict) -> None:
    print(f"\n═══ SIM {r['name']} (scale={r['time_scale']}) ═══")
    g = r["gates"]
    print(
        f"  gates: cand_deg={g['candidates_degenerate']} "
        f"unground={g['candidates_ungrounded']} off_topic={g['candidates_off_topic']} "
        f"acts_ok={g['acts_temporally_distinct']} "
        f"acts_act_ok={g['activities_temporally_distinct']} "
        f"slots_collapse={g['axis_slots_collapsed']} "
        f"tag_collapse={g['axis_tag_lines_collapsed']} "
        f"diff={g['should_differentiate_acts']} draft={g['should_use_draft_refine']}"
    )
    s = r["score"]
    print(
        f"  score: story_sim={s['story_mean_sim']} activity_sim={s['activity_mean_sim']} "
        f"max_tag_jaccard={s['max_tag_jaccard']} pairs={s['tag_pair_jaccard']}"
    )
    for a, info in s["per_axis"].items():
        print(
            f"    [{a}] tags={info['tag_count']} deg={info['tag_degenerate']}"
            f"({info['tag_deg_reason'] or 'ok'}) "
            f"action={info['activity_has_action']} place={info['activity_has_place']} "
            f"idle_tags={info['tag_idle_hits']}"
        )
    for a in AXES:
        preview = r["prompts"][a][:110].replace("\n", " ")
        print(f"    prompt[{a}]: {preview}...")


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario_simulation(name):
    sc = SCENARIOS[name]
    report = _run_scenario(name, sc)
    _print_report(report)
    exp = sc["expect"]
    g = report["gates"]
    s = report["score"]

    if exp.get("skip_differentiate"):
        assert g["should_differentiate_acts"] is False

    assert g["candidates_degenerate"] is (not exp["candidates_ok"])

    if "ungrounded" in exp:
        assert g["candidates_ungrounded"] is exp["ungrounded"]
    if "off_topic" in exp:
        assert g["candidates_off_topic"] is exp["off_topic"]

    assert g["acts_temporally_distinct"] is exp["acts_distinct"]
    assert g["activities_temporally_distinct"] is exp["activities_distinct"]
    assert g["axis_slots_collapsed"] is exp["slots_collapsed"]
    assert g["axis_tag_lines_collapsed"] is exp["tag_lines_collapsed"]
    assert g["should_use_draft_refine"] is exp["draft_refine_auto"]

    if exp.get("expect_high_tag_overlap"):
        assert s["max_tag_jaccard"] >= 0.7
    else:
        assert s["max_tag_jaccard"] < exp["max_tag_jaccard_lt"]

    actions = [s["per_axis"][a]["activity_has_action"] for a in AXES]
    if exp["all_have_action"]:
        assert all(actions)
    else:
        assert not all(actions)

    if "any_axis_degenerate" in exp:
        any_deg = any(s["per_axis"][a]["tag_degenerate"] for a in AXES)
        assert any_deg is exp["any_axis_degenerate"]

    if exp.get("probe_paraphrase_gap"):
        print(
            f"  PROBE paraphrase: acts_distinct={g['acts_temporally_distinct']} "
            f"activity_distinct={g['activities_temporally_distinct']} "
            f"tag_collapse={g['axis_tag_lines_collapsed']} "
            f"story_sim={s['story_mean_sim']} (threshold={_BEAT_SIMILAR_THRESHOLD})"
        )


def test_good_scenario_prompts_are_concrete_and_diverse():
    report = _run_scenario("good_years_cafe", SCENARIOS["good_years_cafe"])
    s = report["score"]
    assert s["acts_distinct"] and s["activities_distinct"]
    assert not s["tag_lines_collapsed"]
    assert s["max_tag_jaccard"] < 0.75
    for a in AXES:
        info = s["per_axis"][a]
        assert info["activity_has_action"]
        assert info["activity_has_place"]
        assert info["tag_count"] >= 25
        assert not info["tag_degenerate"]


def test_collapsed_scenario_is_caught_by_gates():
    report = _run_scenario("collapsed_same_shot", SCENARIOS["collapsed_same_shot"])
    g = report["gates"]
    assert g["candidates_degenerate"]
    assert not g["acts_temporally_distinct"]
    assert not g["activities_temporally_distinct"]
    assert g["axis_slots_collapsed"]
    assert g["axis_tag_lines_collapsed"]
    assert g["should_differentiate_acts"]
    # Idle standing/smile prompts now fail the dynamic-action densify guard.
    assert any(
        report["score"]["per_axis"][a]["tag_degenerate"] for a in AXES
    )


def test_topic_ja_matches_english_cafe_beats():
    tokens = topic_anchor_tokens("この子がカフェで働く話")
    assert "カフェ" in tokens
    assert "cafe" in tokens  # bilingual alias
    cands = SCENARIOS["good_years_cafe"]["candidates"]
    assert not candidates_off_topic(cands, "この子がカフェで働く話")


def test_identity_lock_preserves_hair_eyes_across_diverse_axes():
    report = _run_scenario("good_years_cafe", SCENARIOS["good_years_cafe"])
    for a in AXES:
        low = report["prompts"][a].lower()
        assert "silver_hair" in low
        assert "blue_eyes" in low


def test_scene_constraints_split_day_vs_night():
    night_story = (
        "Under neon rain she unlocks the cafe door at midnight and flips the sign."
    )
    tags = assemble_axis_prompt(
        story=night_story,
        focal=["unlocking", "holding_key"],
        search_tags=[
            "cafe", "door", "night", "neon", "rain", "outdoors", "day", "sunlight",
        ],
        lock_tags=LOCK,
    )
    constraints = infer_axis_scene_constraints(night_story)
    assert constraints["time_of_day"] == "night"
    assert "day" not in apply_scene_constraints(["day", "night", "cafe"], constraints)
    assert "holding" in tags.lower() or "unlocking" in tags.lower()


def test_simulation_summary_prints_matrix():
    rows = []
    for name, sc in SCENARIOS.items():
        r = _run_scenario(name, sc)
        rows.append(r)
        _print_report(r)
    print("\n═══ MATRIX ═══")
    print(
        f"{'scenario':28} {'cand':4} {'acts':4} {'actsA':5} "
        f"{'slotC':5} {'tagC':5} {'simS':5} {'simA':5} {'tagJ':5} {'draft':5}"
    )
    for r in rows:
        g, s = r["gates"], r["score"]
        print(
            f"{r['name']:28} "
            f"{'FAIL' if g['candidates_degenerate'] else 'ok':4} "
            f"{'ok' if g['acts_temporally_distinct'] else 'FAIL':4} "
            f"{'ok' if g['activities_temporally_distinct'] else 'FAIL':5} "
            f"{'yes' if g['axis_slots_collapsed'] else 'no':5} "
            f"{'yes' if g['axis_tag_lines_collapsed'] else 'no':5} "
            f"{s['story_mean_sim']:5.2f} "
            f"{s['activity_mean_sim']:5.2f} "
            f"{s['max_tag_jaccard']:5.2f} "
            f"{'yes' if g['should_use_draft_refine'] else 'no':5}"
        )
    good = next(r for r in rows if r["name"] == "good_years_cafe")
    assert not good["gates"]["candidates_degenerate"]
    assert good["gates"]["acts_temporally_distinct"]
    assert not good["gates"]["axis_tag_lines_collapsed"]
    assert good["score"]["max_tag_jaccard"] < 0.75
