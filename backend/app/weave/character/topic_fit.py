"""Lightweight topic ↔ outfit conflict warnings (no LLM)."""
from __future__ import annotations

from typing import Any

from .split_tags import soft_normalize_tag

_WARRIOR = frozenset({
    "armor", "knight", "sword", "shield", "helmet", "spear", "katana",
    "military_uniform", "gun", "rifle", "mecha",
})
_FANTASY_CAST = frozenset({
    "witch_hat", "wizard", "mage", "staff", "cape", "crown",
})
_CASUAL_PLACE = (
    "書店", "カフェ", "学校", "教室", "コンビニ", "駅", "雨",
    "bookstore", "cafe", "school", "classroom", "station", "rain",
    "office", "library", "shop",
)
_BATTLE_PLACE = (
    "戦場", "ダンジョン", "城", "迷宮", "battlefield", "dungeon", "castle",
)


def topic_outfit_warnings(
    *,
    topic: str,
    identity_tags: list[str] | None,
    setting: str = "",
) -> list[dict[str, str]]:
    """Return warn dicts if outfit vibe clashes with topic/setting."""
    text = f"{topic or ''} {setting or ''}".lower()
    tags = {soft_normalize_tag(t) for t in (identity_tags or [])}
    warns: list[dict[str, str]] = []

    casual = any(p.lower() in text or p in (topic or "") for p in _CASUAL_PLACE)
    battle = any(p.lower() in text or p in (topic or "") for p in _BATTLE_PLACE)
    warrior_hits = sorted(tags & _WARRIOR)
    fantasy_hits = sorted(tags & _FANTASY_CAST)

    if casual and warrior_hits:
        warns.append({
            "code": "TOPIC_OUTFIT_CLASH",
            "problem": f"日常のお題に戦闘系衣装: {', '.join(warrior_hits)}",
            "fix": "再類推するか、衣装を場所に合わせてください",
        })
    if casual and fantasy_hits:
        warns.append({
            "code": "TOPIC_OUTFIT_CLASH",
            "problem": f"日常のお題にファンタジー衣装: {', '.join(fantasy_hits)}",
            "fix": "topic に合わせた衣装へ再類推を検討",
        })
    if battle and not warrior_hits and not fantasy_hits:
        # Soft hint only — not blocking
        warns.append({
            "code": "TOPIC_OUTFIT_SOFT",
            "problem": "戦場・ダンジョン題材だが戦闘/ファンタジー衣装タグがない",
            "fix": "必要なら再類推で衣装を寄せる",
        })
    return warns


def apply_topic_warnings(session: dict[str, Any]) -> list[dict[str, str]]:
    inputs = session.get("inputs") or {}
    character = session.setdefault("character", {})
    warns = topic_outfit_warnings(
        topic=str(inputs.get("topic") or ""),
        identity_tags=list(character.get("identity_tags") or []),
        setting=str(((session.get("story_bundle") or {}).get("world") or {}).get("setting") or ""),
    )
    character["warnings"] = warns
    return warns
