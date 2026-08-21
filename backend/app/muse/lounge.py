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

# What they got up to when there was no camera. Everyday things — the point is
# that it is not work, so nothing here is a shoot, a costume or a location.
#
# The hints say what *happened*, not where. Muse copy that names a place has a
# way of turning up in the picture, which is what
# `test_production_muse_copy_has_no_situation_specific_anchors` is guarding —
# and a small moment is better writing than a venue anyway.
_OUTINGS = (
    ("パンケーキ", "話題の店に並んだら、思ったより待たされた"),
    ("ごはん", "遅い時間に、二人でラーメンを食べた"),
    ("買い物", "服を見に行って、結局どちらも何も買わなかった"),
    ("遊園地", "絶叫系に乗ったら、片方だけずっと叫んでいた"),
    ("旅行", "一泊の温泉。帰りの電車の時間を間違えた"),
    ("散歩", "あてもなく歩いて、気づいたら遠くまで来ていた"),
    ("映画", "終わったあと、感想が見事に食い違った"),
    ("水族館", "クラゲの前から動かない子がいた"),
    ("勉強", "課題を持ち寄ったのに、ほとんど喋って終わった"),
    ("猫", "近所の猫に会いに行ったら、逃げられた"),
    ("花火", "遠くの音だけ聞こえて、結局よく見えなかった"),
    ("だらだら", "どちらかの部屋で、何をするでもなく")
)


def pick_outing() -> tuple[str, str]:
    """お題を一つ。同じ話が続かないよう、毎回引き直す。"""
    return random.choice(_OUTINGS)


def outing_occasions() -> tuple[str, ...]:
    return tuple(name for name, _ in _OUTINGS)


def normalize_outing(
    parsed: dict[str, str],
    cast: list[dict[str, Any]],
    *,
    max_turns: int = 6,
) -> list[dict[str, Any]]:
    """`TURN_N_*` を掛け合いに変える。話者は cast の並びを回る。

    `normalize_reactions` が `REACTOR_N_*` を友達に割り当てているのと同じ手口。
    一度の呼び出しで全員ぶん書かせるので、人数が増えても呼び出しは増えない。
    """
    out: list[dict[str, Any]] = []
    if not cast:
        return out
    for i in range(1, max_turns + 1):
        text_ja = (parsed.get(f"TURN_{i}_JA") or parsed.get(f"T{i}_JA") or "").strip()
        if not text_ja:
            continue
        text_en = (parsed.get(f"TURN_{i}_EN") or parsed.get(f"T{i}_EN") or "").strip()
        who = (parsed.get(f"TURN_{i}_WHO") or parsed.get(f"T{i}_WHO") or "").strip()
        speaker = next(
            (c for c in cast if who and str(c.get("name_ja") or "") in who),
            cast[(i - 1) % len(cast)],
        )
        out.append({
            "id": "",
            "turn": len(out),
            "character_id": str(speaker.get("character_id") or speaker.get("id") or ""),
            "name_ja": str(speaker.get("name_ja") or ""),
            "name": str(speaker.get("name") or speaker.get("name_ja") or ""),
            "text_ja": text_ja,
            "text_en": text_en or text_ja,
            "reaction": (parsed.get(f"TURN_{i}_REACTION") or "").strip()[:8],
        })
    return out


def outing_summary_line(thread: dict[str, Any]) -> str:
    """楽屋の一件を、彼女の手元に残る一行にする。

    要約ではなく**指し先**。いつ・誰と・何を、それだけ。中身が読みたければ
    楽屋にスレッドがある。総監督:「要約は諸刃の剣。結構消えてしまうので。」
    """
    when = str(thread.get("when_ja") or "").strip()
    occasion = str(thread.get("occasion") or "").strip()
    names = [
        str(c.get("name_ja") or "").strip()
        for c in (thread.get("cast") or []) if isinstance(c, dict)
    ]
    with_who = "と".join([n for n in names if n][1:]) or "みんな"
    bits = [b for b in (when, f"{with_who}と{occasion}" if occasion else with_who) if b]
    return "、".join(bits)[:80]


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
