"""Camera uniqueness lint."""
from __future__ import annotations

from typing import Any

from ..schema import CAMERAS, PANEL_KEYS


def lint_cameras(panels: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    defects: list[dict[str, str]] = []
    cams: list[str] = []
    for i, panel in enumerate(panels or []):
        if not isinstance(panel, dict):
            continue
        intent = panel.get("intent") if isinstance(panel.get("intent"), dict) else panel
        cam = str((intent or {}).get("camera") or "").strip()
        key = str(panel.get("key") or (PANEL_KEYS[i] if i < 3 else f"panel_{i+1}"))
        if cam not in CAMERAS:
            defects.append({
                "code": "BAD_CAMERA",
                "panel": key,
                "fix": f"invalid camera: {cam!r}",
                "fix": f"Use one of {', '.join(CAMERAS)}",
            })
            continue
        if cam in cams:
            defects.append({
                "code": "CAMERA_DUP",
                "panel": key,
                "fix": f"duplicate camera: {cam}",
                "fix": "Use three distinct cameras",
            })
        cams.append(cam)
    return defects
