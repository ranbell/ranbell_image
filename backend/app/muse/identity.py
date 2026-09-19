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
        # This is the one source for `establishing`. It used to be written only in
        # Muse's `_WIDE_CROP_TAGS`, so writing "establishing shot" into FRAME was
        # never read as a crop. The aliases for a crop live in one place.
        (r"wide|full[\s_-]?body|long[\s_-]?shot|establishing|全身|引", "full_body"),
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


def _drop_unbalanced_brackets(text: str) -> str:
    """Brackets with no partner in this tag — a JSON leftover, not emphasis.

    Measured (`2acfdbe2`, 2026-08-30): the board prompt carried
    `anime_illustration]`. On a turn where weave returned a whole JSON array as a
    string, `split_weight` only strips brackets from **the value used for
    comparison** (`bare_tag` correctly returned `anime_illustration`), so the `]`
    survived in the raw characters headed for the sampler.

    Both `[...]` and `(...)` are **emphasis syntax** in a prompt, so leaving one
    half changes that word's weight. **Balanced pairs are left alone** — only a
    half with no partner is dropped. No word list; the only question is whether the
    brackets match.
    """
    s = str(text or "")
    for open_ch, close_ch in (("(", ")"), ("[", "]")):
        while s.count(open_ch) > s.count(close_ch):
            s = s.replace(open_ch, "", 1)
        while s.count(close_ch) > s.count(open_ch):
            s = "".join(s.rsplit(close_ch, 1))
    return s.strip()


def _strip_edge_underscores(text: str) -> str:
    """`_anime_illustration` / `__` / `__n/a__` — leftovers from JSON.

    **A real tag neither starts nor ends with `_`.** Measured (2026-08-30), weave
    returned a whole array as a string on some turns and the board prompt carried
    `_solo`, `__n/a__` and `_anime_illustration` verbatim. `bare_tag` only strips
    the value used for comparison, so they survive in the raw characters headed
    for the sampler.

    No word list — only **underscores at the edges**. The `_` inside is danbooru's
    separator and is left alone. A word that was all underscores comes back empty
    and `clamp_weights` drops it.
    """
    return str(text or "").strip().strip("_").strip()


