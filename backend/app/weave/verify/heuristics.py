"""Framing heuristics for Weave Look-dev samples (M3).

Uses WD14 tags on the generated sample — no heavy face detector.
long_shot that looks like a close portrait → framing=fail.
"""
from __future__ import annotations

from typing import Any

from ..character.split_tags import soft_normalize_tag

_CLOSE = frozenset({
    "close-up", "close_up", "portrait", "face", "solo_focus",
    "looking_at_viewer", "facial", "detailed_face",
})
_WIDE = frozenset({
    "full_body", "wide_shot", "long_shot", "scenery", "outdoors",
    "landscape", "from_distance", "cowboy_shot", "full_shot",
    "far_away", "huge_filesize",  # not wide but harmless
})


def evaluate_long_shot_framing(wd14_tags: list[str] | None) -> str:
    """Return pass | fail | unknown for a long_shot sample."""
    tags = {soft_normalize_tag(t) for t in (wd14_tags or []) if t}
    if not tags:
        return "unknown"
    close_hits = tags & _CLOSE
    wide_hits = tags & _WIDE
    # Explicit close markers without wide context → fail
    if ("close-up" in tags or "close_up" in tags or "portrait" in tags) and not wide_hits:
        return "fail"
    if close_hits and not wide_hits and "full_body" not in tags:
        return "fail"
    return "pass"


def evaluate_sample_framing(
    camera: str,
    wd14_tags: list[str] | None = None,
) -> str:
    """Framing result for any camera. Non-long_shot always pass for now."""
    cam = soft_normalize_tag(camera).replace("-", "_")
    if cam != "long_shot":
        return "pass"
    return evaluate_long_shot_framing(wd14_tags)


def apply_framing_to_panel(panel: dict[str, Any], wd14_tags: list[str] | None) -> str:
    cam = str((panel.get("intent") or {}).get("camera") or "")
    result = evaluate_sample_framing(cam, wd14_tags)
    panel.setdefault("qa", {})["framing"] = result
    if result == "fail":
        panel["framing_fail_count"] = int(panel.get("framing_fail_count") or 0) + 1
    return result
