"""Framing heuristics for Weave Look-dev samples (M3).

Uses WD14 tags on the generated sample — no heavy face detector.
long_shot that looks like a close portrait → framing=fail.
Missing tags → framing=unknown (must NOT count as G4 pass).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..character.split_tags import soft_normalize_tag

_CLOSE = frozenset({
    "close-up", "close_up", "portrait", "face", "solo_focus",
    "looking_at_viewer", "facial", "detailed_face",
})
_WIDE = frozenset({
    "full_body", "wide_shot", "long_shot", "scenery", "outdoors",
    "landscape", "from_distance", "cowboy_shot", "full_shot",
    "far_away",
})


def evaluate_long_shot_framing(wd14_tags: list[str] | None) -> str:
    """Return pass | fail | unknown for a long_shot sample."""
    tags = {soft_normalize_tag(t) for t in (wd14_tags or []) if t}
    if not tags:
        return "unknown"
    close_hits = tags & _CLOSE
    wide_hits = tags & _WIDE
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


def apply_framing_to_panel(
    panel: dict[str, Any],
    wd14_tags: list[str] | None,
    *,
    image_id: str | None = None,
) -> str:
    """Set qa.framing. Fail count bumps once per distinct sample image_id."""
    cam = str((panel.get("intent") or {}).get("camera") or "")
    result = evaluate_sample_framing(cam, wd14_tags)
    panel.setdefault("qa", {})["framing"] = result
    if result == "fail":
        iid = str(
            image_id
            or (panel.get("sample") or {}).get("image_id")
            or ""
        ).strip()
        last = str(panel.get("framing_counted_image_id") or "").strip()
        if iid and iid != last:
            panel["framing_fail_count"] = int(panel.get("framing_fail_count") or 0) + 1
            panel["framing_counted_image_id"] = iid
    return result


async def resolve_wd14_for_image(db, image_id: str) -> list[str]:
    """Prefer stored WD14; if empty, sync-tag from disk (soft-fail → [])."""
    if not image_id or str(image_id).startswith(("pending:", "placeholder:")):
        return []
    try:
        doc = await db.get(image_id) or {}
    except Exception:
        return []
    existing = list(doc.get("wd14_tags") or [])
    if existing:
        return existing
    path = Path(str(doc.get("path") or ""))
    if not path.exists():
        return []
    try:
        from ...ai import wd14 as wd14_mod

        tags = await wd14_mod.tags_from_path(path, db=db)
        if tags:
            try:
                await db.set_payload(image_id, {"wd14_tags": tags})
            except Exception:
                pass
        return list(tags or [])
    except Exception:
        return []