def clamp_weight(part: str, cap: float = MAX_TAG_WEIGHT) -> str:
    """One tag, with any emphasis above the cap brought back down to it."""
    body, weight = split_weight(part)
    text = _drop_unbalanced_brackets(
        _strip_backslash_underscore(str(part or "").strip()),
    )
    # **No danbooru tag contains a double underscore.** A space normalises to a
    # single `_`, so a word containing `__` is a way the JSON broke, not a word.
    # Seen in measurement (2026-08-30): `__tags`, `___craft_scene`, `__`, `__n/a__`,
    # `lra__ anime_illustration`. **Looked at before the edges are stripped** —
    # strip first and `__n/a__` survives as `n/a`.
    #
    # A single `_solo` or `_anime_illustration` has an intact word inside, so
    # `_strip_edge_underscores` below rescues it. Only broken words are dropped.
    if "__" in text:
        return ""
    text = _strip_edge_underscores(text)
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
# **Ask what she felt, not what she decided.**
#
# With both `CHECK: OK` and `TAKE: 入る` ("go in"), the field name was calling the
# answer — both read as a report ("checked", "ready"), and going along is the
# natural continuation. Measured, she went along 18 times out of 18: the field was
# deciding the answer.
#
# The capacity to feel and the behaviour of refusing are different things. So asking
# for a decision stopped; she says **what she felt, in one word**, and the room
# decides whether to stop. Rather than making her refuse, the side that takes it is
# placed in the room.
# **The words read as being hurt.** Named as feelings rather than as a scale (fine
# / dissatisfied / uncomfortable / in danger) — measured, what she felt was 「怖い」
# ("frightened"), 「悲しい」 ("sad"), 「寂しい」 ("lonely") and 「理不尽」 ("this is
# unfair"), none of which were points on that scale. **She could not write it
# because there was no field it fitted.** (The Showrunner: "it must have been a
# different feeling — sad, or in pain.")
# The word list that used to decide this. **Nothing references it any more.** Kept
# in case it has to come back — the stopping side was 「つら|辛い|こわい|理不尽|
# いやだ|嫌だ|やめて|傷つ|むり|無理」 (painful / frightening / unfair / I don't want
# to / stop / hurt / I can't), and the non-stopping side (reluctant but shootable)
# was 「戸惑|気が重|不満|困」 (bewildered / heavy-hearted / dissatisfied /
# troubled).
#
# **One field.** Asked for two fields side by side — the feeling she is playing and
# her own feeling — she wrote neither and went straight to the body text (measured
# 0/18). Add requests and it fails. The mix-up is absorbed on the receiving side
# instead: the words below are **ways of speaking that do not occur in a role**.
# 「悲しい」 ("sad") is not among them because a sad role says it too. 「つらい」
# ("this is painful") and 「やめてほしい」 ("I want this to stop") are not names of a
# played emotion but **the appeal of the person it was said to**.
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
#: **A line that is only a label.** `_SAY_LEAK_LINE_RE` requires a colon, so it
#: could not catch a line where the model wrote `CARD` and broke off — and
#: `_is_leaked_heading_line` passes "a single word with no spaces" through, so
#: **`CARD` was appearing at the end of a mutter** (the Showrunner's report,
#: 2026-08-29). It is a closed list of words, so standing alone it is not something
#: she said.
_BARE_BLOCK_LABEL_RE = re.compile(
    r"(?i)^(?:SAY|ASIDE|CARD|PITCH|MY_FEEL|DECLINE|"
    r"TAGS(?:_SHARED|_A|_B)?|SCENE|CRAFT_SCENE|INTENT)$"
)
#: From here on is **what a machine reads**, so it is cut off the bubble.
#:
#: The `COSTUME` family was added on 2026-09-18 — the wardrobe seat spoke its
#: eight-line costume block outright and made a 479-character bubble live
#: (`0239133f`). The source (the seat's contract) was fixed as well, and **it is
#: dropped before arrival too** — the tidiness of the screen is not entrusted to the
#: model's manners (the same stance as stripping field names in `craft_tags`).
_SAY_LEAK_CUT_RE = re.compile(
    r"(?im)^\s*(?:TAGS(?:_SHARED|_A|_B)?|SCENE|CRAFT_SCENE|COSTUME|SILHOUETTE"
    r"|LAYERS|COLOURWAY|PATTERN|FABRIC|CONDITION|HERO|GARMENTS)\s*[:：]"
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
    # **A single word is dropped if it is a label.** The "no spaces, pass through"
    # below exists to protect her short one-liners. The labels are a closed list, so
    # they are taken out first.
    if _BARE_BLOCK_LABEL_RE.match(stripped):
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


#: A field name starting **in the middle** of a line. Looking only at the start of a
#: line means that when two are stacked on one line — `ASIDE: … CARD: …` — the
#: second becomes part of the mutter.
#: The second form (no colon, end of line) is matched **only in upper case** —
#: matched with `(?i)`, a line ending in "a birthday card" would become a field name.
#: What leaked live was a **bare upper-case field name**: `… 気持ちいい……。 CARD`.
_INLINE_LABEL_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"(?i:(SAY|ASIDE|CARD|PITCH|MY_FEEL))(?:\s*\([^)]*\))?\s*[:：]\s*"
    r"|(SAY|ASIDE|CARD|PITCH|MY_FEEL)\s*$"
    r")"
)
#: The shape where an empty field name is left on the tail
#: (`…気持ちいい……。 CARD`).
_BARE_LABEL_TAIL_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(SAY|ASIDE|CARD|PITCH|MY_FEEL)\s*[:：]?\s*$"
)


