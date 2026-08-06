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

logger = logging.getLogger(__name__)

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
    "full_body": "extreme_close-up, face_focus, head_only",
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


def framing_tags(framing: str | None) -> list[str]:
    return list(_FRAMING_TAGS.get(normalize_framing(framing), ()))


def framing_negative(framing: str | None) -> str:
    return _FRAMING_NEGATIVE.get(normalize_framing(framing), "")


def _norm(tag: str) -> str:
    return str(tag or "").strip().lower().replace(" ", "_")


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


def parse_table_read(raw: str) -> tuple[str, str, str]:
    """Return (say, tags, scene) from a Muse table-read answer."""
    text = (raw or "").strip()
    if not text:
        return "", "", ""
    m = _SAY_TAGS_SCENE_RE.match(text)
    if m:
        say = m.group(1).strip()
        tags = re.sub(r"\s+", " ", m.group(2)).strip().strip(",")
        scene = m.group(3).strip()
        return say, tags, scene
    m = _TAGS_RE.match(text)
    if m:
        tags = re.sub(r"\s+", " ", m.group(1)).strip().strip(",")
        scene = m.group(2).strip()
        return "", tags, scene
    return "", "", text


_COUNT_TAGS: dict[str, tuple[str, ...]] = {
    "1girl": ("1girl", "2girls", "3girls", "4girls", "5girls", "6+girls"),
    "1boy": ("1boy", "2boys", "3boys", "4boys", "5boys", "6+boys"),
    "1other": ("1other", "2others", "3others", "4others", "5others", "6+others"),
}


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


def style_tags(style: str) -> list[str]:
    """The chosen look, as tags the sampler reads.

    A style is written for a person ("Cute 2D Anime Style"), so it arrives as a
    phrase. Split it on commas and let each part stand as its own tag; a phrase
    with no commas stays one tag with its spaces turned into underscores, which
    is the shape every other tag in the prompt has.
    """
    out: list[str] = []
    for part in str(style or "").split(","):
        tag = _norm(part)
        if tag and tag not in out:
            out.append(tag)
    return out


def assemble_positive(
    identity_tags: Iterable[str] | None,
    tags: str,
    scene: str,
    *,
    framing: str | None = "auto",
    style: str = "",
    subject: Iterable[str] | None = None,
) -> str:
    """Final Comfy positive: subject, identity, style, model tags, framing, prose.

    Style sits directly after identity because it colours everything that
    follows. It used to reach the brief and stop there: the panel's Style box
    was handed to the LLM as a request and never became a tag, so a run asking
    for cute 2D anime rendered at whatever the checkpoint defaults to.
    """
    head = identity_list(identity_tags)
    lead = [t for t in identity_list(subject) if t not in head]
    banned = conflicting_body_tags(head)
    seen = set(head) | set(lead)

    look: list[str] = []
    for tag in style_tags(style):
        if tag not in seen:
            seen.add(tag)
            look.append(tag)

    model_tags: list[str] = []
    for part in (tags or "").split(","):
        tag = _norm(part)
        if not tag or tag in seen or tag in banned:
            continue
        # Identity owns hair/eyes/figure; do not let the model restate and drift.
        seen.add(tag)
        model_tags.append(part.strip())
    for tag in framing_tags(framing):
        if tag not in seen:
            seen.add(tag)
            model_tags.append(tag)

    chunks = [", ".join(c) for c in (lead, head, look, model_tags) if c]
    if (scene or "").strip():
        chunks.append(scene.strip())
    return ", ".join(c for c in chunks if c)


# ── The shot sheet ───────────────────────────────────────────────────────────
# Anima reads Danbooru tags, natural language, or any mix, and it was trained
# with tag dropout — it does not need every relevant tag listed. The old shape
# handed the model 78 tags and asked an LLM to retype all of them every turn,
# which is both more than the checkpoint wants and how `tatami_mat` became
# `tat_mat` on the last pass with nobody left to notice. Slots are rendered
# here, in Python, so nothing is ever retyped.

_WEIGHT_RE = re.compile(r"\(([^():]+):(\d+(?:\.\d+)?)\)")
# The Grade seat's own spec said never above 1.35 and two 1.4s shipped anyway,
# because the cap lived in a prompt instead of in code.
MAX_WEIGHT = 1.35
# Anima does not need a wall of tags. This is a ceiling, not a target.
MAX_TAGS = 20


