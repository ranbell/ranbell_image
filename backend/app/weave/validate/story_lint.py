"""Aggregate story-bundle lint."""
from __future__ import annotations

from typing import Any

from .cameras import lint_cameras
from .drawability import lint_drawability
from .must_show_resolve import apply_must_show_resolution


def lint_story_bundle(
    story_bundle: dict[str, Any],
    character: dict[str, Any],
) -> dict[str, Any]:
    """Resolve must_show, run lints. Mutates story_bundle panels."""
    unresolved = apply_must_show_resolution(story_bundle, character)
    defects: list[dict[str, str]] = []
    world = story_bundle.get("world") or {}
    for field in (
        "setting", "core_conflict", "ending_intent",
        "throughline_place", "throughline_prop", "causality_one_liner",
    ):
        if not str(world.get(field) or "").strip():
            defects.append({
                "code": "WORLD_MISSING",
                "panel": "",
                "fix": f"world.{field} is empty",
                "fix": f"Fill world.{field}",
            })
    for u in unresolved:
        defects.append({
            "code": "MUST_SHOW_UNRESOLVED",
            "panel": "",
            "fix": f"cannot resolve must_show key: {u}",
            "fix": "Set throughline_prop/place or signature_prop",
        })

    panels = story_bundle.get("panels") or []
    defects.extend(lint_cameras(panels))

    # Normalize panel shape: Storywright may emit flat panels or intent-wrapped.
    flat_panels: list[dict[str, Any]] = []
    for p in panels:
        if not isinstance(p, dict):
            continue
        if "intent" in p and isinstance(p["intent"], dict):
            merged = {**p["intent"], "key": p.get("key"), "must_show_resolved": p["intent"].get("must_show_resolved") or p.get("must_show_resolved")}
            # keep resolved on intent
            if p.get("must_show_resolved"):
                p["intent"]["must_show_resolved"] = p["must_show_resolved"]
            flat_panels.append(merged)
        else:
            flat_panels.append(p)

    for p in flat_panels:
        defects.extend(lint_drawability(p))

    # Throughline coverage: every panel must have resolved tags
    coverage = 0
    for p in panels:
        if not isinstance(p, dict):
            continue
        intent = p.get("intent") if isinstance(p.get("intent"), dict) else p
        resolved = (
            (intent or {}).get("must_show_resolved")
            or p.get("must_show_resolved")
            or []
        )
        if resolved:
            coverage += 1
    if panels and coverage < min(3, len(panels)):
        defects.append({
            "code": "THROUGHLINE_GAP",
            "panel": "",
            "fix": f"throughline resolved on {coverage}/{len(panels)} panels",
            "fix": "Ensure must_show resolves on every panel",
        })

    return {
        "pass": not defects,
        "defects": defects,
        "throughline_coverage": (coverage / 3.0) if panels else 0.0,
    }
