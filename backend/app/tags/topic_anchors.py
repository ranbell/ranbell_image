"""Bilingual お題 anchors: bridge a Japanese theme to English Danbooru vocabulary."""
from __future__ import annotations

import re

_TOPIC_EN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_TOPIC_KANJI_RE = re.compile(r"[\u3400-\u9fff]{2,}")
_TOPIC_KATA_RE = re.compile(r"[\u30a0-\u30ff]{2,}")
_TOPIC_STOP = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "her", "his",
    "she", "girl", "story", "about", "into", "over", "under",
})


# Compact bilingual bridges for お題 gating (substring match is otherwise JA≠EN).
# Single-kanji keys (雨/駅/星/海/夜/朝) are included — regex chunks are 2+ only,
# so topic_anchor_groups also substring-scans these keys in the raw お題.
_TOPIC_JA_EN_ALIASES: dict[str, tuple[str, ...]] = {
    "カフェ": ("cafe", "coffee", "barista"),
    "珈琲": ("coffee", "cafe"),
    "キッチン": ("kitchen",),
    "台所": ("kitchen",),
    "駅": ("station", "platform", "train"),
    "学校": ("school", "classroom"),
    "教室": ("classroom", "school"),
    "公園": ("park",),
    "海": ("sea", "ocean", "beach"),
    "海辺": ("beach", "seaside"),
    "祭り": ("festival", "matsuri"),
    "夏祭": ("festival", "matsuri", "summer festival"),
    "花火": ("fireworks",),
    "三人": ("3girls", "three girls", "trio"),
    "二人": ("2girls", "2boys", "couple"),
    "雨": ("rain", "rainy"),
    "夜": ("night", "midnight"),
    "朝": ("morning", "dawn"),
    "屋上": ("rooftop", "roof"),
    "星": ("star", "stars", "constellation", "starry sky"),
    "待ち合わせ": ("meeting", "waiting", "wait", "meet", "rendezvous"),
    "交換日記": ("diary", "journal", "notebook", "exchange diary"),
    "日記": ("diary", "journal"),
    "友達": ("friend", "friends"),
    "友人": ("friend", "friends"),
    "手紙": ("letter", "envelope"),
    "図書館": ("library",),
    "料理": ("cooking", "kitchen", "recipe"),
    "冒険": ("adventure", "quest"),
    "廃墟": ("ruin", "ruins", "abandoned"),
    "探索": ("explore", "exploring", "search"),
    "働く": ("work", "working", "job", "barista", "trainee", "mentor"),
    "自転車": ("bicycle", "bike", "cycling"),
    "試合": ("match", "game", "stadium", "competition"),
    "祝い": ("celebration", "toast", "party"),
    "放課後": ("after school", "afterschool"),
    "バニーガール": ("bunny girl", "bunny_girl", "playboy bunny", "bunny"),
    "バニー": ("bunny", "bunny girl", "bunny_girl"),
    # Season / place / wardrobe — a JA topic scored 0 without these because the
    # story blob is written in English tags and prose.
    "ビーチ": ("beach", "seaside", "shore"),
    "浜辺": ("beach", "shore"),
    "水着": ("swimsuit", "bikini", "swimwear"),
    "浴衣": ("yukata", "festival"),
    "制服": ("school uniform", "uniform", "serafuku"),
    "傘": ("umbrella",),
    "夏": ("summer",),
    "真夏": ("midsummer", "summer"),
    "春": ("spring", "blossom"),
    "秋": ("autumn", "fall"),
    "冬": ("winter",),
    "雪": ("snow", "snowy"),
    "花火大会": ("fireworks", "festival"),
    "人気者": ("popular", "crowd", "admired"),
    "プール": ("pool", "swimming pool"),
    "温泉": ("onsen", "hot spring", "bath"),
    "神社": ("shrine",),
    "書店": ("bookstore", "bookshop", "book"),
    "本屋": ("bookstore", "bookshop"),
    "電車": ("train", "carriage"),
    "夕方": ("dusk", "evening", "sunset"),
    "夕暮": ("dusk", "sunset", "evening"),
}
_TOPIC_EN_JA_ALIASES: dict[str, tuple[str, ...]] = {
    "cafe": ("カフェ",),
    "coffee": ("カフェ", "珈琲"),
    "kitchen": ("キッチン", "台所"),
    "station": ("駅",),
    "school": ("学校",),
    "park": ("公園",),
    "beach": ("海辺", "海"),
    "festival": ("祭り", "夏祭"),
    "matsuri": ("祭り",),
    "fireworks": ("花火",),
    "rain": ("雨",),
    "night": ("夜",),
    "rooftop": ("屋上",),
    "star": ("星",),
    "library": ("図書館",),
    "bunny": ("バニー", "バニーガール"),
    "bunny girl": ("バニーガール",),
    "bunny_girl": ("バニーガール",),
}


