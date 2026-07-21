"""Chronicle Stage1: 3-panel storyboard JSON (gemma4 production contract)."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import prompt_assets
from .compose import (
    parse_csv_tags,
    resolve_chronicle_identity,
    soft_normalize_tag,
)
from .generator import classify_identity_tag, outfit_tags_from_wd14

logger = logging.getLogger(__name__)

PANELS = ("panel_1", "panel_2", "panel_3")
CAMERAS = ("long_shot", "medium_shot", "close_up")
HAPPENING_CATEGORIES = (
    "物理的アクシデント",
    "人間関係アクシデント",
    "発見系",
    "予定変更系",
    "環境変化系",
    "該当なし",
)
BANNED_TAG_FRAGMENTS = (
    "looking_at_viewer",
    "looking at viewer",
    "looking_at_camera",
    "looking at camera",
    "smile",
    "smiling",
    "grin",
)

_HAIR_COLOR_RE = re.compile(
    r"^(black|white|grey|gray|silver|blonde|blond|brown|auburn|red|pink|blue|"
    r"green|purple|orange|yellow|dark|light)_hair$",
    re.I,
)


def character_profile_from_tags(
    character_tags_user: str,
    wd14_tags: list[str] | None = None,
    *,
    rng=None,
) -> dict[str, str]:
    """Map identity tags → Stage1 character_profile fields."""
    ident = resolve_chronicle_identity(
        character_tags_user or "",
        list(wd14_tags or []),
        rng=rng,
    )
    buckets: dict[str, list[str]] = {
        "hair_color": [],
        "hair_style": [],
        "eyes": [],
        "other": [],
    }
    for tag in ident:
        key = (tag or "").strip().lower().replace(" ", "_").replace("-", "_")
        if not key:
            continue
        cat = classify_identity_tag(tag) or "other"
        if cat == "hair_color" or _HAIR_COLOR_RE.match(key):
            buckets["hair_color"].append(key)
        elif cat == "hair_style":
            buckets["hair_style"].append(key)
        elif cat == "eyes":
            buckets["eyes"].append(key)
        else:
            buckets["other"].append(key)

    outfit = outfit_tags_from_wd14(list(wd14_tags or []))
    # Prefer clothing-like tokens from user tags when WD14 outfit empty
    if not outfit and character_tags_user:
        for t in parse_csv_tags(character_tags_user, strip_colors=False):
            k = soft_normalize_tag(t)
            if k and classify_identity_tag(t) not in {
                "hair_color", "hair_style", "eyes", "face",
            }:
                if any(
                    x in k
                    for x in (
                        "uniform", "dress", "shirt", "blouse", "skirt", "jacket",
                        "coat", "apron", "kimono", "yukata", "hoodie", "sweater",
                    )
                ):
                    outfit.append(k)

    hair_color = ", ".join(buckets["hair_color"][:2]) or "brown_hair"
    hairstyle = ", ".join(buckets["hair_style"][:3]) or "medium_hair"
    eye_color = ", ".join(buckets["eyes"][:2]) or "brown_eyes"
    base_outfit = ", ".join(outfit[:4]) or "casual_clothes"

    # Strip _hair / _eyes suffix for profile fields when single token
    def _field(raw: str, suffix: str) -> str:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        out = []
        for p in parts:
            if p.endswith(suffix) and len(parts) == 1:
                out.append(p)
            else:
                out.append(p)
        return ", ".join(out)

    return {
        "hair_color": _field(hair_color, "_hair"),
        "hairstyle": hairstyle,
        "eye_color": _field(eye_color, "_eyes"),
        "base_outfit": base_outfit,
    }


def consistency_tags_from_profile(profile: dict[str, str]) -> list[str]:
    tags: list[str] = []
    for key in ("hair_color", "hairstyle", "eye_color", "base_outfit"):
        for part in parse_csv_tags(profile.get(key) or "", strip_colors=False):
            # Keep color prefixes for identity (do not soft_normalize)
            t = (part or "").strip().lower().replace(" ", "_").replace("-", "_")
            if t and t not in tags:
                tags.append(t)
    return tags


def build_stage1_user_input(
    *,
    theme: str,
    character_profile: dict[str, str],
    include_happening: bool = False,
    author_style: str = "",
    custom_tags: dict[str, list[str]] | None = None,
    avoid_repeats: list[str] | None = None,
    style_hint: str = "",
) -> dict[str, Any]:
    ct = custom_tags or {}
    theme_text = (theme or "").strip()
    if style_hint.strip():
        theme_text = (
            f"{theme_text}\n[art_style_hint: {style_hint.strip()}]"
            if theme_text
            else f"[art_style_hint: {style_hint.strip()}]"
        )
    return {
        "theme": theme_text,
        "character_profile": {
            "hair_color": character_profile.get("hair_color") or "",
            "hairstyle": character_profile.get("hairstyle") or "",
            "eye_color": character_profile.get("eye_color") or "",
            "base_outfit": character_profile.get("base_outfit") or "",
        },
        "include_happening": bool(include_happening),
        "author_style": (author_style or "").strip(),
        "custom_tags": {
            "panel_1": list(ct.get("panel_1") or []),
            "panel_2": list(ct.get("panel_2") or []),
            "panel_3": list(ct.get("panel_3") or []),
        },
        "avoid_repeats": list(avoid_repeats or [])[:5],
    }


def build_stage1_messages(user_input: dict[str, Any]) -> list[dict[str, str]]:
    system = prompt_assets.stage1_system_prompt()
    few = prompt_assets.stage1_fewshots_block()
    payload = json.dumps(user_input, ensure_ascii=False, indent=2)
    user = (
        "# RUNTIME INPUT (JSON)\n"
        f"{payload}\n\n"
        "Follow the SYSTEM rules. Output one JSON object only.\n"
    )
    if few:
        user += (
            "\n# REFERENCE FEW-SHOTS (style and contract only — do not copy plots)\n"
            f"{few}\n"
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def parse_stage1_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    panels = data.get("panels")
    if not isinstance(panels, list) or len(panels) != 3:
        return None
    return data


def _is_banned_tag(tag: str) -> bool:
    low = (tag or "").lower().replace("-", "_")
    for frag in BANNED_TAG_FRAGMENTS:
        if frag.replace(" ", "_") in low or frag in (tag or "").lower():
            return True
    return False


def _strip_hair_eye_from_danbooru(tags: list[str], consistency: list[str]) -> list[str]:
    cons = {soft_normalize_tag(t) for t in consistency}
    out: list[str] = []
    for t in tags:
        k = soft_normalize_tag(t) or t
        if k in cons:
            continue
        if "hair" in k or k.endswith("_eyes") or k.endswith("eyes"):
            # Drop hair/eye color variants from per-panel tags (一元化)
            if any(
                x in k
                for x in (
                    "hair", "eyes", "bangs", "ponytail", "twintail", "bob_cut",
                )
            ) and classify_identity_tag(t) in {
                "hair_color", "hair_style", "eyes", None,
            }:
                cat = classify_identity_tag(t)
                if cat in ("hair_color", "hair_style", "eyes"):
                    continue
        out.append(t)
    return out


def apply_stage1_failure_handling(
    data: dict[str, Any],
    *,
    character_profile: dict[str, str],
    custom_tags: dict[str, list[str]] | None = None,
    include_happening: bool = False,
) -> dict[str, Any]:
    """Post-process Stage1 JSON per FAILURE HANDLING (no re-LLM)."""
    out = dict(data)
    expected = consistency_tags_from_profile(character_profile)
    out["consistency_tags"] = expected
    out["include_happening"] = bool(include_happening)
    if not include_happening:
        out["happening_summary"] = ""
        out["happening_category"] = "該当なし"

    panels = list(out.get("panels") or [])
    while len(panels) < 3:
        panels.append({})
    panels = panels[:3]

    # Camera uniqueness → force long/medium/close_up
    used: set[str] = set()
    for i, panel in enumerate(panels):
        if not isinstance(panel, dict):
            panel = {}
            panels[i] = panel
        cam = str(panel.get("camera") or "").strip()
        if cam not in CAMERAS or cam in used:
            for c in CAMERAS:
                if c not in used:
                    cam = c
                    break
        panel["camera"] = cam
        used.add(cam)

        tags = panel.get("danbooru_tags") or []
        if isinstance(tags, str):
            tags = parse_csv_tags(tags, strip_colors=False)
        tags = [t for t in tags if t and not _is_banned_tag(t)]
        tags = _strip_hair_eye_from_danbooru(tags, expected)

        # Merge custom_tags
        ct = (custom_tags or {}).get(PANELS[i]) or []
        for t in ct:
            if _is_banned_tag(t):
                continue
            if t not in tags:
                tags.append(t)
        panel["danbooru_tags"] = tags
        if "character_state_diff" not in panel:
            panel["character_state_diff"] = ""
        if "visible_elements" not in panel or not isinstance(
            panel.get("visible_elements"), list
        ):
            panel["visible_elements"] = list(panel.get("visible_elements") or [])

    out["panels"] = panels
    if "shared_tags" not in out:
        out["shared_tags"] = [
            "multiple panels", "sequential art", "no text", "no speech bubble",
        ]
    if "title" not in out:
        out["title"] = ""
    if "core_conflict" not in out:
        out["core_conflict"] = ""
    return out


def stage1_needs_retry(
    data: dict[str, Any] | None,
    *,
    include_happening: bool,
    avoid_repeats: list[str] | None = None,
) -> str | None:
    """Return reason string if should retry LLM, else None."""
    if not data:
        return "parse_failed"
    panels = data.get("panels")
    if not isinstance(panels, list) or len(panels) != 3:
        return "panels_count"
    if include_happening:
        summary = str(data.get("happening_summary") or "").strip()
        cat = str(data.get("happening_category") or "").strip()
        if not summary or cat in ("", "該当なし"):
            return "happening_missing"
    else:
        # Soft: if category is a real accident type, retry
        cat = str(data.get("happening_category") or "").strip()
        if cat in HAPPENING_CATEGORIES and cat != "該当なし":
            if str(data.get("happening_summary") or "").strip():
                return "happening_unexpected"
    avoid = set(avoid_repeats or [])
    cat = str(data.get("happening_category") or "").strip()
    if cat in avoid:
        return "avoid_repeats"
    return None


def candidate_from_stage1(
    data: dict[str, Any],
    *,
    candidate_id: str,
) -> dict[str, Any]:
    """Shape for SSE / draft storage (panel_1/2/3, no past/present/future)."""
    panels = data.get("panels") or []
    panel_summaries = {}
    for i, key in enumerate(PANELS):
        p = panels[i] if i < len(panels) else {}
        panel_summaries[key] = {
            "act": p.get("act") or "",
            "narrative_ja": p.get("narrative_ja") or "",
            "narrative_en": p.get("narrative_en") or "",
            "camera": p.get("camera") or "",
            "gesture": p.get("gesture") or "",
        }
    return {
        "id": candidate_id,
        "title": data.get("title") or "",
        "summary": data.get("core_conflict") or "",
        "core_conflict": data.get("core_conflict") or "",
        "structure_type": data.get("structure_type") or "",
        "happening_summary": data.get("happening_summary") or "",
        "happening_category": data.get("happening_category") or "",
        "include_happening": bool(data.get("include_happening")),
        "panels": panel_summaries,
        "stage1": data,
    }


def custom_tags_from_body(body) -> dict[str, list[str]]:
    def _one(attr: str) -> list[str]:
        return parse_csv_tags(getattr(body, attr, "") or "", strip_colors=False)

    return {
        "panel_1": _one("custom_tags_panel_1"),
        "panel_2": _one("custom_tags_panel_2"),
        "panel_3": _one("custom_tags_panel_3"),
    }
