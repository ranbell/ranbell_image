"""Spicer — lab-only atmospheric spice tags (LLM 0, never identity).

Enabled via ``quality_policy.spicer`` (default False).
Writes ``character.lab_spice``; compile merges into spice layer when on.
"""
from __future__ import annotations

from typing import Any

from ..character.split_tags import soft_normalize_tag

# Setting / topic cue → safe atmosphere tags (no hair/eyes/outfit).
_SPICE_LEXICON: dict[str, list[str]] = {
    "rain": ["rain", "overcast", "wet", "droplets", "melancholy_atmosphere"],
    "雨": ["rain", "overcast", "wet", "droplets"],
    "bookstore": ["dust_motes", "warm_light", "cozy", "bookshelf_bokeh"],
    "書店": ["dust_motes", "warm_light", "cozy"],
    "cafe": ["steam", "soft_lighting", "window_light", "bokeh"],
    "カフェ": ["steam", "soft_lighting", "window_light"],
    "night": ["night", "rim_light", "neon", "deep_shadow"],
    "夜": ["night", "rim_light", "deep_shadow"],
    "station": ["crowd_silhouette", "platform", "motion_blur"],
    "駅": ["crowd_silhouette", "platform"],
    "snow": ["snow", "cold_breath", "pale_light"],
    "雪": ["snow", "cold_breath"],
    "sunset": ["golden_hour", "lens_flare", "long_shadow"],
    "夕": ["golden_hour", "long_shadow"],
}

_PALETTE_SPICE: dict[str, list[str]] = {
    "warm": ["warm_light", "amber_glow"],
    "cool": ["cool_light", "blue_hour"],
    "muted": ["muted_colors", "soft_contrast"],
    "brass": ["warm_light", "metallic_glint"],
    "olive": ["muted_colors", "natural_light"],
    "cream": ["soft_lighting", "diffused_light"],
}

_GENERIC_LAB = ["atmospheric", "detailed_background", "depth_of_field"]


def is_spicer_enabled(session: dict[str, Any]) -> bool:
    policy = session.get("quality_policy") or {}
    if "spicer" in policy:
        return bool(policy.get("spicer"))
    return str(policy.get("mode") or "").lower() == "lab"


def set_spicer_enabled(session: dict[str, Any], enabled: bool) -> None:
    session.setdefault("quality_policy", {})["spicer"] = bool(enabled)


def _dedupe(tags: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        k = soft_normalize_tag(t) if str(t).isascii() else str(t).strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


def harvest_spice_tags(session: dict[str, Any], *, limit: int = 8) -> list[str]:
    """Deterministic spice from setting / topic / palette / positive prefs."""
    world = (session.get("story_bundle") or {}).get("world") or {}
    inputs = session.get("inputs") or {}
    character = session.get("character") or {}
    blob = " ".join([
        str(world.get("setting") or ""),
        str(inputs.get("topic") or ""),
        str(world.get("throughline_place") or ""),
    ])
    hits: list[str] = []
    low = blob.lower()
    for key, tags in _SPICE_LEXICON.items():
        if key.lower() in low or key in blob:
            hits.extend(tags)
    for raw in character.get("palette") or []:
        p = str(raw).lower()
        for key, tags in _PALETTE_SPICE.items():
            if key in p:
                hits.extend(tags)
    # Positive preference chips → mild atmosphere boost
    for row in session.get("preference_log") or []:
        if row.get("positive"):
            hits.extend(["pleasant_atmosphere", "soft_focus"])
            break
    if not hits:
        hits.extend(_GENERIC_LAB)
    return _dedupe(hits)[:limit]


def run_spicer(session: dict[str, Any]) -> list[str]:
    """Apply lab spice onto character.lab_spice. No-op when disabled."""
    if not is_spicer_enabled(session):
        character = session.setdefault("character", {})
        character["lab_spice"] = []
        return []
    tags = harvest_spice_tags(session)
    character = session.setdefault("character", {})
    character["lab_spice"] = tags
    return tags
