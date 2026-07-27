"""Recreate reason chips → imperative constraint sentences (P2)."""
from __future__ import annotations

from typing import Iterable

# Chip id → imperative English template for Storywright
CHIP_TEMPLATES: dict[str, str] = {
    "weak_plot": (
        "Put one visible external event in panel_2 that changes the prop or place state."
    ),
    "too_dark": (
        "Keep tone warm; ending_intent must be quiet hope without tragedy."
    ),
    "place_scatters": (
        "Keep throughline_place identical in all panels; no location jump."
    ),
    "weak_prop": (
        "Show signature_prop visibly in every panel must_show."
    ),
    "cliche": (
        "Avoid stock motifs from avoid_motifs; shift setting detail or ending focus."
    ),
    "more_everyday": (
        "No external accident; advance only by the character's visible action or feeling."
    ),
    "more_incident": (
        "Single physical or environmental accident in panel_2 with a visible aftermath in panel_3."
    ),
    "unclear_story": (
        "Rewrite so each panel's visible_change alone explains the causal chain."
    ),
    "off_topic": (
        "Rebuild the story so the USER TOPIC is the situation, the place and the "
        "season. The character's usual scene and usual prop must not replace it."
    ),
    "same_moment": (
        "The three panels must sit time_scale apart, not be three angles on one "
        "moment. Give each a distinct time_marker and change the place's state."
    ),
}

# Japanese UI labels mapped to chip ids
CHIP_ALIASES: dict[str, str] = {
    "展開が弱い": "weak_plot",
    "暗い": "too_dark",
    "重い": "too_dark",
    "場所が散る": "place_scatters",
    "小道具が弱い": "weak_prop",
    "ありきたり": "cliche",
    "もっと日常": "more_everyday",
    "もっと事件": "more_incident",
    "話がわからない": "unclear_story",
    "お題と違う": "off_topic",
    "同じ場面ばかり": "same_moment",
}


def chips_to_constraints(
    chips: Iterable[str],
    *,
    current_motifs: list[str] | None = None,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    motifs = ", ".join(current_motifs or []) or "rainy_window_stare, empty_classroom_farewell"
    for raw in chips:
        chip = (raw or "").strip()
        if not chip:
            continue
        cid = CHIP_ALIASES.get(chip, chip)
        if cid in seen:
            continue
        seen.add(cid)
        tmpl = CHIP_TEMPLATES.get(cid)
        if not tmpl:
            # Free-text fallback: treat as an imperative if it looks like one.
            out.append(chip if chip.endswith(".") else f"{chip}.")
            continue
        if cid == "cliche":
            out.append(tmpl.replace("avoid_motifs", motifs))
        else:
            out.append(tmpl)
    return out
