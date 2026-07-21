"""Chronicle Stage2: per-panel prompt enhancer (R0 locked tags)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import prompt_assets
from .compose import soft_normalize_tag
from .stage1_storyboard import PANELS

logger = logging.getLogger(__name__)

_CAMERA_WORDS = {
    "long_shot": ("long shot", "long_shot", "full body", "wide shot"),
    "medium_shot": ("medium shot", "medium_shot", "waist up", "cowboy"),
    "close_up": ("close-up", "close_up", "closeup", "close up"),
}


def build_stage2_input(
    *,
    panel: dict[str, Any],
    panel_key: str,
    consistency_tags: list[str],
    custom_tags: list[str],
    shared_tags: list[str] | None = None,
    title: str = "",
    core_conflict: str = "",
) -> str:
    payload = {
        "panel_key": panel_key,
        "title": title,
        "core_conflict": core_conflict,
        "consistency_tags": list(consistency_tags),
        "custom_tags": list(custom_tags),
        "camera": panel.get("camera") or "",
        "character_state_diff": panel.get("character_state_diff") or "",
        "act": panel.get("act") or "",
        "narrative_ja": panel.get("narrative_ja") or "",
        "narrative_en": panel.get("narrative_en") or "",
        "character_focus": panel.get("character_focus") or "",
        "gesture": panel.get("gesture") or "",
        "time_marker": panel.get("time_marker") or "",
        "visible_elements": panel.get("visible_elements") or [],
        "danbooru_tags": panel.get("danbooru_tags") or [],
        "shared_tags": list(shared_tags or []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_stage2_prompt(**kwargs) -> str:
    return prompt_assets.fill_stage2(build_stage2_input(**kwargs))


def _norm(s: str) -> str:
    return soft_normalize_tag(s) or (s or "").strip().lower().replace(" ", "_")


def enforce_r0_locks(
    prompt_text: str,
    *,
    consistency_tags: list[str],
    custom_tags: list[str],
    camera: str,
    character_state_diff: str,
) -> str:
    """Append any missing locked tags/phrases verbatim at the front."""
    text = (prompt_text or "").strip()
    missing: list[str] = []
    blob = text.lower().replace("-", "_")

    for t in consistency_tags:
        key = _norm(t)
        if key and key not in blob and t.lower() not in text.lower():
            missing.append(t)

    for t in custom_tags:
        key = _norm(t)
        if key and key not in blob and t.lower() not in text.lower():
            missing.append(t)

    cam = (camera or "").strip()
    if cam:
        alts = _CAMERA_WORDS.get(cam, (cam,))
        if not any(a.replace(" ", "_") in blob or a in text.lower() for a in alts):
            missing.append(cam)

    diff = (character_state_diff or "").strip()
    if diff and diff.lower() not in text.lower():
        missing.append(diff)

    if not missing:
        return text
    lock_line = ", ".join(dict.fromkeys(missing))
    return f"{lock_line}, {text}" if text else lock_line


def visual_script_from_panel(panel: dict[str, Any], *, locale: str = "ja") -> str:
    if locale == "en":
        return str(panel.get("narrative_en") or panel.get("narrative_ja") or "").strip()
    return str(panel.get("narrative_ja") or panel.get("narrative_en") or "").strip()


async def enhance_all_panels(
    ollama,
    *,
    stage1: dict[str, Any],
    custom_tags: dict[str, list[str]] | None = None,
    model: str,
    options: dict,
    locale: str = "ja",
) -> dict[str, dict]:
    """Run Stage2 for panel_1/2/3 (sequential). Always think=True."""
    import asyncio

    consistency = list(stage1.get("consistency_tags") or [])
    shared = list(stage1.get("shared_tags") or [])
    title = str(stage1.get("title") or "")
    conflict = str(stage1.get("core_conflict") or "")
    panels = list(stage1.get("panels") or [])
    ct = custom_tags or {}

    async def _one(i: int) -> tuple[str, dict]:
        key = PANELS[i]
        panel = panels[i] if i < len(panels) else {}
        if not isinstance(panel, dict):
            panel = {}
        prompt = build_stage2_prompt(
            panel=panel,
            panel_key=key,
            consistency_tags=consistency,
            custom_tags=list(ct.get(key) or []),
            shared_tags=shared,
            title=title,
            core_conflict=conflict,
        )
        raw = await ollama.chat_text(
            prompt,
            model=model,
            options=options,
            fmt=None,
            think=True,
        )
        positive = enforce_r0_locks(
            raw,
            consistency_tags=consistency,
            custom_tags=list(ct.get(key) or []),
            camera=str(panel.get("camera") or ""),
            character_state_diff=str(panel.get("character_state_diff") or ""),
        )
        return key, {
            "positive": positive,
            "negative": "",
            "visual_script": visual_script_from_panel(panel, locale=locale),
            "camera": panel.get("camera") or "",
            "danbooru_tags": list(panel.get("danbooru_tags") or []),
            "character_state_diff": panel.get("character_state_diff") or "",
        }

    results = await asyncio.gather(*(_one(i) for i in range(3)))
    return {k: v for k, v in results}
