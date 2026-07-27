"""Framing heuristics for Weave Look-dev samples (M3).

Uses WD14 tags + optional center-edge density from the image —
no heavy face detector. long_shot that looks like a close portrait → framing=fail.
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


def face_tag_density(wd14_tags: list[str] | None) -> float:
    """0..1 share of close-face cues among close+wide WD14 hits."""
    tags = {soft_normalize_tag(t) for t in (wd14_tags or []) if t}
    close_n = len(tags & _CLOSE)
    wide_n = len(tags & _WIDE)
    denom = close_n + wide_n
    if denom <= 0:
        return 0.0
    return close_n / denom


def center_edge_density(image_path: str | Path | None) -> float | None:
    """0..1 — relative edge energy in the center crop (face/subject approx)."""
    if not image_path:
        return None
    path = Path(image_path)
    if not path.exists():
        return None
    try:
        from PIL import Image, ImageFilter, ImageStat

        with Image.open(path) as im:
            gray = im.convert("L").resize((64, 64))
        edges = gray.filter(ImageFilter.FIND_EDGES)
        w, h = edges.size
        # Portrait-ish central band
        center = edges.crop((int(w * 0.28), int(h * 0.18), int(w * 0.72), int(h * 0.78)))
        c_mean = float(ImageStat.Stat(center).mean[0])
        f_mean = float(ImageStat.Stat(edges).mean[0]) or 1.0
        # Center much hotter than average → subject fills frame
        ratio = c_mean / f_mean
        return max(0.0, min(1.0, (ratio - 0.85) / 0.8))
    except Exception:
        return None


def evaluate_long_shot_framing(
    wd14_tags: list[str] | None,
    *,
    image_path: str | Path | None = None,
) -> str:
    """Return pass | fail | unknown for a long_shot sample."""
    tags = {soft_normalize_tag(t) for t in (wd14_tags or []) if t}
    center_d = center_edge_density(image_path)
    face_d = face_tag_density(wd14_tags)

    if not tags and center_d is None:
        return "unknown"

    close_hits = tags & _CLOSE
    wide_hits = tags & _WIDE
    if ("close-up" in tags or "close_up" in tags or "portrait" in tags) and not wide_hits:
        return "fail"
    if close_hits and not wide_hits and "full_body" not in tags:
        return "fail"
    # Density approx: high face-tag share + hot center → too close for long_shot
    if face_d >= 0.6 and center_d is not None and center_d >= 0.55 and not wide_hits:
        return "fail"
    if face_d >= 0.75 and not wide_hits:
        return "fail"
    if not tags:
        # Image-only signal when WD14 empty
        if center_d is not None and center_d >= 0.7:
            return "fail"
        return "unknown"
    return "pass"


def evaluate_sample_framing(
    camera: str,
    wd14_tags: list[str] | None = None,
    *,
    image_path: str | Path | None = None,
) -> str:
    """Framing result for any camera. Non-long_shot always pass for now."""
    cam = soft_normalize_tag(camera).replace("-", "_")
    if cam != "long_shot":
        return "pass"
    return evaluate_long_shot_framing(wd14_tags, image_path=image_path)


def apply_framing_to_panel(
    panel: dict[str, Any],
    wd14_tags: list[str] | None,
    *,
    image_id: str | None = None,
    image_path: str | Path | None = None,
) -> str:
    """Set qa.framing. Fail count bumps once per distinct sample image_id."""
    cam = str((panel.get("intent") or {}).get("camera") or "")
    face_d = face_tag_density(wd14_tags)
    center_d = center_edge_density(image_path)
    result = evaluate_sample_framing(cam, wd14_tags, image_path=image_path)
    qa = panel.setdefault("qa", {})
    qa["framing"] = result
    qa["framing_signals"] = {
        "face_tag_density": round(face_d, 3),
        "center_edge_density": None if center_d is None else round(float(center_d), 3),
    }
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


async def resolve_image_path(db, image_id: str) -> str | None:
    if not image_id or str(image_id).startswith(("pending:", "placeholder:")):
        return None
    try:
        doc = await db.get(image_id) or {}
    except Exception:
        return None
    path = str(doc.get("path") or "").strip()
    return path if path and Path(path).exists() else None
