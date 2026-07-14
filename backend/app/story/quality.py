"""Chronicle multi-axis quality evaluation (rule-based, no LLM required).

Produces a ``quality_eval`` payload persisted on the story document and shown
as a radar chart in Storybook. Dimensions are 0..1; ``overall`` is their mean.
"""
from __future__ import annotations

import time
from typing import Any

from .generator import (
    AXES,
    _EXPRESSION_TAGS,
    _EXPRESSION_TOKENS,
    _mean_pairwise_similarity,
    _tag_has_dynamic_action,
    _tag_has_expression,
    _tag_has_person_subject,
    acts_temporally_distinct,
    activities_temporally_distinct,
    axis_tag_lines_collapsed,
    topic_anchor_tokens,
    _chronicle_tags_degenerate,
)

QUALITY_DIMS = (
    "topic_fit",
    "diversity",
    "expression",
    "action",
    "drawability",
    "identity",
    "richness",
)

# Place / prop cues for "drawable" scoring (EN + common JA).
_PLACE_RE_HINTS = (
    "cafe", "kitchen", "station", "park", "street", "room", "classroom",
    "counter", "window", "door", "rooftop", "bridge", "library", "shop",
    "stadium", "arena", "bicycle", "bike",
    "カフェ", "キッチン", "駅", "公園", "街", "教室", "部屋", "店", "自転車",
)
_ACTION_VERB_HINTS = (
    "pour", "hold", "reach", "wipe", "run", "write", "open", "grab",
    "lift", "push", "pull", "knead", "fold", "slide", "teach", "point",
    "pedal", "ride", "toast", "cheer", "clink", "flutter", "lean",
    "注", "持", "掴", "走", "書", "開", "押", "拭", "走", "乾杯",
)

# ── Scene richness (reference: dense golden-hour / celebration shots) ─────────
_LIGHTING_TOKENS = frozenset({
    "sunset", "sunrise", "golden_hour", "dusk", "dawn", "rim_light", "backlight",
    "backlighting", "lens_flare", "volumetric_lighting", "god_rays", "sunbeam",
    "dramatic_shadow", "long_shadow", "warm_light", "cool_light", "neon",
    "cinematic_lighting", "sidelight", "contre-jour", "glow", "sparkle",
    "afternoon", "evening", "night", "morning", "blue_hour", "daylight", "bright",
})
_ENV_TOKENS = frozenset({
    "street", "road", "alley", "cityscape", "town", "shop", "storefront", "cafe",
    "bar", "window", "building", "facade", "streetlamp", "lamppost", "sign",
    "banner", "mountain", "sky", "cloud", "stadium", "arena", "crowd", "audience",
    "bleachers", "plant", "flower", "pot", "fence", "sidewalk", "pavement",
    "outdoors", "indoors", "scenery", "city", "urban",
})
_PROP_TOKENS = frozenset({
    "bicycle", "bike", "scarf", "necktie", "ribbon", "bag", "medal", "trophy",
    "mug", "beer", "glass", "bottle", "confetti", "streamer", "flag", "ball",
    "umbrella", "phone", "book", "cup", "pitcher", "apron", "helmet",
})
_MOTION_TOKENS = frozenset({
    "riding", "pedaling", "running", "jumping", "fluttering", "waving",
    "leaning", "turning", "looking_back", "cheering", "toast", "clinking",
    "holding", "reaching", "dynamic_pose", "wind", "motion_blur", "speed_lines",
    "pouring", "spilling", "dancing", "hugging",
})
_ATMOS_TOKENS = frozenset({
    "confetti", "streamer", "wind", "dust", "particle", "sparkle", "bokeh",
    "haze", "fog", "smoke", "petals", "leaves", "rain", "snow",
})


def _token_hits(parts: list[str], vocab: frozenset[str]) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        t = raw.strip().lower().replace(" ", "_").replace("-", "_")
        toks = set(t.split("_"))
        matched = None
        if t in vocab:
            matched = t
        else:
            for v in vocab:
                if v in toks or v in t:
                    matched = v
                    break
        if matched and matched not in seen:
            seen.add(matched)
            hits.append(matched)
    return hits


