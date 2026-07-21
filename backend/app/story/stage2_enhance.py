"""Chronicle Stage2: per-panel prompt enhancer (R0 locked tags)."""
from __future__ import annotations

import json
import logging
from typing import Any

from . import prompt_assets
from .compose import soft_normalize_tag
from .stage1_storyboard import PANELS

logger = logging.getLogger(__name__)


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


def _has_non_ascii(s: str) -> bool:
    return any(ord(c) > 127 for c in (s or ""))


def enforce_r0_locks(
    prompt_text: str,
    *,
    consistency_tags: list[str],
    custom_tags: list[str],
    camera: str,
    character_state_diff: str,
    gesture: str = "",
) -> str:
    """Always prepend consistency + custom + camera (fixed order).

    English ``character_state_diff`` / ``gesture`` are locked when present.
    Non-ASCII values are skipped (Stage1/2 prompts own English; this is a safety net).
    """
    text = (prompt_text or "").strip()
    lock: list[str] = []

    for t in consistency_tags:
        s = str(t or "").strip()
        if s:
            lock.append(s)
    for t in custom_tags:
        s = str(t or "").strip()
        if s:
            lock.append(s)

    cam = (camera or "").strip()
    if cam:
        lock.append(cam)

    diff = (character_state_diff or "").strip()
    if diff:
        if _has_non_ascii(diff):
            logger.warning(
                "Skipping non-ASCII character_state_diff for R0 prepend: %r",
                diff[:120],
            )
        else:
            lock.append(diff)

    gest = (gesture or "").strip()
    if gest:
        if _has_non_ascii(gest):
            logger.warning(
                "Skipping non-ASCII gesture for R0 prepend: %r",
                gest[:120],
            )
        else:
            blob = (text + " " + ", ".join(lock)).lower().replace("-", "_")
            gkey = _norm(gest)
            combined = (text + " " + ", ".join(lock)).lower()
            if gkey and gkey not in blob and gest.lower() not in combined:
                lock.append(gest)

    lock_line = ", ".join(dict.fromkeys(lock))
    if not lock_line:
        return text
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
    log=None,
) -> dict[str, dict]:
    """Run Stage2 for panel_1/2/3 strictly sequential. Always think=True."""
    import time

    consistency = list(stage1.get("consistency_tags") or [])
    shared = list(stage1.get("shared_tags") or [])
    title = str(stage1.get("title") or "")
    conflict = str(stage1.get("core_conflict") or "")
    panels = list(stage1.get("panels") or [])
    ct = custom_tags or {}
    out: dict[str, dict] = {}

    for i in range(3):
        key = PANELS[i]
        panel = panels[i] if i < len(panels) else {}
        if not isinstance(panel, dict):
            panel = {}
        if log:
            log(f"[chronicle] Stage2 {key} START (serial {i + 1}/3)")
        t0 = time.perf_counter()
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
            gesture=str(panel.get("gesture") or ""),
        )
        wall = time.perf_counter() - t0
        if log:
            log(
                f"[chronicle] Stage2 {key} END wall={wall:.3f}s "
                f"out_chars={len(positive or '')}"
            )
        out[key] = {
            "positive": positive,
            "negative": "",
            "visual_script": visual_script_from_panel(panel, locale=locale),
            "camera": panel.get("camera") or "",
            "danbooru_tags": list(panel.get("danbooru_tags") or []),
            "character_state_diff": panel.get("character_state_diff") or "",
        }
    return out
