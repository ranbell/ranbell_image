"""Refine Visual Spec: labeled category footer + parsers."""
from __future__ import annotations

import re

VISUAL_SPEC_CAT_FIELDS: tuple[str, ...] = (
    "subject_tags",
    "hair_tags",
    "expression_tags",
    "clothing_tags",
    "accessory_tags",
    "body_parts_tags",
    "pose_tags",
    "background_tags",
    "object_tags",
    "lighting_tags",
)

# Back-compat alias
REFINE_CAT_FIELDS = VISUAL_SPEC_CAT_FIELDS

# Visual Script prose length (paragraph count). Models differ in what length
# they handle well — expose as a UI slider (default keeps historical behavior).
DEFAULT_PROSE_PARAGRAPHS = 5
MIN_PROSE_PARAGRAPHS = 3
MAX_PROSE_PARAGRAPHS = 7


def clamp_prose_paragraphs(n: int | None) -> int:
    try:
        v = int(n) if n is not None else DEFAULT_PROSE_PARAGRAPHS
    except (TypeError, ValueError):
        v = DEFAULT_PROSE_PARAGRAPHS
    return max(MIN_PROSE_PARAGRAPHS, min(MAX_PROSE_PARAGRAPHS, v))


def prose_sentence_range(paragraphs: int) -> str:
    """Sentences-per-paragraph hint scaled with total length."""
    n = clamp_prose_paragraphs(paragraphs)
    if n <= 3:
        return "2-3"
    if n >= 7:
        return "2-5"
    return "2-4"


def visual_script_length_line(paragraphs: int = DEFAULT_PROSE_PARAGRAPHS) -> str:
    """Hard length directive for Visual Script prompts."""
    n = clamp_prose_paragraphs(paragraphs)
    sent = prose_sentence_range(n)
    if n == DEFAULT_PROSE_PARAGRAPHS:
        return (
            f"Write exactly {n} flowing paragraphs ({sent} sentences each). "
            "Do NOT label the paragraphs — the five focuses below map 1:1 "
            "to paragraphs 1–5."
        )
    return (
        f"Write exactly {n} flowing paragraphs ({sent} sentences each). "
        "Do NOT label the paragraphs. Distribute the five focuses below "
        f"across those {n} paragraphs "
        f"({'compress related focuses into fewer paragraphs' if n < DEFAULT_PROSE_PARAGRAPHS else 'split focuses across more paragraphs when needed'})."
    )


LABELED_TAG_FOOTER = (
    "SUBJECT_TAGS: [comma,separated,danbooru,tags]\n"
    "HAIR_TAGS: [comma,separated,danbooru,tags]\n"
    "EXPRESSION_TAGS: [comma,separated,danbooru,tags]\n"
    "CLOTHING_TAGS: [comma,separated,danbooru,tags]\n"
    "ACCESSORY_TAGS: [comma,separated,danbooru,tags]\n"
    "BODY_PARTS_TAGS: [comma,separated,danbooru,tags]\n"
    "POSE_TAGS: [comma,separated,danbooru,tags]  "
    "(≥5 words of concrete action — NEVER standing/sitting alone)\n"
    "BACKGROUND_TAGS: [comma,separated,danbooru,tags]\n"
    "OBJECT_TAGS: [comma,separated,danbooru,tags]\n"
    "LIGHTING_TAGS: [comma,separated,danbooru,tags]"
)


VS_LABEL_RE = re.compile(
    r"^(SUBJECT|HAIR|EXPRESSION|CLOTHING|ACCESSORY|BODY_PARTS|POSE|BACKGROUND|OBJECT|LIGHTING)_TAGS:\s*(.*)$",
    re.MULTILINE | re.IGNORECASE,
)


def pose_word_count(tags: list[str] | None) -> int:
    """Count underscore/space-split word tokens across pose tags."""
    n = 0
    for raw in tags or []:
        t = str(raw).strip().replace(" ", "_").replace("-", "_")
        if not t:
            continue
        n += sum(1 for p in t.split("_") if p)
    return n


_POSE_IDLE_ONLY = frozenset({
    "standing", "sitting", "arms_at_sides", "static_pose",
    "kneeling", "lying", "crouching", "squatting",
})


def pose_tags_are_thin(tags: list[str] | None, *, min_words: int = 5) -> bool:
    """True when pose bucket is idle-only or under ``min_words`` tokens."""
    parts = [
        str(t).strip().replace(" ", "_").lower()
        for t in (tags or [])
        if str(t).strip()
    ]
    if not parts:
        return True
    if pose_word_count(parts) < min_words:
        return True
    if all(p in _POSE_IDLE_ONLY for p in parts):
        return True
    return False


SECTION_MARKER_RE = re.compile(
    r"\[(?:CHARACTER|ACTION|SCENE|DETAIL|MOOD)\]\s*", re.I
)


def split_tag_csv(raw: str) -> list[str]:
    """Normalize a comma-separated Visual Spec tag cell."""
    cleaned = (raw or "").strip().strip("[]")
    out: list[str] = []
    seen: set[str] = set()
    for part in cleaned.split(","):
        t = part.strip().replace(" ", "_")
        if not t or t in ("[", "]"):
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def strip_section_markers(text: str) -> str:
    return SECTION_MARKER_RE.sub("", text or "").strip()


def parse_visual_script(text: str) -> tuple[str, dict[str, list[str]]]:
    """Split Visual Script body into prose + Refine-style category dict."""
    src = text or ""
    first_m = VS_LABEL_RE.search(src)
    if first_m:
        prose = src[: first_m.start()].strip()
        tags_block = src[first_m.start():]
    else:
        prose = src.strip()
        tags_block = ""
    prose = strip_section_markers(prose)
    cats: dict[str, list[str]] = {}
    for m in VS_LABEL_RE.finditer(tags_block):
        field = m.group(1).lower() + "_tags"
        cats[field] = split_tag_csv(m.group(2) or "")
    return prose, cats


