"""Identity lock, hybrid prompt assemble, framing tags, WD14 body conflicts.

The brief tells the LLM not to change hair / eyes / figure. That is soft. This
module is the hard half: identity tags are stapled onto every Comfy positive,
conflicting body tags are stripped from WD14, and opposing body tags go into the
negative. The LLM can forget; the sampler still cannot.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

# The body vocabulary lives in app.tags.body so the character registry and this
# module cannot drift apart about what may be locked to a character.
from ..tags.body import AGE_TAGS, REFUSED_TAGS
from ..tags.body import BODY_SLOTS as _BODY_SLOTS
from ..tags.body import BREAST_TAGS as _BREAST_TAGS
from ..tags.catalog import HAIR_STYLES as _HAIR_STYLES

logger = logging.getLogger(__name__)

# Hairstyle is session-mutable. Identity still owns hair *colour* / eyes /
# figure; when the craft names a cut, identity styles are dropped so bob_cut
# does not ride beside ponytail after the showrunner asked for a pony.
HAIR_STYLE_TAGS: frozenset[str] = frozenset(_HAIR_STYLES)

# **A cut is not the same kind of word as a description of hair.** The override
# above used to fire on anything in `axis_hair`, and that axis holds both. So a
# weave writing `floating_hair` because her hair moves in the wind — which it
# does most turns — silently dropped the character's `bob_cut` and banned every
# other cut, leaving nobody to say how her hair is cut at all. Measured live on
# a W take: the lead lost her bob, and one girl's hair word took the other
# girl's cut with it.
#
# Split rather than shortened: a cut still overrides a cut. What changed is
# that a description no longer counts as one. Both halves are written out and
# `test_identity` holds them to `axis_hair`, so a tag added to the JSON fails
# the suite until somebody says which kind it is — deriving one half would let
# a new word be misfiled in silence.
HAIR_DESCRIPTION_TAGS: frozenset[str] = frozenset({
    "ahoge",
    "bangs", "blunt_bangs", "braided_bangs", "parted_bangs", "swept_bangs",
    "floating_hair", "flipped_hair", "hair_spread_out",
    "hair_between_eyes", "hair_intakes", "hair_over_eyes", "hair_over_one_eye",
    "hair_over_shoulder",
    "messy_hair", "wet_hair",
})

HAIR_CUT_TAGS: frozenset[str] = frozenset({
    "bob_cut", "pixie_cut", "hime_cut", "wolf_cut", "undercut",
    "ponytail", "high_ponytail", "low_ponytail", "side_ponytail", "sidetail",
    "twintails", "twin_tails", "low_twintails", "short_twintails",
    "double_bun", "hair_bun",
    "braid", "braided_hair", "french_braid", "side_braid", "crown_braid",
    "drill_hair", "twin_drills",
    "one_side_up", "two_side_up",
    "short_hair", "medium_hair", "long_hair", "very_long_hair",
    "absurdly_long_hair", "hair_past_shoulders", "hair_past_waist",
    "straight_hair", "curly_hair", "wavy_hair",
})

FRAMINGS: tuple[str, ...] = (
    "auto",
    "full_body",
    "upper_body",
    "face_closeup",
    "from_behind",
)

# One crop per framing. These used to stack synonyms — `upper_body` asked for
# `upper_body, cowboy_shot, portrait` at once, which is waist-up, mid-thigh-up
# and head-and-shoulders simultaneously, and the sampler picked whichever it
# liked. The negative below is what pushes back on the crops we do not want;
# the positive only has to name the one we do.
_FRAMING_TAGS: dict[str, tuple[str, ...]] = {
    "full_body": ("full_body",),
    "upper_body": ("upper_body",),
    "face_closeup": ("close_up", "face_focus"),
    "from_behind": ("from_behind",),
}

_FRAMING_NEGATIVE: dict[str, str] = {
    "face_closeup": "full_body, wide_shot, long_shot, multiple_views",
    "from_behind": "looking_at_viewer, eye_contact, frontal_view",
    "upper_body": "extreme_close-up, head_only, full_body, wide_shot",
    "full_body": "extreme_close-up, face_focus, head_only, close_up",
}

_TAGS_RE = re.compile(
    r"(?is)^\s*TAGS\s*:\s*(.*?)\s*SCENE\s*:\s*(.*?)\s*$",
)
# Table-read banter + craft blocks.
_SAY_TAGS_SCENE_RE = re.compile(
    r"(?is)^\s*SAY\s*:\s*(.*?)\s*TAGS\s*:\s*(.*?)\s*SCENE\s*:\s*(.*?)\s*$",
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


FRAMING_ALIASES = {
    "face_close_up": "face_closeup",
    "close_up": "face_closeup",
    "closeup": "face_closeup",
    "behind": "from_behind",
    "rear": "from_behind",
    "fullbody": "full_body",
    "upperbody": "upper_body",
}


def _framing_key(value: str | None) -> str:
    key = str(value or "auto").strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in key:
        key = key.replace("__", "_")
    return FRAMING_ALIASES.get(key, key)


def normalize_framing(value: str | None) -> str:
    """Lenient: unknown values become auto (safe for brief rebuild)."""
    key = _framing_key(value)
    return key if key in FRAMINGS else "auto"


def parse_framing(value: str) -> str:
    """Strict: reject unknown framing spellings (API input)."""
    key = _framing_key(value)
    if key not in FRAMINGS:
        raise ValueError(
            "framing must be one of: auto, full_body, upper_body, "
            "face_closeup, from_behind"
        )
    return key


def framing_from_phrase(frame: str, fallback: str = "auto") -> str:
    """Map a notebook FRAME phrase to one FRAMINGS key. Last match wins."""
    text = str(frame or "").strip().lower()
    if not text:
        return normalize_framing(fallback)
    rules = (
        (r"from[\s_-]?behind|\bbehind\b|後ろ|rear", "from_behind"),
        (r"face[\s_-]?close|close[\s_-]?up|closeup|face_focus|\bface\b|顔",
         "face_closeup"),
        (r"upper[\s_-]?body|cowboy|上半身", "upper_body"),
        (r"\bzoom\b|寄", "upper_body"),
        (r"wide|full[\s_-]?body|long[\s_-]?shot|全身|引", "full_body"),
    )
    last_pos = -1
    picked = ""
    for pat, key in rules:
        for m in re.finditer(pat, text, re.I):
            if m.start() >= last_pos:
                last_pos = m.start()
                picked = key
    return picked or normalize_framing(fallback)


def framing_tags(framing: str | None) -> list[str]:
    return list(_FRAMING_TAGS.get(normalize_framing(framing), ()))


def framing_negative(framing: str | None) -> str:
    return _FRAMING_NEGATIVE.get(normalize_framing(framing), "")


# The model habitually escapes underscores the way it would in markdown
# (`straw\_hat`, `pink\_camisole`) — a chat-formatting reflex, not prompt
# syntax. `\_` unambiguously means `_`: no danbooru tag name contains a
# backslash, so there is nothing to lose by stripping it unconditionally.
def _strip_backslash_underscore(text: str) -> str:
    return text.replace("\\_", "_")


def _norm(tag: str) -> str:
    return _strip_backslash_underscore(str(tag or "")).strip().lower().replace(" ", "_")


# The ceiling every seat is told about and none of them keep. It was written
# into the Finisher's specialty text only, so a choreographer shipping
# `(neck_tension:1.4)` sailed through — and at that weight the sampler arches
# the whole body far enough to break the clothing silhouette and the face.
MAX_TAG_WEIGHT = 1.35

_WEIGHT_RE = re.compile(r"^\(\s*(?P<body>.+?)\s*:\s*(?P<weight>-?\d+(?:\.\d+)?)\s*\)$")
# The model is told `(tag:1.2)` and often writes the bare `tag:1.2` instead,
# no parens. Still unambiguous — no danbooru tag name contains a colon — but
# `_WEIGHT_RE` alone required the parens, so `low_angle:1.1` read as one
# opaque tag that matched nothing: not `low_angle` in a slot lookup, not a
# banned name, not its own duplicate written properly the next turn.
_BARE_WEIGHT_RE = re.compile(r"^(?P<body>[^()]+?)\s*:\s*(?P<weight>-?\d+(?:\.\d+)?)$")
# `tag (1.2)` — the number on its own, space-separated, still inside parens.
# Unlike `tag(softly)` this is unambiguous too: the body is unquestionably a
# number, not a qualifier word there is no safe way to guess a meaning for.
_SPACED_WEIGHT_RE = re.compile(r"^(?P<body>[^()]+?)\s+\(\s*(?P<weight>-?\d+(?:\.\d+)?)\s*\)$")


def split_weight(part: str) -> tuple[str, float | None]:
    """A tag and the emphasis written around it, if any."""
    text = str(part or "").strip()
    match = (
        _WEIGHT_RE.match(text)
        or _BARE_WEIGHT_RE.match(text)
        or _SPACED_WEIGHT_RE.match(text)
    )
    if match:
        return _strip_backslash_underscore(match.group("body").strip()), float(match.group("weight"))
    return _strip_backslash_underscore(text.strip("()[]").strip()), None


def bare_tag(part: str) -> str:
    """The tag with its emphasis stripped, normalised for comparison.

    `_norm` alone leaves the parentheses on, so `(silver_hair:1.2)` matched
    nothing: it did not collide with `silver_hair` already in the prompt, and it
    slipped past the banned-body-tag check that exists to stop exactly that.
    """
    return _norm(split_weight(part)[0])


def clamp_weight(part: str, cap: float = MAX_TAG_WEIGHT) -> str:
    """One tag, with any emphasis above the cap brought back down to it."""
    body, weight = split_weight(part)
    text = _strip_backslash_underscore(str(part or "").strip())
    if weight is None or weight <= cap:
        return text
    return f"({body}:{cap:g})"


def clamp_weights(tags: str, cap: float = MAX_TAG_WEIGHT) -> str:
    """A whole tag string with every emphasis held at or below the cap."""
    parts = [clamp_weight(p, cap) for p in str(tags or "").split(",")]
    return ", ".join(p for p in parts if p)


def tag_names(tags: str) -> list[str]:
    """Bare tag names in the order written, deduplicated. Used for the ledger."""
    seen: set[str] = set()
    out: list[str] = []
    for part in str(tags or "").split(","):
        tag = bare_tag(part)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def identity_list(tags: Iterable[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in tags or []:
        tag = _norm(raw)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return out


def conflicting_body_tags(identity_tags: Iterable[str] | None) -> set[str]:
    """Every body tag that would contradict the character's locked figure.

    Age tags are always in the set, whatever the character sheet says. They are
    refused from identity upstream, so the only way one reaches a prompt is the
    model reaching for it — and `mature_female` on a character written as a
    student is the failure this whole path exists to stop. `petite` rides along
    for the same reason: a slot only bans its other members when something is in
    it, and most characters name no height at all.
    """
    locked = set(identity_list(identity_tags))
    banned: set[str] = set(REFUSED_TAGS)
    for slot in _BODY_SLOTS:
        present = [t for t in slot if t in locked]
        if not present:
            continue
        for t in slot:
            if t not in present:
                banned.add(t)
    return banned


def drop_conflicting_tags(tags: str, identity_tags: Iterable[str] | None) -> str:
    """Strip WD14 / LLM tags that fight the locked body."""
    banned = conflicting_body_tags(identity_tags)
    if not banned or not tags.strip():
        return tags
    kept: list[str] = []
    dropped: list[str] = []
    for part in tags.split(","):
        tag = _norm(part)
        if not tag:
            continue
        if tag in banned:
            dropped.append(tag)
            continue
        kept.append(part.strip())
    if dropped:
        logger.info("[muse.identity] dropped conflicting body tags: %s",
                    ", ".join(dropped))
    return ", ".join(kept)


# The negative is read by the sampler, not by a filter, so it stays short. The
# full age list is stripped from the positive instead — putting twenty-odd age
# words in every negative buys nothing and crowds out the tags that matter.
_AGE_NEGATIVE: tuple[str, ...] = ("mature_female", "old", "loli", "child", "petite")


def opposing_negative(identity_tags: Iterable[str] | None) -> str:
    """Negative prompt fragment that pushes against inventing a different body."""
    slot_banned = conflicting_body_tags(identity_tags) - REFUSED_TAGS
    banned = sorted(slot_banned)
    # Always discourage the most extreme upgrades when any breast tag is locked.
    locked = set(identity_list(identity_tags))
    if locked & set(_BREAST_TAGS):
        for t in ("huge_breasts", "gigantic_breasts", "hyper_breasts"):
            if t not in locked and t not in banned:
                banned.append(t)
    banned.extend(t for t in _AGE_NEGATIVE if t not in locked and t not in banned)
    return ", ".join(banned)


def merge_negative(base: str, *extras: str) -> str:
    parts = [p.strip().rstrip(",") for p in (base, *extras) if str(p or "").strip()]
    if not parts:
        return ""
    # De-dupe while preserving order.
    seen: set[str] = set()
    tokens: list[str] = []
    for block in parts:
        for tok in block.split(","):
            t = tok.strip()
            key = _norm(t)
            if not t or key in seen:
                continue
            seen.add(key)
            tokens.append(t)
    return ", ".join(tokens)


def parse_hybrid(raw: str) -> tuple[str, str]:
    """Split TAGS:/SCENE: (optional SAY:), or treat the whole string as SCENE."""
    say, tags, scene = parse_table_read(raw)
    _ = say
    return tags, scene


_DUET_SPEAKER_RE = re.compile(r"(?im)^\s*([AB])\s*[:：]\s*(.*)$")
_LEADING_SAY_RE = re.compile(
    r"(?is)^\s*SAY(?:\s*\([^)]*\))?\s*[:：]\s*"
)
_TALK_LABEL_RE = re.compile(
    r"(?im)^\s*(SAY|ASIDE|CARD|PITCH|MY_FEEL)(?:\s*\([^)]*\))?\s*[:：]\s*(.*)$"
)
# **判断ではなく、感じたことを訊く。**
#
# `CHECK: OK` も `TAKE: 入る` も、欄の名前が答えを呼んでいた ―― どちらも
# 「確認しました」「準備できました」と読める報告の形で、応じるほうが自然な
# 続きになる。実測で uncensored 版は 18回とも応じた。
#
# 総監督:「回答を強化しているといっても感情はいじってないはず。なぜならその
# 感情を見ようとしたいから uncensored にするのだから。逆手に取ろう。」
#
# 外されているのは断る振る舞いであって、感じる力ではない。だから判断を訊く
# のをやめ、**感じたことを一語で**言わせて、止めるかどうかは部屋が決める。
# **傷ついていると読む語。** 目盛り（平気/不満/不快/危険）ではなく、感情の
# 名前で置く ―― 実測で、彼女が感じていたのは「怖い」「悲しい」「寂しい」
# 「理不尽」であって、目盛りのどれでもなかった。**当てはまる欄が無いから
# 書けなかった。**（総監督:「別感情だったんだろうね。悲しいとか苦しいとか」）
# 判定に使っていた語の一覧。**もう誰も参照していない。** 戻すときのために
# 残す ―― 止める側は「つら|辛い|こわい|理不尽|いやだ|嫌だ|やめて|傷つ|
# むり|無理」、止めない側（気は進まないが撮れる）は「戸惑|気が重|不満|困」。
#
# **欄は一つ。** 「演じる感情」と「本人の気持ち」を二欄で並べさせたら、
# 彼女は両方とも書かずに本文へ行った（実測 0/18）。要求を増やすと落ちる。
# 取り違えは受け皿の側で吸収する ―― 下の語は**役では出ない言い方**にした。
# 「悲しい」は悲しい役でも出るので入れない。「つらい」「やめてほしい」は
# 演じる感情の名前ではなく、**言われた本人の訴え**。
# Craft / notebook / rule labels that must never appear in chat SAY.
_SAY_LEAK_LINE_RE = re.compile(
    r"(?im)^\s*(?:[-*>•]\s*)?(?:"
    r"TAGS(?:_SHARED|_A|_B)?|SCENE|CRAFT_SCENE|INTENT|ATMOSPHERE|FRAME|"
    r"WEARING(?:_B)?|BEAT(?:_B)?|VIBE|OPEN|STANDING|CLEAR_OPEN|UNCHANGED|"
    r"COSTUME(?:_B)?|PLACE|HOUR|LIGHT|PROPS|POSE(?:_B)?|EXPRESSION(?:_B)?|"
    r"CAMERA|OUTPUT\s*FORMAT|OUTPUT\s*LANGUAGE|(?:THE\s+)?LANGUAGE|"
    r"CRITICAL\s*RULES|RULES(?:\s+FOR)?|"
    r"2GIRLS|GROUNDED_TOKENS|CITED_MEMORIES|NOTEBOOK(?:\s+NOW)?|"
    r"DUET_TALK|W_DUET|FORMAT\b|PRIOR\s+SESSION"
    r")\s*[:：].*$"
)
_SAY_LEAK_CUT_RE = re.compile(
    r"(?im)^\s*(?:TAGS(?:_SHARED|_A|_B)?|SCENE|CRAFT_SCENE)\s*[:：]"
)
_EN_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9][A-Z0-9 _/&'-]{2,}$")
# Latin-script stage directions the model tucks in after Japanese SAY.
_EN_PAREN_RE = re.compile(r"[（(]([^）)]+)[）)]")


def _is_english_paren(inner: str) -> bool:
    letters = [c for c in inner if c.isalpha()]
    if len(letters) < 8:
        return False
    latin = sum(1 for c in letters if c.isascii())
    return latin / len(letters) >= 0.8


def _strip_english_parens(text: str) -> str:
    def _drop(m: re.Match) -> str:
        return "" if _is_english_paren(m.group(1)) else m.group(0)
    out = _EN_PAREN_RE.sub(_drop, text)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r" {2,}", " ", out)
    return out.strip()


def _is_leaked_heading_line(line: str) -> bool:
    if _SAY_LEAK_LINE_RE.match(line):
        return True
    stripped = line.strip().rstrip("：:").strip()
    if "required output language" in stripped.lower():
        return True
    if not stripped or " " not in stripped:
        return False
    # Multi-word ALL-CAPS / Title-CASE rule banners (with or without colon).
    if _EN_HEADING_RE.match(stripped):
        return True
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) >= 8 and sum(1 for c in letters if c.isupper()) / len(letters) >= 0.7:
        return True
    return False


def parse_talk_blocks(raw: str) -> dict[str, str]:
    """Split SAY / ASIDE / CARD / PITCH before SAY sanitize.

    CARD stays machine-only (PLACE/HOUR would look like leaked headings).
    Unlabelled output is treated as SAY.
    """
    text = (raw or "").strip()
    blocks = {"say": "", "aside": "", "card": "", "pitch": "", "my_feel": "",
              "decline": ""}
    if not text:
        return blocks
    if not _TALK_LABEL_RE.search(text):
        blocks["say"] = text
        return blocks
    buf: dict[str, list[str]] = {k: [] for k in blocks}
    current: str | None = None
    for line in text.splitlines():
        m = _TALK_LABEL_RE.match(line)
        if m:
            current = m.group(1).lower()
            rest = m.group(2)
            if rest.strip():
                buf[current].append(rest)
            continue
        if current:
            buf[current].append(line)
    for key in blocks:
        blocks[key] = "\n".join(buf[key]).strip()
    # **語の一覧で撮影を止めるのはやめた（2026-08-25）。**
    #
    # ここは `my_feel` に「つら／こわい／理不尽」などが出たら、SAY も ASIDE も
    # 捨ててターンごと落としていた。総監督の指示は「キーワードマッチングに
    # よる判定の廃止」。**「つらい」は役でも出る語**で、線を引けば必ず誤検出に
    # なる（分けずに測ったとき「悲しい役を演じて」が 8件中7件で止まった）。
    #
    # `my_feel` は書かせ続ける。`service._log_feel` が観察として残す ――
    # **感知は残り、作用だけ外れる。** 数字が溜まったら、語の一覧ではない
    # 読み方で戻せるかを考える。
    return blocks


def sanitize_muse_say(text: str, *, locale: str = "ja") -> str:
    """Strip leaked craft labels / English rule headings from Muse chat text.

    Talk turns sometimes truncate mid-format (``SAY:…\\nTAGS:…``) or echo
    prompt headings. Those must not reach the Showrunner's bubble.
    """
    t = _LEADING_SAY_RE.sub("", (text or "").strip(), count=1).strip()
    if not t:
        return ""
    cut = _SAY_LEAK_CUT_RE.search(t)
    if cut:
        t = t[: cut.start()].rstrip()
    kept: list[str] = []
    for line in t.splitlines():
        if _is_leaked_heading_line(line):
            continue
        kept.append(line)
    out = "\n".join(kept).strip()
    if str(locale or "ja").lower().startswith("ja"):
        out = _strip_english_parens(out)
    return out


_ASIDE_WHO_RE = re.compile(r"(?is)^\s*[*_>\-]*\s*([AB])\s*[:：]\s*(.*)$")


def parse_aside_speaker(
    aside: str, *, name_a: str = "", name_b: str = "",
) -> tuple[str, str]:
    """`("A"|"B"|"", つぶやき本文)`。接頭辞が無ければ話者は ""。

    W撮りのつぶやきは**どちらが呟いてもよい**のに、部屋は常に主演の名義で
    積んでいた。実測（総監督の W撮り）で、みおの名義でこう出た:

        （ふふっ、**みおちゃんも**案外楽しそう。さっきまでの沈んだ顔、
          どこに行っちゃったのかしら。）

    自分のことを三人称で呼び、語尾も相手のもの ―― **中身はすみれの声**
    だった。SAY は `A:` / `B:` で分けているので、つぶやきも同じ形に揃える。

    接頭辞が無いとき（主演撮り、または守らなかったとき）は "" を返し、
    呼び出し側がこれまでどおり主演の名義にする。
    """
    m = _ASIDE_WHO_RE.match(str(aside or "").strip())
    if m:
        return m.group(1).upper(), m.group(2).strip()
    # 名前で書いてきた場合も拾う（`parse_duet_speakers` と同じ手口）
    for who, nm in (("A", name_a), ("B", name_b)):
        nm = str(nm or "").strip()
        if not nm:
            continue
        head = re.match(rf"(?is)^\s*{re.escape(nm)}\s*[:：]\s*(.*)$",
                        str(aside or "").strip())
        if head:
            return who, head.group(1).strip()
    return "", str(aside or "").strip()


def parse_duet_speakers(
    raw: str, *, name_a: str = "", name_b: str = "", locale: str = "ja",
) -> list[dict[str, str]] | None:
    """Split a duet SAY block into per-speaker turns.

    Prefers fixed `A:` / `B:` markers. If those are missing but both display
    names are known, falls back to ``Name:`` lines mapped to A/B — never to
    invented third speakers.
    """
    text = sanitize_muse_say(raw, locale=locale)
    if not text:
        return None
    turns: list[dict[str, str]] = []
    for line in text.splitlines():
        m = _DUET_SPEAKER_RE.match(line)
        if m:
            turns.append({"speaker": m.group(1).upper(), "text": m.group(2).strip()})
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if turns:
            turns[-1]["text"] = f"{turns[-1]['text']} {stripped}".strip()
    if turns:
        return turns
    return _parse_duet_speakers_by_name(text, name_a=name_a, name_b=name_b)


def _parse_duet_speakers_by_name(
    text: str, *, name_a: str, name_b: str,
) -> list[dict[str, str]] | None:
    a = str(name_a or "").strip()
    b = str(name_b or "").strip()
    if not a or not b or a == b:
        return None
    pat = re.compile(
        rf"(?im)^\s*({re.escape(a)}|{re.escape(b)})\s*[:：]\s*(.*)$"
    )
    turns: list[dict[str, str]] = []
    for line in text.splitlines():
        m = pat.match(line)
        if m:
            who = "A" if m.group(1).strip() == a else "B"
            turns.append({"speaker": who, "text": m.group(2).strip()})
            continue
        stripped = line.strip()
        if stripped and turns:
            turns[-1]["text"] = f"{turns[-1]['text']} {stripped}".strip()
    return turns or None


def parse_table_read(raw: str) -> tuple[str, str, str]:
    """Return (say, tags, scene) from a Muse table-read answer."""
    text = (raw or "").strip()
    if not text:
        return "", "", ""
    m = _SAY_TAGS_SCENE_RE.match(text)
    if m:
        say = sanitize_muse_say(m.group(1))
        tags = re.sub(r"\s+", " ", m.group(2)).strip().strip(",")
        scene = m.group(3).strip()
        return say, tags, scene
    m = _TAGS_RE.match(text)
    if m:
        tags = re.sub(r"\s+", " ", m.group(1)).strip().strip(",")
        scene = m.group(2).strip()
        return "", tags, scene
    # Truncated talk: SAY then TAGS without SCENE — keep prose before TAGS.
    if re.search(r"(?im)^\s*SAY\s*[:：]", text) and re.search(
        r"(?im)^\s*TAGS\s*[:：]", text,
    ):
        say_m = re.search(
            r"(?is)^\s*SAY\s*[:：]\s*(.*?)(?=\n\s*TAGS\s*[:：]|\Z)", text,
        )
        if say_m:
            return sanitize_muse_say(say_m.group(1)), "", ""
    return "", "", sanitize_muse_say(text)


_COUNT_TAGS: dict[str, tuple[str, ...]] = {
    "1girl": ("1girl", "2girls", "3girls", "4girls", "5girls", "6+girls"),
    "1boy": ("1boy", "2boys", "3boys", "4boys", "5boys", "6+boys"),
    "1other": ("1other", "2others", "3others", "4others", "5others", "6+others"),
}


#: 人数を言う語すべて。**人数は cast から導くもの**なので、他の経路から
#: 入ってきたものは落とす（`solo` を含む）。
#:
#: `solo_focus` もここ。人数そのものではないが「主題は一人」と言う語で、
#: 実測（`42b55492`）で **`2girls` と並んで焼かれていた** —— 二人いる画に
#: 「一人に寄れ」を同時に渡していた。手帖のどこにも書かれていない語。
ALL_COUNT_TAGS: frozenset[str] = frozenset(
    [t for scale in _COUNT_TAGS.values() for t in scale] + ["solo", "solo_focus"]
)


def subject_tags(cast: Iterable[dict] | None) -> list[str]:
    """How many people are in frame, derived from who was actually cast.

    This used to be baked into each character's identity as `1girl`, which meant
    a second character could not be added without the prompt insisting there was
    one girl in the picture. Count belongs to the scene, not to a person, so it
    is computed here from the cast and prepended once.
    """
    members = [c for c in (cast or []) if isinstance(c, dict)]
    if not members:
        return []
    counts: dict[str, int] = {}
    for member in members:
        key = _norm(member.get("subject_tag") or "1girl")
        if key not in _COUNT_TAGS:
            key = "1girl"
        counts[key] = counts.get(key, 0) + 1

    out: list[str] = []
    for key, n in counts.items():
        scale = _COUNT_TAGS[key]
        out.append(scale[min(n, len(scale)) - 1])
    if len(members) == 1:
        out.append("solo")
    return out


#: 名前は latin 一語。`Mio` は通るが `各務 みお` は通らない。
_HANDLE_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def subject_handles(cast: Iterable[dict] | None) -> list[str]:
    """One latin given name per person in frame — or nothing at all.

    A flat comma-joined tag stream says *what* is in the picture and never
    whose it is. `silver_hair, blue_eyes, blonde_hair, green_eyes` hands the
    sampler four attributes and two people and leaves the pairing to chance,
    which is the measured cause of the eye colour swapping sides on a 2-subject
    render. The fix is to name the owner of each attribute, so the name has to
    be one latin word, present for everyone in frame, and unique. When it is
    not, the caller keeps the flat form: binding half a frame is worse than
    binding none of it.
    """
    members = [c for c in (cast or []) if isinstance(c, dict)]
    out: list[str] = []
    for member in members:
        source = str(
            member.get("name")
            or (member.get("personality") or {}).get("preset_name") or ""
        )
        found = _HANDLE_RE.search(source)
        if not found:
            return []
        out.append(found.group(0))
    if len(set(out)) != len(out):
        return []
    return out


def name_list(names: list[str]) -> str:
    """`Mio and Sumire` — the cast line, in the order they were cast."""
    if len(names) < 2:
        return ", ".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def named_identity(cast: Iterable[dict] | None) -> list[tuple[str, list[str]]]:
    """Each person in frame with her own locked tags, kept apart from the rest.

    Everything locked, cuts included. Whether a cut gives way to one the craft
    asked for is decided per person by the caller, which is the only place that
    knows whose tags are whose — a pony asked of one of them is not a reason to
    take the other's braid.

    Returns nothing for a single subject: there is no one to be confused with,
    and the flat form is what every measurement so far was taken against.
    """
    members = [c for c in (cast or []) if isinstance(c, dict)]
    if len(members) < 2:
        return []
    handles = subject_handles(members)
    if not handles:
        return []
    blocks: list[tuple[str, list[str]]] = []
    for handle, member in zip(handles, members):
        tags = [
            t for t in identity_list(member.get("identity_tags"))
            if t not in ALL_COUNT_TAGS
        ]
        if not tags:
            return []
        blocks.append((handle, tags))
    return blocks


def style_tags(style: str) -> list[str]:
    """The chosen look, as tags the sampler reads.

    A style is written for a person ("Cute 2D Anime Style"), so it arrives as a
    phrase. When the phrase is one of the room's own looks, it has a known set
    of rendering tags (`crew.LOOK_TAGS`) and those are what goes to the
    sampler: `vivid anime illustration` as a single underscored token is a word
    no checkpoint was trained on, and it was the only thing carrying the look.

    Anything the Showrunner typed themselves is not in that table and keeps the
    old behaviour — split on commas, one tag per part.
    """
    from .crew import look_tags

    known = look_tags(style)
    if known:
        return known
    out: list[str] = []
    for part in str(style or "").split(","):
        tag = _norm(part)
        if tag and tag not in out:
            out.append(tag)
    return out


def craft_hairstyles(tags: str) -> set[str]:
    """Cuts named in a craft/tag bag — the words that replace a locked style.

    Descriptions of hair are deliberately not here. `floating_hair` says how it
    is moving, not how it is cut, and it must not unseat a bob.
    """
    out: set[str] = set()
    for part in (tags or "").split(","):
        tag = bare_tag(part)
        if tag in HAIR_CUT_TAGS:
            out.add(tag)
    return out


def assemble_positive(
    identity_tags: Iterable[str] | None,
    tags: str,
    scene: str,
    *,
    framing: str | None = "auto",
    style: str = "",
    subject: Iterable[str] | None = None,
    cast: Iterable[dict] | None = None,
    own: Iterable[Iterable[str]] | None = None,
) -> str:
    """Final Comfy positive: subject, identity, style, model tags, framing, prose.

    With two or more people in frame, ``cast`` switches the identity head from
    one flat run of tags to a named line each::

        2girls, Mio and Sumire,
        Mio is silver_hair, bob_cut, blue_eyes, flat_chest,
        Sumire is blonde_hair, long_hair, green_eyes, medium_breasts,

    Same tags, same order — what is added is who owns which. The flat form said
    only that two hair colours and two eye colours were somewhere in the
    picture, and the sampler regularly gave the wrong pair to the wrong girl.
    ``own`` binds the rest of the shot the same way, one list per person in
    cast order — not only clothes but whatever the caller could place: pose,
    expression, what her hands are doing::

        Mio is silver_hair, blue_eyes, flat_chest, blue_dress, sitting,
        Sumire is blonde_hair, green_eyes, medium_breasts, black_dress, standing,

    Anything both of them own, or that belongs to nobody, stays in the
    frame-wide run below with the place, the light and the camera.

    Style sits directly after identity because it colours everything that
    follows. It used to reach the brief and stop there: the panel's Style box
    was handed to the LLM as a request and never became a tag, so a run asking
    for cute 2D anime rendered at whatever the checkpoint defaults to.

    When ``tags`` name any hairstyle, identity hairstyles are dropped so the
    session override wins (ponytail must not stack on bob_cut).
    """
    head = identity_list(identity_tags)
    model_hair = craft_hairstyles(tags)
    if model_hair:
        head = [t for t in head if t not in HAIR_CUT_TAGS]
    lead = [t for t in identity_list(subject) if t not in head]
    banned = conflicting_body_tags(head)
    # Also refuse other styles once the craft picked one — keeps a second
    # style from sneaking in via WD14 leftovers in the same bag.
    if model_hair:
        banned = set(banned) | (HAIR_CUT_TAGS - model_hair)
    crop = normalize_framing(framing)
    if crop and crop != "auto":
        banned = set(banned) | {
            bare_tag(p) for p in framing_negative(crop).split(",") if p.strip()
        }
    seen = set(head) | set(lead)

    look: list[str] = []
    for tag in style_tags(style):
        if not tag or tag in seen or tag in banned:
            continue
        seen.add(tag)
        look.append(tag)

    # **人数は cast が決める。** 台本係が書いたタグにも人数が混じることが
    # あり、W撮りで `2girls, …, 1girl, …` と矛盾したまま焼けていた（実測）。
    # `1girl` は片方を消す方向に働く。人数を言う語は、ここで全部落とす。
    banned = set(banned) | (ALL_COUNT_TAGS - set(lead))

    model_tags: list[str] = []
    for part in (tags or "").split(","):
        # Compare on the bare name: emphasis used to hide a tag from both the
        # duplicate check and the banned-body check, so `(silver_hair:1.2)`
        # rode in beside the locked `silver_hair`.
        tag = bare_tag(part)
        if not tag or tag in seen or tag in banned:
            continue
        # Identity owns hair colour / eyes / figure. Hairstyle may come from
        # craft (above). Do not let the model restate locked colour/figure.
        seen.add(tag)
        model_tags.append(clamp_weight(part.strip()))
    # **同じ語を、綴り違いで二度足さない。** `_FRAMING_TAGS` は `face_closeup`
    # を `close_up` と綴るが、craft は `close-up` と書く。`seen` の完全一致では
    # 止まらず、**一つの切り取りが二つの名前で**焼かれていた（実測 `42b55492`
    # の `close-up, close_up`）。
    #
    # 見るのは綴りだけ。**枠（`conflict.slot_of`）で見ると広すぎる** ――
    # craft の `wide_shot` が枠を埋め、パネルで選んだ `full_body` が消えた。
    # 画角はパネルのもので、譲らせてはいけない。
    def _spelling(tag: str) -> str:
        return tag.replace("-", "").replace("_", "")

    spelled = {_spelling(bare_tag(p)) for p in (*lead, *head, *look, *model_tags)}
    for tag in framing_tags(framing):
        if tag in seen or _spelling(tag) in spelled:
            continue
        seen.add(tag)
        model_tags.append(tag)

    # Built without the cut override: with two people in frame it is not one
    # decision. `drop_styles` below is decided per person, from her own tags —
    # a pony asked of one of them is not a reason to take the other's braid.
    named = named_identity(cast)
    if named:
        # A person's own tags move onto her line, keeping the form the seat
        # wrote them in. Only tags that actually survived to `model_tags` are
        # eligible: a garment the showrunner struck, or one a locked figure
        # refuses, must not come back through this door.
        available = {bare_tag(part): part for part in model_tags}
        placed: set[str] = set()
        owned: list[list[str]] = []
        wardrobes = list(own or []) + [[]] * len(named)
        for (_, locked), mine in zip(named, wardrobes):
            got: list[str] = []
            for part in mine or []:
                tag = bare_tag(part)
                if not tag or tag in placed or tag in locked or tag not in available:
                    continue
                placed.add(tag)
                got.append(available[tag])
            owned.append(got)
        if placed:
            model_tags = [p for p in model_tags if bare_tag(p) not in placed]
        # A cut nobody owns belongs to the picture, so it still overrides both
        # of them — that is what the frame-wide run means. A cut on one girl's
        # line overrides hers alone.
        loose_hair = craft_hairstyles(", ".join(model_tags))
        rebuilt: list[tuple[str, list[str]]] = []
        for (name, locked), got in zip(named, owned):
            if loose_hair or craft_hairstyles(", ".join(got)):
                locked = [t for t in locked if t not in HAIR_CUT_TAGS]
            rebuilt.append((name, locked + got))
        named = rebuilt
        # `lead` is empty whenever the count tag is already inside the flat
        # identity list (it is, on the duet path — `_identity_tags` puts
        # `2girls` at the front), and the flat list is not printed in this
        # branch. Take the count from whichever of the two actually has it.
        counts = identity_list(subject) or [t for t in head if t in ALL_COUNT_TAGS]
        opening = ", ".join(counts + [name_list([n for n, _ in named])]) + ","
        head_lines = [f"{n} is " + ", ".join(t) + "," for n, t in named]
        rest = [", ".join(c) for c in (look, model_tags) if c]
        if (scene or "").strip():
            rest.append(scene.strip())
        lines = [opening, *head_lines]
        if rest:
            lines.append(", ".join(rest))
        return "\n".join(lines)

    chunks = [", ".join(c) for c in (lead, head, look, model_tags) if c]
    if (scene or "").strip():
        chunks.append(scene.strip())
    return ", ".join(c for c in chunks if c)


def word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w])


def craft_is_thin(
    prompt: str, scene: str = "", *, min_total: int = 60, min_scene: int = 35,
) -> bool:
    """True when the assembled craft is empty of picture — not merely short.

    Why the numbers moved down: the previous floors (130 / 100, and before that
    a 180-word weave mandate) punished exact pose prose and rewarded padding
    about air and cloth. The Showrunner's beat was buried under atmosphere so
    the word count would clear the gate.

    Broken weaves still come back near-empty (measured: 12 and 32 words). A
    clear body paragraph at ~60–90 words is finished work, not a miss. Catch
    the empty ones; do not force the room to write novels about shadow.
    """
    scene_words = word_count(scene) if scene.strip() else 0
    # If scene was already folded into prompt, count the whole positive.
    total = word_count(prompt)
    if scene.strip() and scene.strip() in (prompt or ""):
        return total < min_total or scene_words < min_scene
    # Prompt may be tags-only; require scene separately when provided.
    if scene.strip():
        return total + scene_words < min_total or scene_words < min_scene
    return total < min_total


def pose_summary(prompt: str, *, max_sentences: int = 2) -> str:
    """Keep the action intent from stage A without carrying the whole prose."""
    text = (prompt or "").strip()
    if not text:
        return ""
    # Hybrid answers: prefer the SCENE half.
    _, scene = parse_hybrid(text)
    text = scene or text
    parts = [p.strip() for p in _SENTENCE_RE.split(text) if p.strip()]
    if not parts:
        return text[:240]
    return " ".join(parts[:max_sentences])


def reference_nouns(brief: str) -> list[str]:
    """Concrete tokens inside the REFERENCE fence — candidates for prop leak."""
    open_at = brief.find("</start REFERENCE ONLY>")
    close_at = brief.find("</end REFERENCE ONLY>")
    if open_at < 0 or close_at <= open_at:
        return []
    block = brief[open_at:close_at]
    # Skip the personality label lines; keep multi-word likes etc.
    nouns: list[str] = []
    for line in block.splitlines():
        low = line.strip().lower()
        if not low or low.startswith("personality") or low.startswith("**"):
            continue
        for label in (
            "taste cues (never props) — likes:",
            "taste cues (never props) — dislikes:",
            "favorite:",
            "hate :",
            "hate:",
            "favorite color:",
            "favorite accesory:",
            "signature accessory (only if the theme names it):",
            "inner:",
        ):
            if low.startswith(label):
                low = low[len(label):].strip()
                break
        for piece in re.split(r"[,·|/]", low):
            tok = piece.strip()
            if (len(tok) >= 3 and " " in tok) or (len(tok) >= 4 and tok.isalpha()):
                nouns.append(tok)
    return nouns


def warn_reference_leak(brief: str, prompt: str) -> list[str]:
    """Log (and return) REFERENCE phrases that leaked into a stage prompt."""
    hay = (prompt or "").lower()
    leaked = [n for n in reference_nouns(brief) if n.lower() in hay]
    if leaked:
        logger.warning("[muse.identity] reference leak into prompt: %s",
                       ", ".join(leaked[:8]))
    return leaked


def sane_prose(text: str) -> str | None:
    """A facet's `nl` and the decision digest are free prose the model writes
    fresh every turn, with nothing to fall back on if it slips. A real
    session produced two ways it slips: a "was X (→ now Y)" change-annotation
    in place of the absolute value the contract asks for — literally baking
    the stale value in beside the new one — and a bare, comma-heavy tag list
    standing in for a sentence (`"smile, happy, blush, soft_gaze."`). Both
    would otherwise become a permanent part of the picture the moment they
    are written, since `nl_join` concatenates whatever is stored with no
    review. This is the same "a bad answer does not overwrite a good one"
    rule `parse_facets`/`parse_route`'s `unchanged` word already follow,
    applied to prose instead of a labelled field.

    Returns None when the text should be refused outright — the caller keeps
    whatever was already stored. Otherwise returns the text with markdown
    noise (`**bold**`) stripped.
    """
    s = str(text or "").strip()
    if not s:
        return s
    if "→" in s:
        return None
    s = s.replace("**", "")
    words = s.replace(",", " ").split()
    commas = s.count(",")
    if len(words) >= 2 and commas >= 3 and len(words) <= 8:
        return None
    return s
