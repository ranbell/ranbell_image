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

logger = logging.getLogger(__name__)

# Mutually exclusive breast-size tags. Presence of one in identity means every
# other member of the set is a conflict if WD14 or the LLM invents it.
_BREAST_TAGS: tuple[str, ...] = (
    "flat_chest",
    "small_breasts",
    "medium_breasts",
    "large_breasts",
    "huge_breasts",
    "gigantic_breasts",
    "perky_breasts",
)

# Broader body-type slots that should not be upgraded by a draft guess.
_BODY_SLOTS: tuple[tuple[str, ...], ...] = (
    _BREAST_TAGS,
    ("petite", "tall", "short", "loli"),
    ("slim", "slender", "skinny", "curvy", "plump", "fat", "muscular",
     "athletic", "toned", "abs"),
)

FRAMINGS: tuple[str, ...] = (
    "auto",
    "full_body",
    "upper_body",
    "face_closeup",
    "from_behind",
)

_FRAMING_TAGS: dict[str, tuple[str, ...]] = {
    "full_body": ("full_body", "wide_shot"),
    "upper_body": ("upper_body", "cowboy_shot", "portrait"),
    "face_closeup": ("close_up", "portrait", "face_focus", "looking_at_viewer"),
    "from_behind": ("from_behind", "back", "looking_back"),
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
    """Every body tag that would contradict the character's locked figure."""
    locked = set(identity_list(identity_tags))
    banned: set[str] = set()
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


def opposing_negative(identity_tags: Iterable[str] | None) -> str:
    """Negative prompt fragment that pushes against inventing a different body."""
    banned = sorted(conflicting_body_tags(identity_tags))
    # Always discourage the most extreme upgrades when any breast tag is locked.
    locked = set(identity_list(identity_tags))
    if locked & set(_BREAST_TAGS):
        for t in ("huge_breasts", "gigantic_breasts", "hyper_breasts"):
            if t not in locked and t not in banned:
                banned.append(t)
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
    """Split a TAGS:/SCENE: answer, or treat the whole string as SCENE prose."""
    text = (raw or "").strip()
    if not text:
        return "", ""
    m = _TAGS_RE.match(text)
    if m:
        tags = re.sub(r"\s+", " ", m.group(1)).strip().strip(",")
        scene = m.group(2).strip()
        return tags, scene
    # Model ignored the format — keep the prose, leave tags empty so identity
    # lock still leads and we do not invent a tag list.
    return "", text


def assemble_positive(
    identity_tags: Iterable[str] | None,
    tags: str,
    scene: str,
    *,
    framing: str | None = "auto",
) -> str:
    """Final Comfy positive: locked identity, model tags, framing, scene prose."""
    head = identity_list(identity_tags)
    banned = conflicting_body_tags(head)
    model_tags: list[str] = []
    seen = set(head)
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
    chunks = [", ".join(head)] if head else []
    if model_tags:
        chunks.append(", ".join(model_tags))
    if (scene or "").strip():
        chunks.append(scene.strip())
    return ", ".join(c for c in chunks if c)


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