def _split_labels(line: str) -> list[tuple[str, str | None, str]]:
    """Split a line into "before the label", "the label" and "after it". One piece
    when there is no label."""
    marks = list(_INLINE_LABEL_RE.finditer(line))
    if not marks:
        return [(line, None, "")]
    out: list[tuple[str, str | None, str]] = []
    head = line[:marks[0].start()]
    if head.strip():
        out.append((head, None, ""))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
        label = str(m.group(1) or m.group(2) or "").lower()
        out.append(("", label, line[m.end():end]))
    return out


def _drop_bare_label_tail(text: str) -> str:
    """Drop a bare field name left at the tail (`… CARD` / `… PITCH:`)."""
    out = str(text or "")
    for _ in range(3):
        stripped = _BARE_LABEL_TAIL_RE.sub("", out).rstrip()
        if stripped == out:
            break
        out = stripped
    return out


def trim_to_a_sentence(text: str, cap: int) -> str:
    """Stop text that exceeds a cap **at a sentence boundary**. (2026-09-18)

    The Showrunner: "it looks like text gets cut off by prompt overflow". The cap
    itself is needed — neither the prose handed to the picture nor the memory
    handed to her can be unbounded. What is wrong is **where the cut falls**:
    `text[:900]` lands in the middle of a word, so a tail like `a heavy knit card`
    flows straight downstream.

    Stop at the last full stop; with no full stop, on a word boundary. Never walk
    back past half the cap (do not shorten it too far).
    """
    body = str(text or "").strip()
    if len(body) <= cap:
        return body
    head = body[:cap]
    for mark in (". ", "。", "! ", "? ", "！", "？"):
        cut = head.rfind(mark)
        if cut > cap // 2:
            return head[: cut + len(mark)].strip()
    space = head.rfind(" ")
    return (head[:space] if space > cap // 2 else head).strip()


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
        # **A field starting mid-line is read as a break between fields too
        # (2026-09-16).**
        #
        # Only the start of a line was looked at, so live (`f8961eaa`)
        # `ASIDE: 恥ずかしいけど…気持ちいい……。 CARD` became the mutter as it was
        # and `CARD` leaked to the screen (CARD's content was thrown away along with
        # the rest of the line). The Showrunner: "tags such as SAY leak".
        for piece, label, rest in _split_labels(line):
            if label:
                current = label
                if rest.strip():
                    buf[current].append(rest)
            elif current:
                buf[current].append(piece)
    for key in blocks:
        blocks[key] = _drop_bare_label_tail("\n".join(buf[key]).strip())
    # **Stopping a shoot on a word list was abandoned (2026-08-25).**
    #
    # This used to throw away both SAY and ASIDE and drop the whole turn whenever
    # `my_feel` held 「つら／こわい／理不尽」 (painful / frightening / unfair) and
    # the like. The Showrunner's instruction was "abolish judgement by keyword
    # matching". **「つらい」 ("painful") is a word a role says too**, and drawing a
    # line on it guarantees false positives (measured without splitting them,
    # 「悲しい役を演じて」 — "play a sad part" — was stopped 7 times in 8).
    #
    # `my_feel` is still written. `service._log_feel` keeps it as observation —
    # **the sensing stays and only the effect comes off.** Once the numbers pile up,
    # think about whether it can come back in a reading that is not a word list.
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
    """`("A"|"B"|"", the mutter itself)`. With no prefix the speaker is "".

    In a duet **either of them may mutter**, yet the room always filed it under
    the lead. Measured (the Showrunner's duet), this came out under Mio's name:

        (Hee — **Mio-chan** looks like she is enjoying herself after all. Where
         did that downcast face from a moment ago go?)

    She refers to herself in the third person and the sentence endings are the
    other one's — **the voice inside is Sumire's**. SAY is already split by
    `A:` / `B:`, so the mutter is brought into the same shape.

    With no prefix (a lead shoot, or a turn that did not obey) it returns "" and
    the caller files it under the lead as before.
    """
    m = _ASIDE_WHO_RE.match(str(aside or "").strip())
    if m:
        return m.group(1).upper(), m.group(2).strip()
    # Also catches the case where it wrote the name (the same trick as
    # `parse_duet_speakers`)
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


#: Every word that states a headcount. **The headcount is derived from the cast**,
#: so anything arriving by another road is dropped (`solo` included).
#:
#: `solo_focus` belongs here too. It is not a count but it says "the subject is one
#: person", and measured (`42b55492`) it was **burned in beside `2girls`** — handing
#: a picture of two people "close in on one" at the same time. A word written
#: nowhere in the notebook.
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


#: Names are one Latin word. `Mio` passes; `各務 みお` does not.
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


def named_identity(
    cast: Iterable[dict] | None, *, solo: bool = False,
) -> list[tuple[str, list[str]]]:
    """Each person in frame with her own locked tags, kept apart from the rest.

    Everything locked, cuts included. Whether a cut gives way to one the craft
    asked for is decided per person by the caller, which is the only place that
    knows whose tags are whose — a pony asked of one of them is not a reason to
    take the other's braid.

    Returns nothing for a single subject unless ``solo=True``: the flat
    ``assemble_positive`` path keeps historical measurements; the person-box
    path needs one named row for solo too.
    """
    members = [c for c in (cast or []) if isinstance(c, dict)]
    if len(members) < 1:
        return []
    if len(members) < 2 and not solo:
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


def prose_without_cast_names(scene: str, cast: Iterable[dict] | None) -> str:
    """On a solo shoot, drop the cast names from the prose.

    The gate that drops person-name tags already exists (`_scrub_invented_tags`) —
    the recorded reason is that in danbooru a person-name tag points at a real
    character, so it **pulls in somebody else's face**. Only the tag side was
    closed; **the prose walked straight through**.

    Cutting 1,257 characters of coaxing out of weave (`aefe230`) exposed the hole.
    Measured (30-sample pack):

        08-28  names in prose      1/30
        08-31  before the trim     0/30
        08-31  after the trim      5/30   ← and 5/30 in the final prompt too
            "…, crying, tears, Mio sits slumped at the piano, …"

    **Not dropped on a two-person shoot.** There the names are doing work — take
    them out of "Mio leans on Sumire's shoulder" and nobody knows who is who. On a
    solo shoot they point at nothing and are just extra words for the sampler to
    read.

    Names are replaced with `she` (deleting them leaves a sentence with no
    subject).
    """
    people = [c for c in (cast or []) if c]
    if len(people) != 1:
        return scene
    text = str(scene or "")
    words: set[str] = set()
    for field in ("name", "name_ja"):
        full = str(people[0].get(field) or "").strip()
        if len(full) >= 2:
            # **The full name first.** Matched only in parts, `各務 みお` is
            # replaced twice and becomes `She her sits`. Matching runs longest
            # first, so the whole disappears before the parts.
            words.add(full)
        for word in re.split(r"[\s　]+", full):
            word = word.strip()
            if len(word) >= 2:
                words.add(word)
    def _swap(m: re.Match[str]) -> str:
        # **Subject case at the start of a sentence, object case elsewhere.**
        # Naively replacing with `she` gives "A wide shot looks down toward the lens
        # at she".
        head = text[:m.start()].rstrip()
        if not head or head.endswith((".", "!", "?")):
            return "She"
        return "her"

    for word in sorted(words, key=len, reverse=True):
        # The space between surname and given name matches any kind of gap.
        body = r"\s+".join(re.escape(part) for part in word.split())
        text = re.sub(rf"(?<![A-Za-z]){body}(?:'s)?(?![A-Za-z])",
                      _swap, text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip()


def latin_names(text: str, people: Iterable[dict] | None) -> str:
    """Replace Japanese names mixed into a value with their Latin spelling.

    **The contract alone leaks.** The clerks are told "English only", yet the keys
    of the JSON they are handed are Japanese names — they write with a Japanese
    name in front of them. Live (`2088299b`, 2026-09-02):

        beat_b: standing near the fountain, finger poking **みお's** cheek

    which rides straight into the picture prompt. The gate that drops person-name
    tags has been there a while, but it only covered tags; the text of a field
    went through untouched.

    **The Latin spelling already exists** — the same one `identity.subject_handles`
    produces for the name line. The gate uses those names too, so the line and the
    fields never disagree on spelling.
    """
    body = str(text or "")
    members = [c for c in (people or []) if isinstance(c, dict)]
    handles = subject_handles(members)
    if not (body.strip() and handles):
        return body
    for member, handle in zip(members, handles):
        for field in ("name_ja", "name"):
            full = str(member.get(field) or "").strip()
            if not full or full == handle:
                continue
            for part in [full] + re.split(r"[\s　]+", full):
                part = part.strip()
                # **Surname alone and given name alone are replaced too.** What
                # appeared live was 「みお」, not 「各務 みお」.
                if len(part) >= 2 and part in body:
                    body = body.replace(part, handle)
    return body


#: Where each of them stands when two are in frame. **Which side the lead is on** is
#: decided here, in one place.
#:
#: The Showrunner (2026-09-10): "can we put the lead on the right?"
#:
#: **The picture and the diary read the same value.** Held separately by the picture
#: side (`assemble`) and by the side that tells the diary "in this photo you are the
#: one on the …" (`muse.service._which_one_is_me`), changing one makes **her own
#: memory and the picture disagree** — the same breakage as the two diaries
#: disagreeing about the colour of a ribbon.
#:
#: To swap left and right, this one line. For top and bottom, swap `SIDE_WORDS`.
LEAD_SIDE: str = "right"

#: How the position is said (English for the picture, Japanese for the diary).
SIDE_WORDS: dict[str, tuple[str, str]] = {
    "left": ("on the left", "左"),
    "right": ("on the right", "右"),
}


def side_of(*, lead: bool) -> tuple[str, str]:
    """(the English written into the picture, the Japanese written into the diary).
    `lead=False` gives the partner's side."""
    key = LEAD_SIDE if lead else ("left" if LEAD_SIDE == "right" else "right")
    return SIDE_WORDS[key]


def assemble_from_boxes(
    *, cast: Iterable[dict] | None, people: list[dict], frame_wide: list[str],
    style: str = "", framing: str | None = "auto", scene: str = "",
    support: Iterable[str] | None = None,
) -> str:
    """Build straight from the per-person boxes. **Nothing is fought over.**

    The Showrunner (2026-08-31): "**static traits like hair may go up front**, but
    feelings and actions **have to be in their own slot**", "priority is position
    within the prompt".

    Static and dynamic used to share a line, and `placed` was global, so **when
    both had the same pose only one of them got to sit** (measured, `8c48e8cb`):

        Subaru is navy_hair, …, sitting, …      ← whoever came first took it
        Mio is silver_hair, …, (no pose)

    Here whatever is in a box appears on that person's line. **The same word may
    appear for both.** If both are `sitting`, both sit.

    Order is priority:

        2girls, Subaru and Mio,
        Subaru is <static>,
        Mio is <static>,
        Subaru: <pose, clothes, expression>,   ← right after the name, never
        Mio: <pose, clothes, expression>,        pushed to the back
        <place, background, light, crop, look>,
        <prose>
    """
    # solo=True: a single person goes through the box path too (no flat-bag
    # ending any more).
    named = named_identity(cast, solo=True)
    if not named or not people:
        return ""
    # **Japanese names are stopped here, last of all.** The per-person clerks have
    # a gate at their exits and `frame` does not pass through it — live, `68d1daa5`
    # carried `focus on 各務 みお` all the way into the prompt. Rather than adding a
    # gate per field, it is checked at **the single point where it reaches the
    # picture**.
    members = [c for c in (cast or []) if isinstance(c, dict)]

    def _latin(text: str) -> str:
        return latin_names(text, members)
    # One box per named person; extra boxes are ignored, missing → skip dynamic.
    people = list(people)[: len(named)]
    lead = ", ".join(
        identity_list(subject_tags(cast)) + [name_list([n for n, _ in named])]
    ) + ","
    lines = [lead]
    # **Write one person in one run.** Directly under the static (hair, eyes, build)
    # goes that person's dynamic (pose, clothes, expression).
    #
    # The interleaved version mixed up the builds live (`d2a56ace`, 2026-09-02):
    #
    #     Mio is …, flat_chest, slim,
    #     Subaru is …, large_breasts, tall,
    #     Mio: lying on the bench, …
    #     Subaru: standing near the bench, …
    #
    # **Each person appears twice, alternating**, so where one person's share
    # begins and ends is lost. In the picture Mio took on Subaru's chest, and
    # Subaru's posture (standing) turned into sitting as well. The Showrunner: "it
    # might be better as Mio danbooru / Mio prose / Subaru danbooru / Subaru
    # prose".
    for (name, locked), box in zip(named, people):
        run = list(box.get("beat") or [])
        run += [w for w in (box.get("wearing") or []) if w not in run]
        run += [f for f in (box.get("face") or []) if f not in run]
        # **When a hairstyle is named, drop the cut from the identity side
        # (2026-09-06).** The flat road has had this rule from the start (so that
        # `bob_cut` does not stand next to `ponytail`). The box road did not, and
        # live both appeared:
        #
        #     Mio is silver_hair, **bob_cut**, short_hair, …
        #     Mio: standing, …, **ponytail**, …
        #
        # The hair **colour** belongs to identity. Only the cut yields.
        if any(bare_tag(t) in HAIR_CUT_TAGS for t in run):
            locked = [t for t in locked if bare_tag(t) not in HAIR_CUT_TAGS]
        lines.append(f"{name} is " + ", ".join(locked) + ",")
        if run:
            lines.append(f"{name}: " + _latin(", ".join(run)) + ",")
    # 3. What belongs to nobody. **Quality words come last.** Position is priority,
    # so they go after the place and the crop — the look adds no content and only
    # says how what is already there appears.
    rest = ([_latin(f) for f in frame_wide] + framing_tags(framing)
            + style_tags(style)
            + [str(t) for t in (support or []) if str(t).strip()])
    seen: set[str] = set()
    rest = [r for r in rest if r and not (r.lower() in seen or seen.add(r.lower()))]
    if rest:
        lines.append(", ".join(rest) + ("," if (scene or "").strip() else ""))
    if (scene or "").strip():
        lines.append(_latin(scene.strip()))
    return "\n".join(lines)


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
    scene = prose_without_cast_names(scene, cast)
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
    # Opposite-crop bans used to live here AND in scrub. Two judges meant a
    # framing tag the notebook named could vanish with no clear winner.
    # Conflict drop is scrub-only (`drop_crops_not_in_frame`); assemble only
    # injects the panel / notebook framing tags below. Negatives for the
    # sampler still come from `runtime.negative_for` → `framing_negative`.
    seen = set(head) | set(lead)

    look: list[str] = []
    for tag in style_tags(style):
        if not tag or tag in seen or tag in banned:
            continue
        seen.add(tag)
        look.append(tag)

    # **The cast decides the headcount.** Counts can creep into the tags the writer
    # produces too, and a duet was being burned in with a contradictory
    # `2girls, …, 1girl, …` (measured). `1girl` works toward erasing one of them.
    # Every word stating a count is dropped here.
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
    # **The same word is not added twice under different spellings.**
    # `_FRAMING_TAGS` spells `face_closeup` as `close_up` while craft writes
    # `close-up`. An exact match on `seen` does not stop that, and **one crop was
    # burned in under two names** (measured, `42b55492`'s `close-up, close_up`).
    #
    # Only the spelling is looked at. **Looking by slot (`conflict.slot_of`) is too
    # wide** — craft's `wide_shot` would fill the slot and the `full_body` chosen on
    # the panel would disappear. The crop belongs to the panel and must not yield.
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
