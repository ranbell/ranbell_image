"""Refine / Chronicle Visual Spec: labeled category footer + parsers."""
from __future__ import annotations

import re

VISUAL_SPEC_CAT_FIELDS: tuple[str, ...] = (
    "subject_tags",
    "hair_tags",
    "expression_tags",
    "clothing_tags",
    "accessory_tags",
    "pose_tags",
    "background_tags",
    "object_tags",
    "lighting_tags",
)

# Back-compat aliases
CHRONICLE_CAT_FIELDS = VISUAL_SPEC_CAT_FIELDS
REFINE_CAT_FIELDS = VISUAL_SPEC_CAT_FIELDS

LABELED_TAG_FOOTER = (
    "SUBJECT_TAGS: [comma,separated,danbooru,tags]\n"
    "HAIR_TAGS: [comma,separated,danbooru,tags]\n"
    "EXPRESSION_TAGS: [comma,separated,danbooru,tags]\n"
    "CLOTHING_TAGS: [comma,separated,danbooru,tags]\n"
    "ACCESSORY_TAGS: [comma,separated,danbooru,tags]\n"
    "POSE_TAGS: [comma,separated,danbooru,tags]\n"
    "BACKGROUND_TAGS: [comma,separated,danbooru,tags]\n"
    "OBJECT_TAGS: [comma,separated,danbooru,tags]\n"
    "LIGHTING_TAGS: [comma,separated,danbooru,tags]"
)

# Chronicle prose prompt wraps the footer with Pass-1 instructions.
CHRONICLE_LABELED_TAG_FOOTER = (
    "After the 5-paragraph prose (still inside POSITIVE, after the prose), "
    "output ONLY these labeled category lines. Prefer tags already present in "
    "the PASS 1 TAG LINE — do not invent a parallel taxonomy. Leave a bucket "
    "empty if nothing fits:\n\n"
    f"{LABELED_TAG_FOOTER}"
)

VS_LABEL_RE = re.compile(
    r"^(SUBJECT|HAIR|EXPRESSION|CLOTHING|ACCESSORY|POSE|BACKGROUND|OBJECT|LIGHTING)_TAGS:\s*(.*)$",
    re.MULTILINE | re.IGNORECASE,
)

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


def merge_category_tags(
    *sources: dict[str, list[str]] | None,
) -> dict[str, list[str]]:
    """Merge category dicts; first occurrence of each tag wins globally."""
    out: dict[str, list[str]] = {k: [] for k in VISUAL_SPEC_CAT_FIELDS}
    seen_global: set[str] = set()
    for src in sources:
        if not src:
            continue
        for key in VISUAL_SPEC_CAT_FIELDS:
            for tag in src.get(key) or []:
                t = str(tag).strip().replace(" ", "_")
                k = t.lower()
                if not t or k in seen_global:
                    continue
                seen_global.add(k)
                out[key].append(t)
    return {k: v for k, v in out.items() if v}
