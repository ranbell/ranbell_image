"""Unified subject / person-count anchors for Refine, Muse, and Inspire."""
from __future__ import annotations

# Superset of former runners._PERSON_COUNT_TAGS, ai._SUBJECT_ANCHOR_TAGS,
# and generator._SUBJECT_ANCHORS (kept in sync deliberately).
SUBJECT_ANCHOR_TAGS: frozenset[str] = frozenset({
    "1girl", "1boy", "2girls", "2boys", "3girls", "3boys", "4girls", "5girls",
    "6+girls", "6+boys",
    "1other", "2others", "multiple_others",
    "solo", "solo_focus", "duo", "couple", "group",
    "multiple_girls", "multiple_boys", "multiple girls", "multiple boys",
})

# Alias used by safety-net "prepend from WD14" logic.
PERSON_COUNT_TAGS: frozenset[str] = SUBJECT_ANCHOR_TAGS


def insert_after_anchors(tag_line: str, new_tags: list[str]) -> str:
    """Insert ``new_tags`` after the last subject-anchor tag (case-insensitive dedupe)."""
    parts = [t.strip() for t in (tag_line or "").split(",") if t.strip()]
    existing = {p.lower() for p in parts}
    add: list[str] = []
    for tag in new_tags:
        key = tag.lower()
        if key not in existing:
            add.append(tag)
            existing.add(key)
    if not add:
        return tag_line
    cut = max(
        (i + 1 for i, p in enumerate(parts) if p.lower() in SUBJECT_ANCHOR_TAGS),
        default=0,
    ) or len(parts)
    return ", ".join(parts[:cut] + add + parts[cut:])


def ensure_subject_anchor(
    tags_positive: str,
    raw_docs: list,
    *,
    wd14_scores_key: str = "wd14_tags_scores",
    min_score: float = 0.40,
) -> str:
    """If Pass-1 output lacks a subject count tag, prepend the best WD14 one."""
    tag_set = {t.strip().lower() for t in tags_positive.split(",") if t.strip()}
    if tag_set & PERSON_COUNT_TAGS:
        return tags_positive
    best_tag, best_score = "", 0.0
    for doc, _idx in raw_docs:
        wd14 = doc.get("wd14_tags", [])
        scores = doc.get(wd14_scores_key) or []
        for tag, score in zip(wd14, scores):
            if tag in PERSON_COUNT_TAGS and score > best_score:
                best_tag, best_score = tag, score
    if best_tag and best_score >= min_score:
        return f"{best_tag}, {tags_positive}"
    return tags_positive
