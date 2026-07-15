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
    _chronicle_tags_degenerate,
)
from .topic_anchors import _is_ja_script_token, topic_anchor_groups

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
    "stadium", "arena", "bicycle", "bike", "festival", "stall", "lantern",
    "beach", "ocean", "seaside",
    "カフェ", "キッチン", "駅", "公園", "街", "教室", "部屋", "店", "自転車",
    "祭り", "屋台", "海辺",
)
_ACTION_VERB_HINTS = (
    "pour", "hold", "reach", "wipe", "run", "write", "open", "grab",
    "lift", "push", "pull", "knead", "fold", "slide", "teach", "point",
    "pedal", "ride", "toast", "cheer", "clink", "flutter", "lean",
    "race", "share", "buy", "scoop", "kick", "chase", "crouch",
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


def _score_richness(
    prompts: dict[str, str],
    scored_axes: list[str] | None = None,
) -> tuple[float, dict[str, Any]]:
    per: dict[str, Any] = {}
    scores: list[float] = []
    axes = [a for a in (scored_axes or list(AXES)) if a in AXES] or list(AXES)
    for a in axes:
        line = prompts.get(a) or ""
        if not line.strip():
            per[a] = {"score": None, "skipped": True}
            continue
        detail = score_prompt_richness(line)
        per[a] = detail
        scores.append(float(detail["score"]))
    if not scores:
        return 0.5, per
    return _clamp01(sum(scores) / len(scores)), per


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _coerce_text(v: Any) -> str:
    """Normalise list / dict / scalar story fields to a plain string."""
    if isinstance(v, list):
        return " ".join(_coerce_text(x) for x in v)
    if isinstance(v, dict):
        return str(v.get("positive") or "")
    if v is None:
        return ""
    return str(v)


def quality_eval_failure(exc: BaseException | str, *, method: str = "rules") -> dict:
    """Stub persisted when scoring fails — Storybook must not crash."""
    msg = str(exc).strip() or type(exc).__name__ if not isinstance(exc, str) else exc
    return {
        "version": 1,
        "evaluated_at": time.time(),
        "method": method,
        "ok": False,
        "error": msg[:500],
        "overall": None,
        "dimensions": None,
        "per_axis": {},
        "notes": {"error": msg[:500]},
        "scored_axes": [],
    }


def _parts(tag_line: str) -> list[str]:
    return [t.strip() for t in (tag_line or "").split(",") if t.strip()]


def _score_topic_fit(
    *,
    user_topic: str,
    title: str,
    overall: str,
    stories: dict[str, str],
    activities: dict[str, str],
    prompts: dict[str, str] | None = None,
    topic_directive: str = "",
) -> tuple[float, str]:
    topic = (user_topic or "").strip()
    if not topic:
        return 0.55, "no_topic"  # mild penalty — free improvisation allowed
    groups = topic_anchor_groups(topic, topic_directive)
    if not groups:
        return 0.55, "no_tokens"
    blob = " ".join(
        [
            title or "",
            overall or "",
            *(stories.get(a) or "" for a in AXES),
            *(activities.get(a) or "" for a in AXES),
            *((prompts or {}).get(a) or "" for a in AXES),
        ]
    ).lower().replace("_", " ")
    # English-only blobs: ignore JA-script members so aliases are not diluted
    # (カフェ never appears in EN prose; cafe/coffee/barista do).
    blob_has_ja = any(_is_ja_script_token(c) for c in blob)
    hits = 0
    for group in groups:
        if not blob_has_ja:
            members = [t for t in group if not _is_ja_script_token(t)] or group
        else:
            members = group
        if any(
            tok.replace("_", " ") in blob or tok in blob
            for tok in members
        ):
            hits += 1
    ratio = hits / max(1, len(groups))
    # Soft curve: 1 group hit → ~0.45+, half → ~0.7, all → 1.0
    score = _clamp01(0.25 + 0.75 * ratio)
    return score, f"hits={hits}/{len(groups)}"


def _score_diversity(
    *,
    stories: dict[str, str],
    activities: dict[str, str],
    prompts: dict[str, str],
    time_scale: str,
    scored_axes: list[str] | None = None,
) -> tuple[float, str]:
    axes = [a for a in (scored_axes or list(AXES)) if a in AXES] or list(AXES)
    story_sim = _mean_pairwise_similarity([stories.get(a) or "" for a in axes])
    act_sim = _mean_pairwise_similarity([activities.get(a) or "" for a in axes])
    tag_collapse = axis_tag_lines_collapsed({a: prompts.get(a) or "" for a in axes})
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
    if not micro and not acts_temporally_distinct({a: stories.get(a) or "" for a in axes}):
        raw = min(raw, 0.35)
        note += " acts_collapsed"
    return _clamp01(raw), note


def _score_expression(
    prompts: dict[str, str],
    scored_axes: list[str] | None = None,
) -> tuple[float, dict[str, Any]]:
    per: dict[str, Any] = {}
    scores: list[float] = []
    axes = [a for a in (scored_axes or list(AXES)) if a in AXES] or list(AXES)
    for a in axes:
        parts = _parts(prompts.get(a) or "")
        if not parts:
            # Base-image axes with no generated prompt — skip (do not award 1.0).
            per[a] = {"person": False, "ok": False, "score": None, "skipped": True}
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
    if not scores:
        return 0.5, per
    return _clamp01(sum(scores) / len(scores)), per


def _score_action(
    prompts: dict[str, str],
    activities: dict[str, str],
    scored_axes: list[str] | None = None,
) -> tuple[float, dict]:
    per: dict[str, Any] = {}
    scores: list[float] = []
    axes = [a for a in (scored_axes or list(AXES)) if a in AXES] or list(AXES)
    for a in axes:
        parts = _parts(prompts.get(a) or "")
        act_text = (activities.get(a) or "").lower()
        if not parts and not act_text.strip():
            per[a] = {"score": None, "skipped": True}
            continue
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
    if not scores:
        return 0.5, per
    return _clamp01(sum(scores) / len(scores)), per


def _score_drawability(
    activities: dict[str, str],
    stories: dict[str, str],
    scored_axes: list[str] | None = None,
) -> tuple[float, str]:
    scores: list[float] = []
    axes = [a for a in (scored_axes or list(AXES)) if a in AXES] or list(AXES)
    for a in axes:
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
    act_slice = {a: activities.get(a) or "" for a in axes}
    distinct = activities_temporally_distinct(act_slice) if any(act_slice.values()) else True
    mean = sum(scores) / max(1, len(scores))
    if not distinct:
        mean = min(mean, 0.45)
    return _clamp01(mean), f"acts_distinct={distinct}"


def _score_identity(
    prompts: dict[str, str],
    lock_tags: list[str] | None,
    scored_axes: list[str] | None = None,
) -> tuple[float, str]:
    locks = [
        str(t).strip().lower().replace(" ", "_")
        for t in (lock_tags or [])
        if t
    ]
    if not locks:
        # No lock — common for multi-character bases (hair/eyes dropped).
        # Prefer multi-subject anchors; else fall back to any hair/eye cue.
        axes = [a for a in (scored_axes or list(AXES)) if a in AXES] or list(AXES)
        multi_hits = 0
        hair_eye_hits = 0
        for a in axes:
            low = (prompts.get(a) or "").lower()
            if any(
                m in low
                for m in ("3girls", "2girls", "multiple_girls", "multiple_boys", "2boys")
            ):
                multi_hits += 1
            if "_hair" in low or "_eyes" in low:
                hair_eye_hits += 1
        n = max(1, len(axes))
        if multi_hits:
            return _clamp01(multi_hits / n), "multi_subject_anchor"
        return _clamp01(hair_eye_hits / n), "heuristic_hair_eyes"
    axes = [a for a in (scored_axes or list(AXES)) if a in AXES] or list(AXES)
    scores: list[float] = []
    for a in axes:
        low = {
            t.strip().lower().replace(" ", "_")
            for t in (prompts.get(a) or "").split(",")
            if t.strip()
        }
        if not low:
            continue  # skip empty base-axis prompts
        present = sum(1 for t in locks if t in low)
        scores.append(present / max(1, len(locks)))
    if not scores:
        return 0.5, f"locks={len(locks)}"
    return _clamp01(sum(scores) / len(scores)), f"locks={len(locks)}"


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
    draft_deltas: dict[str, dict] | None = None,
    scored_axes: list[str] | None = None,
    topic_directive: str = "",
) -> dict[str, Any]:
    """Return a ``quality_eval`` dict ready to persist on the story payload.

    ``scored_axes``: axes that received prompt generation (excludes base-image
    reuse). Empty-prompt axes are skipped rather than scored as perfect.

    ``draft_deltas``: per-axis richness before/after Phase B — also boosts the
    richness dimension when the image model contributed expression.
    """
    user_topic = _coerce_text(user_topic)
    title = _coerce_text(title)
    overall = _coerce_text(overall)
    topic_directive = _coerce_text(topic_directive)
    stories = {k: _coerce_text(v) for k, v in (stories or {}).items()}
    activities = {k: _coerce_text(v) for k, v in (activities or {}).items()}
    # Normalise prompts: accept either raw strings or {positive, negative} dicts.
    norm_prompts: dict[str, str] = {}
    for a in AXES:
        raw = (prompts or {}).get(a)
        if isinstance(raw, dict):
            norm_prompts[a] = str(raw.get("positive") or "")
        else:
            norm_prompts[a] = _coerce_text(raw)

    axes = [a for a in (scored_axes or list(AXES)) if a in AXES] or list(AXES)

    topic_fit, topic_note = _score_topic_fit(
        user_topic=user_topic,
        title=title,
        overall=overall,
        stories=stories,
        activities=activities,
        prompts=norm_prompts,
        topic_directive=topic_directive,
    )
    diversity, div_note = _score_diversity(
        stories=stories,
        activities=activities,
        prompts=norm_prompts,
        time_scale=time_scale,
        scored_axes=axes,
    )
    expression, expr_per = _score_expression(norm_prompts, scored_axes=axes)
    action, action_per = _score_action(norm_prompts, activities, scored_axes=axes)
    drawability, draw_note = _score_drawability(activities, stories, scored_axes=axes)
    identity, id_note = _score_identity(norm_prompts, lock_tags, scored_axes=axes)
    richness, rich_per = _score_richness(norm_prompts, scored_axes=axes)

    draft_boost = 0.0
    draft_note = ""
    draft_per: dict[str, Any] = {}
    if draft_deltas:
        deltas: list[float] = []
        for axis, d in draft_deltas.items():
            if not isinstance(d, dict):
                continue
            draft_per[axis] = d
            try:
                deltas.append(float(d.get("delta", 0.0)))
            except (TypeError, ValueError):
                pass
        if deltas:
            mean_d = sum(deltas) / len(deltas)
            draft_boost = _clamp01(mean_d / 12.0) * 0.25
            richness = _clamp01(richness + draft_boost)
            draft_note = (
                f"draft_refine axes={len(deltas)} mean_delta={mean_d:+.2f} "
                f"boost={draft_boost:+.2f}"
            )

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

    notes = {
        "topic_fit": topic_note,
        "diversity": div_note,
        "drawability": draw_note,
        "identity": id_note,
        "richness": f"mean={richness:.2f}",
        "scored_axes": ",".join(axes),
    }
    if draft_note:
        notes["draft_grounding"] = draft_note

    out: dict[str, Any] = {
        "version": 1,
        "evaluated_at": time.time(),
        "method": method,
        "ok": True,
        "overall": overall_score,
        "dimensions": dimensions,
        "per_axis": {
            "expression": expr_per,
            "action": action_per,
            "richness": rich_per,
        },
        "notes": notes,
        "scored_axes": axes,
    }
    if draft_per:
        out["per_axis"]["draft_richness"] = draft_per
        out["draft_grounding"] = {
            "axes": list(draft_per.keys()),
            "mean_delta": round(
                sum(float(d.get("delta", 0.0)) for d in draft_per.values())
                / max(1, len(draft_per)),
                3,
            ),
            "richness_boost": round(draft_boost, 3),
        }
    return out