def score_prompt_richness(tag_line: str) -> dict[str, Any]:
    """Score one prompt for reference-grade visual richness (0..1 + breakdown).

    Tuned against dense scenes: golden-hour bicycle street, stadium celebration
    with confetti — lighting, expression, motion, environment density, props.
    """
    parts = _parts(tag_line)
    if not parts:
        return {
            "score": 0.0,
            "lighting": 0, "environment": 0, "props": 0,
            "motion": 0, "atmosphere": 0, "expression": False,
            "tag_count": 0,
        }
    lighting = _token_hits(parts, _LIGHTING_TOKENS)
    environment = _token_hits(parts, _ENV_TOKENS)
    props = _token_hits(parts, _PROP_TOKENS)
    motion = _token_hits(parts, _MOTION_TOKENS)
    atmosphere = _token_hits(parts, _ATMOS_TOKENS)
    has_expr = _tag_has_expression(parts)

    # Soft caps — reference images typically clear these floors.
    light_s = _clamp01(len(lighting) / 3.0)
    env_s = _clamp01(len(environment) / 4.0)
    prop_s = _clamp01(len(props) / 3.0)
    motion_s = _clamp01(len(motion) / 3.0)
    atmos_s = _clamp01(len(atmosphere) / 2.0)
    expr_s = 1.0 if has_expr else 0.0
    density_s = _clamp01((len(parts) - 25) / 35.0)  # 25→0, 60→1

    score = _clamp01(
        0.18 * light_s
        + 0.18 * env_s
        + 0.14 * prop_s
        + 0.16 * motion_s
        + 0.10 * atmos_s
        + 0.14 * expr_s
        + 0.10 * density_s
    )
    return {
        "score": round(score, 3),
        "lighting": len(lighting),
        "environment": len(environment),
        "props": len(props),
        "motion": len(motion),
        "atmosphere": len(atmosphere),
        "expression": has_expr,
        "tag_count": len(parts),
        "hits": {
            "lighting": lighting,
            "environment": environment,
            "props": props,
            "motion": motion,
            "atmosphere": atmosphere,
        },
    }


def _score_richness(prompts: dict[str, str]) -> tuple[float, dict[str, Any]]:
    per: dict[str, Any] = {}
    scores: list[float] = []
    for a in AXES:
        detail = score_prompt_richness(prompts.get(a) or "")
        per[a] = detail
        scores.append(float(detail["score"]))
    return _clamp01(sum(scores) / max(1, len(scores))), per


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _parts(tag_line: str) -> list[str]:
    return [t.strip() for t in (tag_line or "").split(",") if t.strip()]


def _score_topic_fit(
    *,
    user_topic: str,
    title: str,
    overall: str,
    stories: dict[str, str],
    activities: dict[str, str],
) -> tuple[float, str]:
    topic = (user_topic or "").strip()
    if not topic:
        return 0.7, "no_topic"  # neutral — free improvisation is allowed
    tokens = topic_anchor_tokens(topic)
    if not tokens:
        return 0.7, "no_tokens"
    blob = " ".join(
        [
            title or "",
            overall or "",
            *(stories.get(a) or "" for a in AXES),
            *(activities.get(a) or "" for a in AXES),
        ]
    ).lower()
    hits = sum(1 for tok in tokens if tok in blob)
    ratio = hits / max(1, len(tokens))
    # Soft curve: 1 token hit → ~0.45, half → ~0.7, all → 1.0
    score = _clamp01(0.25 + 0.75 * ratio)
    return score, f"hits={hits}/{len(tokens)}"


def _score_diversity(
    *,
    stories: dict[str, str],
    activities: dict[str, str],
    prompts: dict[str, str],
    time_scale: str,
) -> tuple[float, str]:
    story_sim = _mean_pairwise_similarity([stories.get(a) or "" for a in AXES])
    act_sim = _mean_pairwise_similarity([activities.get(a) or "" for a in AXES])
    tag_collapse = axis_tag_lines_collapsed(prompts)
    # Invert similarity → diversity. Micro scales expect higher similarity.
    micro = (time_scale or "").strip().lower() in {"minutes", "tens_of_minutes"}
    story_div = 1.0 - story_sim
    act_div = 1.0 - act_sim
    raw = 0.45 * story_div + 0.35 * act_div + (0.0 if tag_collapse else 0.20)
    if micro:
        # Don't punish near-duplicates on micro scales as hard.
        raw = 0.55 + 0.45 * raw
    note = (
        f"story_sim={story_sim:.2f} act_sim={act_sim:.2f} "
        f"tag_collapse={tag_collapse}"
    )
    # Soft floor when acts_temporally_distinct fails on long scales.
    if not micro and not acts_temporally_distinct(stories):
        raw = min(raw, 0.35)
        note += " acts_collapsed"
    return _clamp01(raw), note


def _score_expression(prompts: dict[str, str]) -> tuple[float, dict[str, Any]]:
    per: dict[str, Any] = {}
    scores: list[float] = []
    for a in AXES:
        parts = _parts(prompts.get(a) or "")
        if not parts:
            per[a] = {"person": False, "ok": True, "score": 1.0}
            scores.append(1.0)
            continue
        person = _tag_has_person_subject(parts)
        ok = (not person) or _tag_has_expression(parts)
        # Bonus when multiple distinct expression tags appear.
        expr_n = sum(
            1 for p in parts
            if p.lower().replace(" ", "_") in _EXPRESSION_TAGS
            or (set(p.lower().replace("-", "_").split("_")) & _EXPRESSION_TOKENS)
        )
        s = 1.0 if ok else 0.15
        if ok and person and expr_n >= 2:
            s = 1.0
        elif ok and person:
            s = 0.85
        per[a] = {"person": person, "ok": ok, "expr_count": expr_n, "score": s}
        scores.append(s)
    return _clamp01(sum(scores) / max(1, len(scores))), per


