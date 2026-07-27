"""Causality cross-checks for StoryBundle (code facilitator)."""
from __future__ import annotations

import re
from typing import Any


def causality_present(one_liner: str) -> bool:
    return bool((one_liner or "").strip())


def _panel_visible(panel: dict[str, Any]) -> str:
    if not isinstance(panel, dict):
        return ""
    intent = panel.get("intent") if isinstance(panel.get("intent"), dict) else panel
    return str((intent or {}).get("visible_change") or "").strip()


def lint_causality(story_bundle: dict[str, Any]) -> list[dict[str, str]]:
    """Cross-beat causality defects (empty / duplicate visible_change, weak chain)."""
    defects: list[dict[str, str]] = []
    world = story_bundle.get("world") or {}
    one = str(world.get("causality_one_liner") or "").strip()
    panels = [p for p in (story_bundle.get("panels") or []) if isinstance(p, dict)]

    if one and len(one) < 8:
        defects.append({
            "code": "CAUSALITY_TOO_SHORT",
            "panel": "",
            "problem": "causality_one_liner is too short to chain 3 beats",
            "fix": "Write one sentence linking panel1→2→3",
        })

    visibles = [_panel_visible(p) for p in panels]
    for i, v in enumerate(visibles):
        key = str(panels[i].get("key") or f"panel_{i+1}")
        if not v:
            defects.append({
                "code": "VISIBLE_CHANGE_EMPTY",
                "panel": key,
                "problem": "visible_change is empty",
                "fix": "State what visibly differs in this still",
            })
    # Distinct visible changes (ignore empty)
    filled = [v for v in visibles if v]
    if len(filled) >= 2 and len(set(filled)) < len(filled):
        defects.append({
            "code": "VISIBLE_CHANGE_DUP",
            "panel": "",
            "problem": "duplicate visible_change across panels",
            "fix": "Each panel needs a distinct visible change",
        })

    # Time is the spine: three panels must sit apart on the chosen scale, not be
    # three angles on one moment (the "same story three times" failure).
    markers = [
        str(((p.get("intent") if isinstance(p.get("intent"), dict) else p) or {})
            .get("time_marker") or "").strip().lower()
        for p in panels
    ]
    filled_markers = [m for m in markers if m]
    if panels and (not filled_markers or len(set(filled_markers)) < 2):
        defects.append({
            "code": "TIME_MARKER_FLAT",
            "panel": "",
            "problem": (
                "time_marker is empty or identical across panels — the beats do not "
                "sit apart in time"
            ),
            "fix": "Give each panel a distinct time_marker spanning world.time_scale",
        })

    # Soft chain cue: one_liner should reference progression (arrows / then / て / →)
    if one and filled:
        chain_cue = bool(re.search(r"(→|->|then|そして|てから|→|→)", one, re.I))
        # Also accept comma-separated clause lists of 2+
        clause_bits = [b.strip() for b in re.split(r"[、,;/]|→|->", one) if b.strip()]
        if not chain_cue and len(clause_bits) < 2:
            defects.append({
                "code": "CAUSALITY_WEAK_CHAIN",
                "panel": "",
                "problem": "causality_one_liner does not clearly chain beats",
                "fix": "Chain panel1→2→3 in one sentence",
            })
    return defects


def causality_report(story_bundle: dict[str, Any]) -> dict[str, Any]:
    defects = lint_causality(story_bundle)
    world = story_bundle.get("world") or {}
    return {
        "present": causality_present(str(world.get("causality_one_liner") or "")),
        "defects": defects,
        "ok": not defects and causality_present(str(world.get("causality_one_liner") or "")),
    }