def clamp_weights(text: str, *, cap: float = MAX_WEIGHT) -> str:
    """Pull `(tag:1.4)` back to the cap. Unweighted text passes through."""
    def _fix(m: re.Match[str]) -> str:
        value = float(m.group(2))
        if value <= cap:
            return m.group(0)
        return f"({m.group(1)}:{cap:g})"
    return _WEIGHT_RE.sub(_fix, text or "")


def _phrase(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    return str(value or "").strip().rstrip(",")


def render_shot(
    shot: dict[str, Any],
    *,
    identity_tags: Iterable[str] | None = None,
    subject: Iterable[str] | None = None,
    style: str = "",
    framing: str | None = "auto",
    slot_order: Iterable[str] = (),
    quality: Iterable[str] = ("masterpiece", "best quality"),
) -> str:
    """Shot sheet → one Anima prompt: locked tags first, then a plain paragraph.

    Identity, count and framing stay as tags because they are the parts that may
    not drift; everything the crew wrote becomes prose, in a fixed slot order,
    because that is what this checkpoint reads best and because a fixed order is
    one the model cannot scramble.
    """
    head = identity_list(subject) + [
        t for t in identity_list(identity_tags) if t not in set(identity_list(subject))
    ]
    seen = set(head)
    lead: list[str] = []
    for group in (style_tags(style), framing_tags(framing), quality):
        for tag in group:
            tag = str(tag).strip()
            key = _norm(tag)
            if tag and key not in seen:
                seen.add(key)
                lead.append(tag)

    tags = (head + lead)[:MAX_TAGS]

    body: list[str] = []
    for slot in (slot_order or shot.keys()):
        text = _phrase(shot.get(slot))
        if text:
            body.append(clamp_weights(text))

    parts = [", ".join(tags)] if tags else []
    if body:
        parts.append(". ".join(p.rstrip(".") for p in body) + ".")
    return ", ".join(p for p in parts if p)


def apply_delta(
    shot: dict[str, Any],
    *,
    add: dict[str, Any] | None = None,
    remove: Iterable[str] | None = None,
    allowed: Iterable[str] | None = None,
    list_slots: Iterable[str] = (),
    overwrite: bool = False,
) -> dict[str, Any]:
    """Apply one seat's edit without anyone retyping the sheet.

    ``add`` writes only slots the seat owns (``allowed``); everything else is
    dropped rather than trusted, because a seat reaching outside its slots is
    exactly the drift this shape exists to stop. ``remove`` holds phrases to
    delete and is deliberately not restricted — the reduce seat has to be able
    to cut a darkening word out of a slot it does not own, or the light only
    ever goes one way.

    ``overwrite`` is for the seats that *settle* a slot rather than add to it.
    When the Showrunner moves the scene somewhere else, the planner's new place
    has to replace the old one; appending would leave the picture in two rooms.
    """
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in (shot or {}).items()}
    permitted = set(allowed) if allowed is not None else None
    declared = set(list_slots)

    def _is_list(slot: str) -> bool:
        # Declared, or already holding a list. Reading it off the data as well
        # means a caller that forgets to declare a slot does not silently get a
        # comma-joined string where a list belongs.
        return slot in declared or isinstance(out.get(slot), list)

    for slot, value in (add or {}).items():
        if permitted is not None and slot not in permitted:
            logger.info("[muse.identity] %s is not this seat's slot; ignored", slot)
            continue
        text = _phrase(value)
        if not text:
            continue
        items = [i.strip() for i in text.split(",") if i.strip()]
        if _is_list(slot) or isinstance(value, (list, tuple)):
            if overwrite:
                out[slot] = items
                continue
            have = {_norm(i) for i in out.get(slot) or []}
            out.setdefault(slot, [])
            out[slot].extend(i for i in items if _norm(i) not in have)
        else:
            current = "" if overwrite else _phrase(out.get(slot))
            out[slot] = f"{current}, {text}" if current else text

    for raw in remove or []:
        target = _norm(raw)
        if not target:
            continue
        for slot, value in list(out.items()):
            if isinstance(value, list):
                out[slot] = [i for i in value if _norm(i) != target]
            elif isinstance(value, str) and value:
                kept = [
                    p.strip() for p in value.split(",")
                    if p.strip() and target not in _norm(p)
                ]
                out[slot] = ", ".join(kept)
    return out


def word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w])


def craft_is_thin(
    prompt: str, scene: str = "", *, min_total: int = 160, min_scene: int = 100,
) -> bool:
    """True when the assembled craft is too short for a rich render."""
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
