"""Board / sample / final prompt builders for Weave renders."""
from __future__ import annotations

from typing import Any

from ..character.split_tags import soft_normalize_tag
from ..compile.cameras import CAMERA_FORCE_ADD, strip_framing_conflicts
from ..compile.layers import WEAVE_NEGATIVE, compile_panel


_BOARD_PURPOSE: dict[str, list[str]] = {
    "portrait": ["close-up", "upper_body", "detailed_face", "looking_away"],
    "full": ["long_shot", "full_body", "small_figure", "wide_shot"],
    "prop": ["medium_shot", "upper_body"],
    "mood": ["medium_shot", "atmospheric", "soft_lighting"],
}


def compile_board_slot(session: dict[str, Any], slot: str) -> dict[str, str]:
    character = session.get("character") or {}
    identity = list(character.get("identity_tags") or [])
    props = list(character.get("prop_tags") or [])
    sig = soft_normalize_tag(str(character.get("signature_prop") or ""))
    if sig and sig not in props:
        props.append(sig)

    purpose = list(_BOARD_PURPOSE.get(slot, ["medium_shot"]))
    camera = "close_up" if slot == "portrait" else (
        "long_shot" if slot == "full" else "medium_shot"
    )
    cam_tags = list(CAMERA_FORCE_ADD.get(camera, purpose))
    tags = identity + cam_tags
    if slot == "prop":
        tags.extend(props)
        tags.append("holding")
    # Strip framing conflicts for the intended camera
    tags = strip_framing_conflicts(tags, camera if camera != "close_up" else "close_up")
    # Dedup
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        k = soft_normalize_tag(t) if str(t).isascii() else str(t)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    positive = ", ".join(out)
    return {
        "positive": positive,
        "negative": WEAVE_NEGATIVE,
        "camera": camera,
        "slot": slot,
    }


def compile_panel_render(
    session: dict[str, Any],
    panel_key: str,
    *,
    env_boost: bool = False,
) -> dict[str, str]:
    # Prefer existing compile if present
    panel = next((p for p in session.get("panels") or [] if p.get("key") == panel_key), None)
    if panel and (panel.get("compile") or {}).get("positive") and not env_boost:
        c = panel["compile"]
        return {
            "positive": c.get("positive") or "",
            "negative": c.get("negative") or WEAVE_NEGATIVE,
            "camera": (panel.get("intent") or {}).get("camera") or "",
            "panel_key": panel_key,
        }
    compiled = compile_panel(session, panel_key, env_boost=env_boost)
    return {
        "positive": compiled.get("positive") or "",
        "negative": compiled.get("negative") or WEAVE_NEGATIVE,
        "camera": compiled.get("camera") or "",
        "panel_key": panel_key,
    }
