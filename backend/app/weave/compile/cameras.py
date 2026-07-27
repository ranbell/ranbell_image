"""Camera supremacy tables for deterministic compile."""
from __future__ import annotations

# Framing is boilerplate: keep it to the few tags that actually move the model,
# so the budget goes to the story instead (long_shot used to spend 6 slots).
CAMERA_FORCE_ADD: dict[str, list[str]] = {
    "long_shot": ["long_shot", "full_body", "wide_shot"],
    "medium_shot": ["medium_shot", "upper_body"],
    "close_up": ["close-up", "detailed_face"],
}

CAMERA_FORCE_REMOVE: dict[str, list[str]] = {
    "long_shot": [
        "close-up", "close_up", "portrait", "face_focus", "face_portrait",
        "extreme_close_up", "foreground_center", "highest_detail", "headshot",
    ],
    "medium_shot": ["close-up", "close_up", "extreme_close_up", "foreground_center"],
    "close_up": ["long_shot", "wide_shot", "full_body", "small_figure", "panoramic"],
}

CAMERA_NEGATIVE: dict[str, list[str]] = {
    "long_shot": ["close-up", "portrait", "face focus", "cropped"],
    "medium_shot": ["extreme close-up"],
    "close_up": [],
}


def strip_framing_conflicts(tags: list[str], camera: str) -> list[str]:
    ban = {t.replace("-", "_") for t in CAMERA_FORCE_REMOVE.get(camera, [])}
    ban |= set(CAMERA_FORCE_REMOVE.get(camera, []))
    out: list[str] = []
    for t in tags:
        key = t.lower().replace(" ", "_")
        key_u = key.replace("-", "_")
        if key in ban or key_u in ban or t in ban:
            continue
        out.append(t)
    return out
