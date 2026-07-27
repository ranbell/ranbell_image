"""Positive tag budget for Weave compile (identity / throughline first)."""
from __future__ import annotations

from ..character.split_tags import soft_normalize_tag

# Anime image models degrade past ~20–24 tags.
WEAVE_MAX_TAGS = 24

_SUBJECT_ANCHORS = frozenset({
    "1girl", "2girls", "3girls", "1boy", "2boys", "multiple_girls",
    "solo", "duo",
})


def cap_positive_tags(
    tags: list[str],
    *,
    priority: list[str] | None = None,
    max_tags: int = WEAVE_MAX_TAGS,
) -> list[str]:
    """Hard-cap tag list; keep subject anchors + priority, then fill in order."""
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        t = soft_normalize_tag(str(raw)) if str(raw).isascii() else str(raw).strip()
        if not t:
            continue
        key = t.lower().replace("-", "_")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(t)
    if max_tags < 1 or len(deduped) <= max_tags:
        return deduped

    by_key = {t.lower().replace("-", "_"): t for t in deduped}
    chosen: list[str] = []
    chosen_keys: set[str] = set()

    def _take(key: str) -> None:
        if key in chosen_keys or key not in by_key or len(chosen) >= max_tags:
            return
        chosen.append(by_key[key])
        chosen_keys.add(key)

    for t in deduped:
        k = t.lower().replace("-", "_")
        if k in _SUBJECT_ANCHORS:
            _take(k)
    for raw in priority or []:
        t = soft_normalize_tag(str(raw)) if str(raw).isascii() else str(raw).strip()
        if t:
            _take(t.lower().replace("-", "_"))
    for t in deduped:
        _take(t.lower().replace("-", "_"))
        if len(chosen) >= max_tags:
            break
    return chosen


def cap_positive_line(
    tag_line: str | list[str],
    *,
    priority: list[str] | None = None,
    max_tags: int = WEAVE_MAX_TAGS,
) -> str:
    if isinstance(tag_line, (list, tuple)):
        tags = [str(t) for t in tag_line]
    else:
        tags = [p.strip() for p in str(tag_line or "").split(",") if p.strip()]
    return ", ".join(cap_positive_tags(tags, priority=priority, max_tags=max_tags))
