"""Parse lounge share / reaction LLM output (labelled blocks, not JSON)."""
from __future__ import annotations

import random
import re
from typing import Any

_LABEL_RE = re.compile(
    r"^[ \t]*[#*\-]*[ \t]*([A-Z][A-Z0-9_]*)[ \t]*[:：][ \t]*(.*)$",
)

_SHARE_TEMPLATES = (
    "report",      # どこで撮ってどんな感じだったか
    "praise",      # 監督が良いと言っていたこと
    "soft_flex",   # 軽く自慢
    "ask_friend",  # ちょっと相談
    "vibe",        # 空気・場所のぼやき
)

# Traits that make a Muse more likely to pitch an idea to the showrunner.
_PITCHY_TRAITS = (
    "curious", "creative", "bold", "proactive", "talkative", "mischievous",
    "playful", "expressive", "confident", "adventurous", "stylish",
    "好奇心", "積極", "提案", "おしゃれ", "元気", "大胆", "遊び", "発言",
)


def pick_share_template() -> str:
    return random.choice(_SHARE_TEMPLATES)


def pitch_chance(character: dict[str, Any] | None = None, preset: dict[str, Any] | None = None) -> float:
    """Base chance of a post-wrap pitch, boosted by outgoing personality traits."""
    chance = 0.12
    traits: list[str] = []
    src = character or {}
    p = src.get("personality") if isinstance(src.get("personality"), dict) else {}
    traits.extend(str(t) for t in (p.get("traits") or []) if t)
    if preset:
        traits.extend(str(t) for t in (preset.get("personality") or []) if t)
    blob = " ".join(traits).lower()
    hits = sum(1 for key in _PITCHY_TRAITS if key.lower() in blob)
    chance += min(0.28, hits * 0.07)
    return min(0.45, chance)


def should_pitch(character: dict[str, Any] | None = None, preset: dict[str, Any] | None = None) -> bool:
    return random.random() < pitch_chance(character, preset)


def should_write_habit(*, notes: list[Any], rng: random.Random | None = None) -> bool:
    """Rare handpost about the showrunner's taste — only when there were notes."""
    if not any(str(n).strip() for n in (notes or [])):
        return False
    roll = (rng or random).random()
    return roll < 0.18


def parse_labelled(raw: str) -> dict[str, str]:
    """Split `KEY: value` lines; multi-line values accumulate until the next key."""
    text = (raw or "").strip()
    if not text:
        return {}
    current = ""
    bodies: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = _LABEL_RE.match(line)
        if m:
            current = m.group(1).upper()
            bodies.setdefault(current, [])
            inline = (m.group(2) or "").strip()
            if inline:
                bodies[current].append(inline)
            continue
        if current:
            bodies.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in bodies.items() if "\n".join(v).strip()}


def normalize_share(parsed: dict[str, str], *, fallback_ja: str = "") -> dict[str, Any]:
    text_ja = (parsed.get("TEXT_JA") or parsed.get("JA") or fallback_ja or "").strip()
    text_en = (parsed.get("TEXT_EN") or parsed.get("EN") or "").strip()
    if not text_en and text_ja:
        text_en = text_ja
    tags = {
        "pose": (parsed.get("POSE") or "").strip(),
        "outfit": (parsed.get("OUTFIT") or "").strip(),
        "expression": (parsed.get("EXPRESSION") or "").strip(),
        "place": (parsed.get("PLACE") or "").strip(),
        "vibe": (parsed.get("VIBE") or "").strip(),
    }
    return {
        "text_ja": text_ja,
        "text_en": text_en,
        "tags": {k: v for k, v in tags.items() if v},
    }


def normalize_pitch(parsed: dict[str, str], *, fallback_ja: str = "") -> dict[str, str]:
    text_ja = (parsed.get("TEXT_JA") or parsed.get("PITCH_JA") or fallback_ja or "").strip()
    text_en = (parsed.get("TEXT_EN") or parsed.get("PITCH_EN") or "").strip()
    if not text_en and text_ja:
        text_en = text_ja
    return {"text_ja": text_ja, "text_en": text_en}


def normalize_habit(parsed: dict[str, str]) -> dict[str, str]:
    title = (parsed.get("TITLE_JA") or parsed.get("TITLE") or "").strip()
    title_en = (parsed.get("TITLE_EN") or title).strip()
    body_ja = (parsed.get("BODY_JA") or parsed.get("TEXT_JA") or "").strip()
    body_en = (parsed.get("BODY_EN") or parsed.get("TEXT_EN") or "").strip()
    if not body_en and body_ja:
        body_en = body_ja
    if not title and body_ja:
        title = body_ja.splitlines()[0][:40]
        title_en = title
    return {
        "title": title,
        "title_en": title_en,
        "body_ja": body_ja,
        "body_en": body_en,
    }


def normalize_reactions(
    parsed: dict[str, str],
    friends: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map REACTOR_N_* blocks onto the friend list order."""
    out: list[dict[str, Any]] = []
    for i, friend in enumerate(friends[:2], start=1):
        prefix = f"REACTOR_{i}_"
        text_ja = (parsed.get(f"{prefix}JA") or parsed.get(f"R{i}_JA") or "").strip()
        text_en = (parsed.get(f"{prefix}EN") or parsed.get(f"R{i}_EN") or "").strip()
        if not text_ja and not text_en:
            continue
        if not text_en:
            text_en = text_ja
        reaction = (parsed.get(f"{prefix}REACTION") or parsed.get(f"R{i}_REACTION") or "💕").strip()
        stance = (parsed.get(f"{prefix}STANCE") or parsed.get(f"R{i}_STANCE") or "try").strip().lower()
        if stance not in ("try", "twist", "skip"):
            stance = "try"
        twist = (parsed.get(f"{prefix}TWIST") or parsed.get(f"R{i}_TWIST") or "").strip()
        out.append({
            "character_id": str(friend.get("id") or ""),
            "name_ja": str(friend.get("name_ja") or friend.get("name") or ""),
            "name": str(friend.get("name") or friend.get("name_ja") or ""),
            "reaction": reaction[:8] or "💕",
            "text_ja": text_ja,
            "text_en": text_en,
            "stance": stance,
            "twist": twist,
            "tier": str(friend.get("tier") or ""),
            "score": float(friend.get("score") or 0.0),
        })
    return out
