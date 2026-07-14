"""Multi-topic Chronicle full simulations (no live LLM/Comfy).

Runs several お題 through real gates, tag merge, draft grounding, Visual Spec
category buckets, and quality_eval — same shape as sim_beach_girl_tens.

Run:
  PYTHONPATH=backend python3 -m pytest tests/story/sim_multi_topics.py -q -s
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.story.generator import (
    AXES,
    CHRONICLE_CAT_FIELDS,
    acts_temporally_distinct,
    activities_temporally_distinct,
    apply_scene_constraints,
    axis_slots_collapsed,
    axis_tag_lines_collapsed,
    bucket_danbooru_tags,
    candidates_degenerate,
    candidates_off_topic,
    candidates_ungrounded,
    draft_richness_delta,
    identity_lock_tags,
    infer_axis_scene_constraints,
    is_multi_character,
    merge_category_tags,
    merge_chronicle_axis_tags,
    merge_draft_wd14_tags,
    parse_visual_script_category_tags,
    should_differentiate_acts,
    should_use_draft_refine,
    topic_anchor_tokens,
)
from app.story.quality import evaluate_chronicle_quality, score_prompt_richness

WORKFLOW = "chronicle_sim.json"


def _run_case(sc: dict) -> dict:
    topic = sc["user_topic"]
    scale = sc["time_scale"]
    div = sc["divergence"]
    base = sc["base_wd14"]
    multi = is_multi_character(base)
    lock = identity_lock_tags(base, multi_character=multi)

    draft = should_use_draft_refine(
        mode="auto", time_scale=scale, divergence=div, workflow_name=WORKFLOW,
    )
    gates = {
        "candidates_degenerate": candidates_degenerate(sc["candidates"]),
        "candidates_ungrounded": candidates_ungrounded(
            sc["candidates"], seed_tags=sc["seed_tags"],
        ),
        "candidates_off_topic": candidates_off_topic(
            sc["candidates"], user_topic=topic,
        ),
        "acts_distinct": acts_temporally_distinct(sc["stories"]),
        "activities_distinct": activities_temporally_distinct(sc["activities"]),
        "slots_collapsed": axis_slots_collapsed(sc["slots"]),
        "should_differentiate": should_differentiate_acts(scale),
        "draft_auto": draft,
        "multi_character": multi,
        "topic_anchors": topic_anchor_tokens(topic),
    }

    prompts: dict[str, dict] = {}
    deltas: dict[str, dict] = {}
    for axis in AXES:
        build = sc["axis_build"][axis]
        before = merge_chronicle_axis_tags(
            focal=build["focal"], search_tags=build["search"], lock_tags=lock,
        )
        search = list(build["search"])
        if draft:
            search = merge_draft_wd14_tags(
                vocab_tags=search,
                draft_tags=sc["draft_wd14"][axis],
                lock_tags=lock,
                focal=build["focal"],
            )
        search = apply_scene_constraints(
            search, infer_axis_scene_constraints(sc["stories"][axis]),
        )
        tag_line = merge_chronicle_axis_tags(
            focal=build["focal"], search_tags=search, lock_tags=lock,
        )
        parts = [t.strip() for t in tag_line.split(",") if t.strip()]
        for a in sc.get("subject_anchors", ["1girl", "solo"]):
            if a.lower() not in {p.lower() for p in parts}:
                parts.insert(0, a)
        for lk in lock:
            if lk.lower() not in {p.lower() for p in parts}:
                parts.append(lk)
        pad = ["detailed_background", "depth_of_field", "cinematic_lighting"]
        seen = {p.lower() for p in parts}
        for p in pad:
            if p.lower() not in seen and len(parts) < 34:
                parts.append(p)
                seen.add(p.lower())
        tag_line = ", ".join(parts)

        vs_raw = f"{sc['prose'][axis]}\n\n{sc['cat_footer'][axis]}"
        prose, vs_cats = parse_visual_script_category_tags(vs_raw)
        cats = merge_category_tags(vs_cats, bucket_danbooru_tags(tag_line))
        positive = f"{tag_line}\n\n{prose}".strip()
        prompts[axis] = {
            "positive": positive,
            "negative": "blurry, lowres, bad anatomy, static_pose, expressionless",
            "visual_script": prose,
            "categories": cats,
            "tag_line": tag_line,
        }
        if draft:
            deltas[axis] = draft_richness_delta(
                before_tag_line=before, after_tag_line=tag_line,
            )

    tag_only = {a: prompts[a]["tag_line"] for a in AXES}
    gates["tag_lines_collapsed"] = axis_tag_lines_collapsed(tag_only)

    q = evaluate_chronicle_quality(
        user_topic=topic,
        title=sc["title"],
        overall=sc["overall"],
        stories=sc["stories"],
        activities=sc["activities"],
        prompts=prompts,
        time_scale=scale,
        lock_tags=lock,
        draft_deltas=deltas or None,
    )
    return {
        "name": sc["name"],
        "topic": topic,
        "scale": scale,
        "divergence": div,
        "lock": lock,
        "gates": gates,
        "quality": q,
        "prompts": prompts,
        "deltas": deltas,
    }


def _print_case(r: dict) -> None:
    q = r["quality"]
    g = r["gates"]
    print("\n" + "═" * 72)
    print(f"  CASE: {r['name']}")
    print(f"  お題: {r['topic']}")
    print(f"  scale={r['scale']}  div={r['divergence']}  multi={g['multi_character']}")
    print(f"  lock={r['lock']}")
    print("═" * 72)
    print(
        f"  gates: deg={g['candidates_degenerate']} off={g['candidates_off_topic']} "
        f"unground={g['candidates_ungrounded']} acts={g['acts_distinct']} "
        f"slots={g['slots_collapsed']} tags={g['tag_lines_collapsed']} "
        f"diff={g['should_differentiate']} draft={g['draft_auto']}"
    )
    print(f"  anchors: {g['topic_anchors']}")
    print("  quality:")
    for d, v in q["dimensions"].items():
        bar = "█" * int(round(v * 10)) + "░" * (10 - int(round(v * 10)))
        print(f"    {d:12} {v:5.2f}  {bar}")
    print(f"    {'OVERALL':12} {q['overall']:5.2f}")
    if q.get("notes", {}).get("draft_grounding"):
        print(f"    draft: {q['notes']['draft_grounding']}")

    for axis in AXES:
        p = r["prompts"][axis]
        rich = score_prompt_richness(p["positive"])
        print(f"\n  [{axis.upper()}] richness={rich['score']:.2f}")
        print(f"    TAG: {p['tag_line'][:110]}…")
        print(f"    VS : {p['visual_script'][:120].replace(chr(10), ' ')}…")
        cats = p["categories"]
        bits = []
        for k in CHRONICLE_CAT_FIELDS:
            if cats.get(k):
                bits.append(f"{k.replace('_tags','')}={','.join(cats[k][:3])}")
        print(f"    SPEC: {' | '.join(bits[:6])}")
    if r["deltas"]:
        print("  draft Δ:", ", ".join(
            f"{a}:{d['delta']:+.2f}" for a, d in r["deltas"].items()
        ))


# ── Cases ─────────────────────────────────────────────────────────────────────

CASES = {
    "cafe_years": {
        "name": "カフェで働く少女 / years",
        "user_topic": "この子がカフェで働く話",
        "time_scale": "years",
        "divergence": 0.55,
        "title": "First Pour",
        "overall": "From spilling milk as a trainee to mentoring a junior at the same cafe.",
        "base_wd14": [
            "1girl", "solo", "silver_hair", "blue_eyes", "apron", "cafe",
            "indoors", "day", "smile",
        ],
        "subject_anchors": ["1girl", "solo"],
        "seed_tags": ["apron", "coffee_cup", "counter", "cafe"],
        "candidates": [
            {
                "id": "A", "title": "Pour",
                "past": "As a trainee she spills milk on her apron at the cafe counter.",
                "present": "She pours latte art for a regular at the sunlit cafe.",
                "future": "Years later she trains a junior while wiping the espresso machine.",
                "motif": "milk", "turn": "spill to mentor",
                "grounded_tags": ["apron", "cafe", "coffee_cup"],
            },
            {
                "id": "B", "title": "Close",
                "past": "She stacks chairs after late cafe practice.",
                "present": "She locks the cafe door and pockets the key.",
                "future": "She opens her own tiny shop two blocks away.",
                "motif": "key", "turn": "close to open",
                "grounded_tags": ["cafe", "key", "chair"],
            },
            {
                "id": "C", "title": "Steam",
                "past": "She burns the first steamed milk at the cafe.",
                "present": "She slides a perfect cappuccino across the counter.",
                "future": "She writes the daily special on the chalkboard.",
                "motif": "steam", "turn": "burn to chalk",
                "grounded_tags": ["cafe", "steam", "cup"],
            },
        ],
        "stories": {
            "past": (
                "Years ago at the cafe she fumbled the milk pitcher and soaked her "
                "apron, laughing nervously under the morning window light."
            ),
            "present": (
                "Now she pours a careful heart of latte art at the sunlit cafe "
                "counter, silver hair tied back, focused smile."
            ),
            "future": (
                "Years later she guides a junior's hand on the pitcher while wiping "
                "the espresso machine at the same cafe in the evening."
            ),
        },
        "activities": {
            "past": "Spilling milk from a pitcher onto her apron at the cafe.",
            "present": "Pouring latte art into a cup at the cafe counter.",
            "future": "Guiding a junior's pour while wiping the espresso machine.",
        },
        "slots": {
            "past": {"place": "cafe counter", "activity": "spills milk"},
            "present": {"place": "cafe counter", "activity": "pours latte"},
            "future": {"place": "cafe machine", "activity": "teaches pour"},
        },
        "axis_build": {
            "past": {
                "focal": ["spilling", "holding", "nervous", "open_mouth"],
                "search": [
                    "cafe", "apron", "milk", "pitcher", "counter", "indoors",
                    "morning", "window", "steam",
                ],
            },
            "present": {
                "focal": ["pouring", "concentrating", "smile", "holding"],
                "search": [
                    "cafe", "latte_art", "coffee_cup", "counter", "indoors",
                    "day", "window", "apron", "steam",
                ],
            },
            "future": {
                "focal": ["guiding", "wiping", "teaching", "serious"],
                "search": [
                    "cafe", "espresso_machine", "apron", "indoors", "evening",
                    "cloth", "steam", "warm_light",
                ],
            },
        },
        "draft_wd14": {
            "past": ["spilling", "apron", "milk", "cafe", "morning", "window", "steam"],
            "present": ["pouring", "latte_art", "cafe", "smile", "daylight", "steam"],
            "future": ["wiping", "espresso_machine", "cafe", "evening", "warm_light"],
        },
        "prose": {
            "past": (
                "A trainee (1girl, solo, silver_hair, blue_eyes) spills milk from a "
                "pitcher (spilling, holding) onto her apron at the cafe counter "
                "(cafe, indoors, morning). Nervous open-mouthed laugh (nervous, "
                "open_mouth) under the window."
            ),
            "present": (
                "She pours latte art (pouring, latte_art, concentrating, smile) into "
                "a cup at the sunlit cafe counter (cafe, coffee_cup, day, window)."
            ),
            "future": (
                "She guides a junior's hand (guiding, teaching) while wiping the "
                "espresso machine (wiping, espresso_machine) in warm evening light "
                "(cafe, evening, warm_light)."
            ),
        },
        "cat_footer": {
            "past": (
                "SUBJECT_TAGS: 1girl, solo, blue_eyes\n"
                "HAIR_TAGS: silver_hair\n"
                "EXPRESSION_TAGS: nervous, open_mouth\n"
                "CLOTHING_TAGS: apron\n"
                "POSE_TAGS: spilling, holding\n"
                "BACKGROUND_TAGS: cafe, indoors, counter, window\n"
                "OBJECT_TAGS: milk, pitcher\n"
                "LIGHTING_TAGS: morning"
            ),
            "present": (
                "SUBJECT_TAGS: 1girl, solo, blue_eyes\n"
                "HAIR_TAGS: silver_hair\n"
                "EXPRESSION_TAGS: smile, concentrating\n"
                "CLOTHING_TAGS: apron\n"
                "POSE_TAGS: pouring, holding\n"
                "BACKGROUND_TAGS: cafe, indoors, counter, window\n"
                "OBJECT_TAGS: coffee_cup, latte_art\n"
                "LIGHTING_TAGS: day, daylight"
            ),
            "future": (
                "SUBJECT_TAGS: 1girl, solo, blue_eyes\n"
                "HAIR_TAGS: silver_hair\n"
                "EXPRESSION_TAGS: serious\n"
                "CLOTHING_TAGS: apron\n"
                "POSE_TAGS: guiding, wiping, teaching\n"
                "BACKGROUND_TAGS: cafe, indoors\n"
                "OBJECT_TAGS: espresso_machine, cloth\n"
                "LIGHTING_TAGS: evening, warm_light"
            ),
        },
        "expect": {
            "off_topic": False,
            "diff": True,
            "draft": True,
            "overall_min": 0.60,
            "topic_min": 0.55,
        },
    },
    "rain_station_hours": {
        "name": "雨の駅で待ち合わせ / hours",
        "user_topic": "雨の駅で待ち合わせ",
        "time_scale": "hours",
        "divergence": 0.40,
        "title": "Platform Rain",
        "overall": "Across a rainy afternoon she waits, meets, and leaves the station platform.",
        "base_wd14": [
            "1girl", "solo", "black_hair", "brown_eyes", "coat", "umbrella",
            "outdoors", "rain", "train_station",
        ],
        "subject_anchors": ["1girl", "solo"],
        "seed_tags": ["umbrella", "train_station", "rain", "platform"],
        "candidates": [
            {
                "id": "A", "title": "Wait",
                "past": "At the rainy train station she checks the clock above the platform.",
                "present": "Under her umbrella she waves when she spots her friend on the platform.",
                "future": "They walk off the station steps sharing one umbrella in the rain.",
                "motif": "umbrella", "turn": "wait to share",
                "grounded_tags": ["umbrella", "rain", "train_station"],
            },
            {
                "id": "B", "title": "Miss",
                "past": "She arrives early to the rainy station platform.",
                "present": "She lowers the umbrella to look for a familiar coat.",
                "future": "She boards the next train alone as rain streaks the window.",
                "motif": "clock", "turn": "early to alone",
                "grounded_tags": ["train_station", "rain", "coat"],
            },
            {
                "id": "C", "title": "Dash",
                "past": "She runs through puddles toward the station entrance in the rain.",
                "present": "She shakes water from her umbrella on the platform.",
                "future": "She buys two tickets at the rainy station gate.",
                "motif": "puddle", "turn": "run to tickets",
                "grounded_tags": ["rain", "umbrella", "train_station"],
            },
        ],
        "stories": {
            "past": (
                "A few hours earlier at the rainy train station she stood under her "
                "umbrella checking the platform clock, coat collar turned up."
            ),
            "present": (
                "Now on the wet platform she waves with her free hand, umbrella tilted, "
                "spotting her friend through the rain at the train station."
            ),
            "future": (
                "Later they share one umbrella down the station steps, rain tapping "
                "the fabric as trains hiss behind them."
            ),
        },
        "activities": {
            "past": "Checking the platform clock under an umbrella at the rainy station.",
            "present": "Waving on the wet platform while holding an umbrella.",
            "future": "Sharing one umbrella while walking down the station steps.",
        },
        "slots": {
            "past": {"place": "station platform", "activity": "checks clock"},
            "present": {"place": "station platform", "activity": "waves"},
            "future": {"place": "station steps", "activity": "shares umbrella"},
        },
        "axis_build": {
            "past": {
                "focal": ["holding", "looking_up", "serious"],
                "search": [
                    "train_station", "platform", "umbrella", "rain", "outdoors",
                    "coat", "clock", "wet",
                ],
            },
            "present": {
                "focal": ["waving", "holding", "smile", "looking_at_another"],
                "search": [
                    "train_station", "platform", "umbrella", "rain", "outdoors",
                    "coat", "wet",
                ],
            },
            "future": {
                "focal": ["walking", "sharing", "holding", "looking_ahead"],
                "search": [
                    "train_station", "stairs", "umbrella", "rain", "outdoors",
                    "coat", "wet",
                ],
            },
        },
        "draft_wd14": {
            "past": ["umbrella", "rain", "train_station", "platform", "coat", "looking_up"],
            "present": ["waving", "umbrella", "rain", "platform", "smile", "wet"],
            "future": ["walking", "umbrella", "rain", "stairs", "sharing"],
        },
        "prose": {
            "past": (
                "Under a dark umbrella (1girl, solo, black_hair, brown_eyes, umbrella) "
                "she looks up at the platform clock (looking_up, holding) in the rain "
                "(rain, train_station, platform, outdoors)."
            ),
            "present": (
                "She waves on the wet platform (waving, smile) while tilting her umbrella "
                "(umbrella, rain, train_station, wet)."
            ),
            "future": (
                "Two figures share one umbrella down the station steps (walking, sharing, "
                "holding) as rain keeps falling (rain, train_station, stairs)."
            ),
        },
        "cat_footer": {
            "past": (
                "SUBJECT_TAGS: 1girl, solo, brown_eyes\n"
                "HAIR_TAGS: black_hair\n"
                "EXPRESSION_TAGS: serious, looking_up\n"
                "CLOTHING_TAGS: coat\n"
                "POSE_TAGS: holding, looking_up\n"
                "BACKGROUND_TAGS: train_station, platform, outdoors, rain\n"
                "OBJECT_TAGS: umbrella, clock\n"
                "LIGHTING_TAGS: overcast"
            ),
            "present": (
                "SUBJECT_TAGS: 1girl, solo, brown_eyes\n"
                "HAIR_TAGS: black_hair\n"
                "EXPRESSION_TAGS: smile, looking_at_another\n"
                "CLOTHING_TAGS: coat\n"
                "POSE_TAGS: waving, holding\n"
                "BACKGROUND_TAGS: train_station, platform, outdoors, rain\n"
                "OBJECT_TAGS: umbrella\n"
                "LIGHTING_TAGS: rainy"
            ),
            "future": (
                "SUBJECT_TAGS: 1girl, solo, brown_eyes\n"
                "HAIR_TAGS: black_hair\n"
                "EXPRESSION_TAGS: looking_ahead\n"
                "CLOTHING_TAGS: coat\n"
                "POSE_TAGS: walking, sharing, holding\n"
                "BACKGROUND_TAGS: train_station, stairs, outdoors, rain\n"
                "OBJECT_TAGS: umbrella\n"
                "LIGHTING_TAGS: rainy"
            ),
        },
        "expect": {
            "off_topic": False,
            "diff": True,
            "draft": True,
            "overall_min": 0.55,
            "topic_min": 0.45,
        },
    },
    "rooftop_night_days": {
        "name": "屋上で星を見る / days",
        "user_topic": "屋上で星を見る",
        "time_scale": "days",
        "divergence": 0.45,
        "title": "Roof Stars",
        "overall": "Across several nights she climbs to the school rooftop to watch the stars.",
        "base_wd14": [
            "1girl", "solo", "blonde_hair", "green_eyes", "school_uniform",
            "outdoors", "night", "rooftop",
        ],
        "subject_anchors": ["1girl", "solo"],
        "seed_tags": ["rooftop", "night", "school", "star"],
        "candidates": [
            {
                "id": "A", "title": "Climb",
                "past": "She pushes open the rooftop door of the school at night.",
                "present": "On the rooftop she points up at a bright star.",
                "future": "Days later she lies back on the rooftop counting constellations.",
                "motif": "star", "turn": "open to lie back",
                "grounded_tags": ["rooftop", "night", "school"],
            },
            {
                "id": "B", "title": "Wind",
                "past": "Wind lifts her ribbon on the school rooftop at dusk.",
                "present": "She holds the railing and looks at the night sky.",
                "future": "She sketches the star map in a notebook on the rooftop.",
                "motif": "railing", "turn": "wind to sketch",
                "grounded_tags": ["rooftop", "night", "notebook"],
            },
            {
                "id": "C", "title": "Quiet",
                "past": "She sits alone on the quiet school rooftop after class.",
                "present": "She cups warm tea and watches the city lights from the rooftop.",
                "future": "She shares the rooftop view with a quiet friend under the stars.",
                "motif": "tea", "turn": "alone to share",
                "grounded_tags": ["rooftop", "night", "tea"],
            },
        ],
        "stories": {
            "past": (
                "A few days ago she pushed open the heavy rooftop door of the school "
                "at night, wind catching her blonde hair."
            ),
            "present": (
                "Now on the school rooftop she points at a bright star, school uniform "
                "fluttering, green eyes wide with wonder under the night sky."
            ),
            "future": (
                "Days later she lies back on the cool rooftop concrete counting "
                "constellations, notebook open beside her under the stars."
            ),
        },
        "activities": {
            "past": "Pushing open the school rooftop door at night.",
            "present": "Pointing up at a bright star from the rooftop.",
            "future": "Lying back on the rooftop counting constellations.",
        },
        "slots": {
            "past": {"place": "rooftop door", "activity": "opens door"},
            "present": {"place": "rooftop", "activity": "points at star"},
            "future": {"place": "rooftop", "activity": "lies counting stars"},
        },
        "axis_build": {
            "past": {
                "focal": ["reaching", "opening", "looking_ahead"],
                "search": [
                    "rooftop", "door", "school", "night", "outdoors", "wind",
                    "school_uniform",
                ],
            },
            "present": {
                "focal": ["pointing", "looking_up", "smile", "wonder"],
                "search": [
                    "rooftop", "night", "star", "sky", "outdoors", "school",
                    "school_uniform",
                ],
            },
            "future": {
                "focal": ["lying", "looking_up", "holding", "calm"],
                "search": [
                    "rooftop", "night", "star", "constellation", "outdoors",
                    "notebook", "school_uniform",
                ],
            },
        },
        "draft_wd14": {
            "past": ["reaching", "door", "rooftop", "night", "wind", "school"],
            "present": ["pointing", "looking_up", "star", "rooftop", "night", "smile"],
            "future": ["lying", "star", "rooftop", "night", "notebook", "looking_up"],
        },
        "prose": {
            "past": (
                "She reaches for the rooftop door handle (1girl, solo, blonde_hair, "
                "green_eyes, reaching, opening) at the school night rooftop "
                "(rooftop, school, night, outdoors, wind)."
            ),
            "present": (
                "On the rooftop she points at a bright star (pointing, looking_up, "
                "smile, wonder) under the night sky (rooftop, night, star, sky)."
            ),
            "future": (
                "She lies back on the rooftop (lying, looking_up, calm) with a notebook "
                "beside her (notebook, rooftop, night, constellation)."
            ),
        },
        "cat_footer": {
            "past": (
                "SUBJECT_TAGS: 1girl, solo, green_eyes\n"
                "HAIR_TAGS: blonde_hair\n"
                "EXPRESSION_TAGS: looking_ahead\n"
                "CLOTHING_TAGS: school_uniform\n"
                "POSE_TAGS: reaching, opening\n"
                "BACKGROUND_TAGS: rooftop, school, outdoors, night\n"
                "OBJECT_TAGS: door\n"
                "LIGHTING_TAGS: night, moonlight"
            ),
            "present": (
                "SUBJECT_TAGS: 1girl, solo, green_eyes\n"
                "HAIR_TAGS: blonde_hair\n"
                "EXPRESSION_TAGS: smile, wonder, looking_up\n"
                "CLOTHING_TAGS: school_uniform\n"
                "POSE_TAGS: pointing, looking_up\n"
                "BACKGROUND_TAGS: rooftop, outdoors, night, sky\n"
                "OBJECT_TAGS: star\n"
                "LIGHTING_TAGS: night, starlight"
            ),
            "future": (
                "SUBJECT_TAGS: 1girl, solo, green_eyes\n"
                "HAIR_TAGS: blonde_hair\n"
                "EXPRESSION_TAGS: calm, looking_up\n"
                "CLOTHING_TAGS: school_uniform\n"
                "POSE_TAGS: lying, looking_up, holding\n"
                "BACKGROUND_TAGS: rooftop, outdoors, night\n"
                "OBJECT_TAGS: notebook, constellation\n"
                "LIGHTING_TAGS: night, starlight"
            ),
        },
        "expect": {
            "off_topic": False,
            "diff": True,
            "draft": True,
            "overall_min": 0.55,
            "topic_min": 0.40,
        },
    },
    "festival_trio_hours": {
        "name": "夏祭りで遊ぶ三人 / hours",
        "user_topic": "夏祭りで遊ぶ三人の少女",
        "time_scale": "hours",
        "divergence": 0.45,
        "title": "Lantern Dash",
        "overall": "One festival evening: food stall, lantern race, fireworks sweets.",
        "base_wd14": [
            "3girls", "multiple_girls", "blonde_hair", "black_hair", "brown_hair",
            "yukata", "festival", "night", "outdoors", "smile",
        ],
        "subject_anchors": ["3girls", "multiple_girls"],
        "seed_tags": ["festival", "yukata", "lantern", "3girls"],
        "candidates": [
            {
                "id": "A", "title": "Stall",
                "past": "At the summer festival three girls buy grilled squid at a food stall.",
                "present": "The three girls race between paper lanterns in yukata.",
                "future": "They share a candy apple under fireworks at the festival.",
                "motif": "lantern", "turn": "stall to fireworks",
                "grounded_tags": ["festival", "yukata", "3girls"],
            },
            {
                "id": "B", "title": "Scoop",
                "past": "Three girls lean over a goldfish scooping tub at the festival.",
                "present": "One cheers as her friend scoops a goldfish.",
                "future": "They carry the water bag toward the shrine path at the festival.",
                "motif": "goldfish", "turn": "scoop to carry",
                "grounded_tags": ["festival", "goldfish", "3girls"],
            },
            {
                "id": "C", "title": "Spark",
                "past": "Three girls light sparklers behind the festival stalls.",
                "present": "Sparks fall as they draw circles in the night air.",
                "future": "They blow out the last sparkler under lanterns at the festival.",
                "motif": "sparkler", "turn": "light to extinguish",
                "grounded_tags": ["festival", "sparkler", "3girls", "night"],
            },
        ],
        "stories": {
            "past": (
                "Earlier at the summer festival, three girls in yukata crowd a grilled "
                "squid stall, laughing as smoke rises past paper lanterns."
            ),
            "present": (
                "Now the three girls race between glowing paper lanterns, sleeves "
                "fluttering, one pointing ahead at the festival night."
            ),
            "future": (
                "Later they sit shoulder to shoulder under fireworks, sharing one candy "
                "apple at the festival night."
            ),
        },
        "activities": {
            "past": "Three girls buying grilled squid at a festival food stall.",
            "present": "Three girls racing between paper lanterns in yukata.",
            "future": "Three girls sharing a candy apple under fireworks at the festival.",
        },
        "slots": {
            "past": {"place": "festival stall", "activity": "buy squid"},
            "present": {"place": "lantern street", "activity": "race"},
            "future": {"place": "fireworks lawn", "activity": "share apple"},
        },
        "axis_build": {
            "past": {
                "focal": ["holding", "reaching", "laughing", "open_mouth"],
                "search": [
                    "3girls", "multiple_girls", "yukata", "festival", "food_stall",
                    "paper_lantern", "night", "outdoors", "smoke",
                ],
            },
            "present": {
                "focal": ["running", "pointing", "looking_back", "smile"],
                "search": [
                    "3girls", "multiple_girls", "yukata", "festival", "paper_lantern",
                    "night", "outdoors", "glow", "wind",
                ],
            },
            "future": {
                "focal": ["sitting", "holding", "sharing", "closed_eyes", "happy"],
                "search": [
                    "3girls", "multiple_girls", "yukata", "festival", "fireworks",
                    "candy_apple", "night", "outdoors", "sparkle",
                ],
            },
        },
        "draft_wd14": {
            "past": ["3girls", "food_stall", "paper_lantern", "yukata", "laughing", "night"],
            "present": ["3girls", "running", "paper_lantern", "yukata", "smile", "glow"],
            "future": ["3girls", "fireworks", "candy_apple", "sitting", "sparkle", "night"],
        },
        "prose": {
            "past": (
                "Three girls in yukata (3girls, multiple_girls, yukata) crowd a festival "
                "food stall (festival, food_stall, night), one reaching for a skewer "
                "(reaching, holding, laughing, open_mouth)."
            ),
            "present": (
                "The trio races under paper lanterns (running, paper_lantern, festival, "
                "night), one pointing ahead (pointing), another looking back smiling "
                "(looking_back, smile)."
            ),
            "future": (
                "Under fireworks (fireworks, night, festival) they share a candy apple "
                "(holding, sharing, candy_apple) with soft closed-eye smiles "
                "(closed_eyes, happy)."
            ),
        },
        "cat_footer": {
            "past": (
                "SUBJECT_TAGS: 3girls, multiple_girls\n"
                "EXPRESSION_TAGS: laughing, open_mouth\n"
                "CLOTHING_TAGS: yukata\n"
                "POSE_TAGS: holding, reaching\n"
                "BACKGROUND_TAGS: festival, food_stall, outdoors, night\n"
                "OBJECT_TAGS: paper_lantern, smoke\n"
                "LIGHTING_TAGS: night, glow"
            ),
            "present": (
                "SUBJECT_TAGS: 3girls, multiple_girls\n"
                "EXPRESSION_TAGS: smile, looking_back\n"
                "CLOTHING_TAGS: yukata\n"
                "POSE_TAGS: running, pointing\n"
                "BACKGROUND_TAGS: festival, outdoors, night\n"
                "OBJECT_TAGS: paper_lantern\n"
                "LIGHTING_TAGS: night, glow"
            ),
            "future": (
                "SUBJECT_TAGS: 3girls, multiple_girls\n"
                "EXPRESSION_TAGS: closed_eyes, happy\n"
                "CLOTHING_TAGS: yukata\n"
                "POSE_TAGS: sitting, holding, sharing\n"
                "BACKGROUND_TAGS: festival, outdoors, night\n"
                "OBJECT_TAGS: fireworks, candy_apple\n"
                "LIGHTING_TAGS: night, sparkle"
            ),
        },
        "expect": {
            "off_topic": False,
            "diff": True,
            "draft": True,
            "overall_min": 0.55,
            "multi": True,
            "topic_min": 0.45,
        },
    },
}


@pytest.mark.parametrize("name", list(CASES))
def test_multi_topic_full_sim(name, capsys):
    sc = CASES[name]
    report = _run_case(sc)
    _print_case(report)
    exp = sc["expect"]
    g = report["gates"]
    q = report["quality"]

    assert g["candidates_degenerate"] is False
    assert g["candidates_off_topic"] is exp["off_topic"]
    assert g["should_differentiate"] is exp["diff"]
    assert g["draft_auto"] is exp["draft"]
    if exp.get("multi"):
        assert g["multi_character"] is True
        assert not any(t.endswith("_eyes") for t in report["lock"])
    assert q["overall"] >= exp["overall_min"]
    assert q["dimensions"]["topic_fit"] >= exp["topic_min"]
    assert q["dimensions"]["expression"] >= 0.5
    for axis in AXES:
        pos = report["prompts"][axis]["positive"]
        assert "SUBJECT_TAGS" not in pos  # categories stripped from Comfy payload
        assert report["prompts"][axis]["categories"]


def test_multi_topic_matrix_summary(capsys):
    """Print a compact scoreboard across all cases."""
    rows = []
    for name, sc in CASES.items():
        r = _run_case(sc)
        q = r["quality"]
        rows.append((
            name,
            q["overall"],
            q["dimensions"]["topic_fit"],
            q["dimensions"]["diversity"],
            q["dimensions"]["richness"],
            q["dimensions"]["expression"],
            r["gates"]["draft_auto"],
            r["gates"]["multi_character"],
        ))
    print("\n═══ MULTI-TOPIC SCOREBOARD ═══")
    print(f"{'case':28} {'ovr':>5} {'top':>5} {'div':>5} {'rich':>5} {'expr':>5} draft multi")
    for name, ovr, top, div, rich, expr, draft, multi in rows:
        print(
            f"{name:28} {ovr:5.2f} {top:5.2f} {div:5.2f} {rich:5.2f} {expr:5.2f} "
            f"{'Y' if draft else 'n':5} {'Y' if multi else 'n'}"
        )
    assert all(o >= 0.55 for _, o, *_ in rows)