def _normalize_topic_token(tok: str) -> str:
    return (tok or "").lower().replace("_", " ").strip()


def _is_ja_script_token(tok: str) -> bool:
    """True when token is mostly CJK / kana (cannot hit an English-only blob)."""
    if not tok:
        return False
    return any(
        "\u3400" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff"
        for c in tok
    )


def topic_anchor_groups(
    user_topic: str, topic_directive: str = "",
) -> list[list[str]]:
    """Seed groups from お題: each ``[seed, *aliases]`` for topic-fit scoring.

    A group counts as a hit when *any* member appears in the story blob.
    Flat ``topic_anchor_tokens`` is the deduped union of all groups.
    """
    text = f"{user_topic or ''} {topic_directive or ''}".strip()
    if not text:
        return []
    text_l = text.lower()
    seeds: list[str] = []
    seen_seeds: set[str] = set()

    def _add_seed(tok: str) -> None:
        t = _normalize_topic_token(tok)
        if not t or t in seen_seeds or t in _TOPIC_STOP:
            return
        # Allow 1-char seeds only when they are known alias keys (雨/駅/星…).
        if len(t) < 2 and t not in _TOPIC_JA_EN_ALIASES:
            return
        seen_seeds.add(t)
        seeds.append(t)

    for m in _TOPIC_EN_RE.finditer(text):
        _add_seed(m.group(0))
    for m in _TOPIC_KANJI_RE.finditer(text):
        _add_seed(m.group(0))
    for m in _TOPIC_KATA_RE.finditer(text):
        _add_seed(m.group(0))

    # Substring scan for alias keys (covers 1-kanji and mixed verbs like 働く).
    for key in sorted(_TOPIC_JA_EN_ALIASES.keys(), key=len, reverse=True):
        if key in text or key.lower() in text_l:
            _add_seed(key)

    groups: list[list[str]] = []
    for seed in seeds:
        group: list[str] = []
        gseen: set[str] = set()

        def _add_g(tok: str) -> None:
            t = _normalize_topic_token(tok)
            if not t or t in gseen or t in _TOPIC_STOP:
                return
            if len(t) < 2 and t not in _TOPIC_JA_EN_ALIASES:
                return
            gseen.add(t)
            group.append(t)

        _add_g(seed)
        for alias in _TOPIC_JA_EN_ALIASES.get(seed, ()):
            _add_g(alias)
        for en, ja_aliases in _TOPIC_EN_JA_ALIASES.items():
            if seed == en or seed in {_normalize_topic_token(a) for a in ja_aliases}:
                _add_g(en)
                for a in ja_aliases:
                    _add_g(a)
        # EN seed → JA aliases (and JA→EN already covered above).
        if seed in _TOPIC_EN_JA_ALIASES:
            for a in _TOPIC_EN_JA_ALIASES[seed]:
                _add_g(a)
                for en_alias in _TOPIC_JA_EN_ALIASES.get(
                    _normalize_topic_token(a), (),
                ):
                    _add_g(en_alias)
        if group:
            groups.append(group)
    return groups[:16]


def topic_anchor_tokens(user_topic: str, topic_directive: str = "") -> list[str]:
    """Salient tokens from お題 (+ directive) for off-topic detection.

    Japanese uses kanji/katakana chunks (not whole phrases glued by hiragana)
    so a topic like 「廃墟を探索する冒険」 yields 「廃墟」「探索」「冒険」.
    Each JA token is also expanded with common EN aliases so English candidate
    beats still match a Japanese お題 (カフェ↔cafe). Single-kanji cues like
    雨/駅/星 are recovered via the alias-key substring scan.
    """
    seen: set[str] = set()
    out: list[str] = []
    for group in topic_anchor_groups(user_topic, topic_directive):
        for tok in group:
            if tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out[:24]