def _score_action(prompts: dict[str, str], activities: dict[str, str]) -> tuple[float, dict]:
    per: dict[str, Any] = {}
    scores: list[float] = []
    for a in AXES:
        parts = _parts(prompts.get(a) or "")
        act_text = (activities.get(a) or "").lower()
        has_tag_action = _tag_has_dynamic_action(parts) if parts else False
        has_text_action = any(v in act_text for v in _ACTION_VERB_HINTS)
        deg, reason = _chronicle_tags_degenerate(prompts.get(a) or "") if parts else (True, "empty")
        if has_tag_action and has_text_action:
            s = 1.0
        elif has_tag_action or has_text_action:
            s = 0.7
        else:
            s = 0.2
        if deg and reason == "no_dynamic_action":
            s = min(s, 0.25)
        per[a] = {
            "tag_action": has_tag_action,
            "text_action": has_text_action,
            "score": s,
        }
        scores.append(s)
    return _clamp01(sum(scores) / max(1, len(scores))), per


def _score_drawability(
    activities: dict[str, str],
    stories: dict[str, str],
) -> tuple[float, str]:
    scores: list[float] = []
    for a in AXES:
        text = f"{activities.get(a) or ''} {stories.get(a) or ''}".lower()
        if not text.strip():
            scores.append(0.2)
            continue
        has_place = any(p in text for p in _PLACE_RE_HINTS)
        has_verb = any(v in text for v in _ACTION_VERB_HINTS)
        length_ok = len(text.strip()) >= 24
        s = 0.2
        if has_verb:
            s += 0.4
        if has_place:
            s += 0.25
        if length_ok:
            s += 0.15
        scores.append(_clamp01(s))
    distinct = activities_temporally_distinct(activities) if any(activities.values()) else True
    mean = sum(scores) / max(1, len(scores))
    if not distinct:
        mean = min(mean, 0.45)
    return _clamp01(mean), f"acts_distinct={distinct}"


def _score_identity(
    prompts: dict[str, str],
    lock_tags: list[str] | None,
) -> tuple[float, str]:
    locks = [
        str(t).strip().lower().replace(" ", "_")
        for t in (lock_tags or [])
        if t
    ]
    if not locks:
        # No lock available — score presence of any hair/eye colour cue.
        hits = 0
        for a in AXES:
            low = (prompts.get(a) or "").lower()
            if "_hair" in low or "_eyes" in low:
                hits += 1
        return _clamp01(hits / 3.0), "heuristic_hair_eyes"
    scores: list[float] = []
    for a in AXES:
        low = {
            t.strip().lower().replace(" ", "_")
            for t in (prompts.get(a) or "").split(",")
            if t.strip()
        }
        if not low:
            scores.append(0.0)
            continue
        present = sum(1 for t in locks if t in low)
        scores.append(present / max(1, len(locks)))
    return _clamp01(sum(scores) / max(1, len(scores))), f"locks={len(locks)}"


def evaluate_chronicle_quality(
    *,
    user_topic: str = "",
    title: str = "",
    overall: str = "",
    stories: dict[str, str] | None = None,
    activities: dict[str, str] | None = None,
    prompts: dict[str, str] | None = None,
    time_scale: str = "years",
    lock_tags: list[str] | None = None,
    method: str = "rules",
) -> dict[str, Any]:
    """Return a ``quality_eval`` dict ready to persist on the story payload."""
    stories = stories or {}
    activities = activities or {}
    # Normalise prompts: accept either raw strings or {positive, negative} dicts.
    norm_prompts: dict[str, str] = {}
    for a in AXES:
        raw = (prompts or {}).get(a)
        if isinstance(raw, dict):
            norm_prompts[a] = str(raw.get("positive") or "")
        else:
            norm_prompts[a] = str(raw or "")

    topic_fit, topic_note = _score_topic_fit(
        user_topic=user_topic,
        title=title,
        overall=overall,
        stories=stories,
        activities=activities,
    )
    diversity, div_note = _score_diversity(
        stories=stories,
        activities=activities,
        prompts=norm_prompts,
        time_scale=time_scale,
    )
    expression, expr_per = _score_expression(norm_prompts)
    action, action_per = _score_action(norm_prompts, activities)
    drawability, draw_note = _score_drawability(activities, stories)
    identity, id_note = _score_identity(norm_prompts, lock_tags)
    richness, rich_per = _score_richness(norm_prompts)

    dimensions = {
        "topic_fit": round(topic_fit, 3),
        "diversity": round(diversity, 3),
        "expression": round(expression, 3),
        "action": round(action, 3),
        "drawability": round(drawability, 3),
        "identity": round(identity, 3),
        "richness": round(richness, 3),
    }
    overall_score = round(
        sum(dimensions[d] for d in QUALITY_DIMS) / len(QUALITY_DIMS), 3
    )

    return {
        "version": 1,
        "evaluated_at": time.time(),
        "method": method,
        "overall": overall_score,
        "dimensions": dimensions,
        "per_axis": {
            "expression": expr_per,
            "action": action_per,
            "richness": rich_per,
        },
        "notes": {
            "topic_fit": topic_note,
            "diversity": div_note,
            "drawability": draw_note,
            "identity": id_note,
            "richness": f"mean={richness:.2f}",
        },
    }
