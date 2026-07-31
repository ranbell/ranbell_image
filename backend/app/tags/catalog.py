"""Shared Danbooru tag taxonomy from ``static/tag_categories.json``.

Used by Inspire (Phase A frozenset classification), expression
guards / quality scoring, and tests — so JSON remains the single source of
truth (no hand-copied expression lists).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

_JSON_PATH = Path(__file__).resolve().parent.parent / "static" / "tag_categories.json"

TAG_DATA: dict = json.loads(_JSON_PATH.read_text(encoding="utf-8"))


def fs(*keys: str) -> frozenset[str]:
    """Retrieve tags at the given JSON key path as a frozenset."""
    d: dict | list = TAG_DATA
    for k in keys:
        d = d[k]  # type: ignore[index]
    return frozenset(d)  # type: ignore[arg-type]


# ── Frozenset mirrors of tag_categories.json ──────────────────────────────────
COUNT = fs("always_fixed", "count")
EYE_SHAPES = fs("always_fixed", "eye_shapes")
BODY = fs("always_fixed", "body")
SKIN_FACE = fs("always_fixed", "skin_face")
RACE = fs("always_fixed", "race")
COMPOSITION = fs("always_fixed", "composition")
PROPS = fs("always_fixed", "props")
HAIR_STYLES = fs("axis_hair")
EXPRESSION = fs("axis_emotion")
POSE = fs("axis_action")
CLOTHING_EXPLICIT = fs("axis_clothing_explicit")
ACCESSORIES = fs("axis_accessories")
BODY_PARTS = fs("axis_parts")
ART_STYLE = fs("axis_art_style", "volatile") | fs("axis_art_style", "always_fixed")
ENVIRONMENT = (
    fs("axis_environment", "visual_lighting") | fs("axis_environment", "time_weather")
)
BACKGROUND = fs("axis_background", "abstract") | fs("axis_background", "location")

CLOTHING_SUFFIXES: tuple[str, ...] = tuple(TAG_DATA["patterns"]["clothing_suffixes"])
ACTION_KEYWORDS: tuple[str, ...] = tuple(TAG_DATA["patterns"]["action_keywords"])

STYLE_ALWAYS_FIXED = fs("axis_art_style", "always_fixed")
VISUAL_LIGHTING = fs("axis_environment", "visual_lighting")
ABSTRACT_BG = fs("axis_background", "abstract")

# Full emotion axis from JSON.
EXPRESSION_TAGS: frozenset[str] = EXPRESSION

# Substring tokens for soft expression detection in tag parts.
EXPRESSION_TOKENS: frozenset[str] = frozenset({
    "smile", "smiling", "grin", "laugh", "tear", "tears", "teary", "sob",
    "blush", "frown", "pout", "smirk", "wink", "angry", "sad", "shy",
    "nervous", "worried", "expressionless", "serious", "stoic", "gasp",
    "smug", "melancholy", "nostalgic", "pensive", "flustered", "embarrassed",
    "scared", "terrified", "panicked", "relieved", "focused", "cheerful",
    "joyful", "lonely", "annoyed", "glaring", "wonder", "concentrating",
    "determined", "awe",
})


def build_display_group_map() -> dict[str, str]:
    """tag → UI display group (first match wins)."""
    result: dict[str, str] = {}
    for entry in TAG_DATA.get("display_category_map", []):
        label = entry["label"]
        path = entry["source"].split(".")
        node: dict | list = TAG_DATA
        for key in path:
            if isinstance(node, dict):
                node = node.get(key, [])
            else:
                node = []
        if isinstance(node, list):
            for tag in node:
                if tag not in result:
                    result[tag] = label
    return result


def build_tag_to_axis(
    *,
    extra_always_fixed: Iterable[str] = (),
) -> dict[str, str]:
    """Build tag→axis map from JSON (+ optional always_fixed extras e.g. WD14 char names)."""
    m: dict[str, str] = {}

    for tag in extra_always_fixed:
        m[str(tag).lower()] = "always_fixed"

    for tag in (
        "general", "sensitive", "explicit", "safe", "nsfw",
        "questionable", "rating_safe", "rating_explicit", "rating_general",
    ):
        m[tag] = "always_fixed"

    for tag in (*COUNT, *EYE_SHAPES, *BODY, *SKIN_FACE, *RACE, *COMPOSITION, *PROPS):
        m[tag] = "always_fixed"

    for tag in ART_STYLE:
        m[tag] = "always_fixed" if tag in STYLE_ALWAYS_FIXED else "style"

    for tag in HAIR_STYLES:
        m[tag] = "hair"
    for tag in EXPRESSION:
        m[tag] = "emotion"
    for tag in POSE:
        m[tag] = "action"
    for tag in ACCESSORIES:
        m[tag] = "clothing"
    for tag in CLOTHING_EXPLICIT:
        m[tag] = "clothing"
    for tag in BODY_PARTS:
        m[tag] = "parts"

    for tag in ENVIRONMENT:
        m[tag] = "visual" if tag in VISUAL_LIGHTING else "time_weather"
    for tag in BACKGROUND:
        m[tag] = "visual" if tag in ABSTRACT_BG else "location"

    return m


# Default map (JSON only — no WD14 CSV). Inspire merges character names on top.
TAG_TO_AXIS: dict[str, str] = build_tag_to_axis()



# Parts that end like verbs but are nouns/visuals — never pose/action tags.
_POSE_DENY_SUFFIXES: tuple[str, ...] = (
    "ring", "lighting", "censoring", "piercing", "clothing", "building",
    "ceiling", "string", "wedding", "morning", "evening", "padding", "legwear",
)
_POSE_DENY_EXACT: frozenset[str] = frozenset({
    "glowing", "glowing_eye", "glowing_eyes", "foreshortening", "snowing",
    "raining", "steaming_body", "falling_petals", "falling_leaves",
    "falling_snow", "folding_fan", "center_opening", "bodystocking", "rigging",
    "clothes_writing", "revealing_clothes", "landing", "loading_screen",
    "viewing", "sleeping_bag",
})


def get_tag_axis(
    tag: str,
    *,
    mapping: dict[str, str] | None = None,
) -> str | None:
    """Return axis for a tag, or None if unknown.

    Known frozenset entries win before suffix heuristics. Expressive ``*_eyes``
    listed under axis_emotion stay emotion; unknown ``*_eyes`` → always_fixed.
    """
    m = mapping if mapping is not None else TAG_TO_AXIS
    mapped = m.get(tag)
    if mapped is not None:
        return mapped
    if tag.endswith("_hair"):
        return "hair"
    if tag.endswith("_eyes"):
        return "always_fixed"
    if any(tag.endswith(s) for s in CLOTHING_SUFFIXES):
        return "clothing"
    if any(kw in tag for kw in ACTION_KEYWORDS):
        return "action"
    return None
