"""Merging WD14 tag sets from several images into one prompt.

Refine built this to fuse up to six source images; Muse reuses it to fuse a
background track with a character track. It is all pure functions over plain
dicts — no FastAPI, no database, no model — which is why it lives here rather
than in the route module it grew up in.

The interesting part is the common/unique decomposition: tags every source
agrees on are ranked by mean confidence and kept down to ``common_ratio``, while
each source additionally gets a budget of its own distinctive tags proportional
to its weight. A token-overlap pass then drops any lower-weight tag that
contradicts a higher-weight one, which is what stops ``blonde_hair`` and
``purple_hair`` both surviving into the same prompt.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable

from ..tags.subject_anchors import (
    SUBJECT_ANCHOR_TAGS as _SUBJECT_ANCHOR_TAGS,
    insert_after_anchors,
)

logger = logging.getLogger(__name__)


_WD14_MUST_INCLUDE_THRESHOLD = 0.70

def removal_tag_set(cfg: dict | None) -> set[str]:
    """Normalize Admin `prompt_removal_tags` to a lowercase underscore set."""
    if not cfg:
        return set()
    return {
        t.lower().replace(" ", "_")
        for t in (cfg.get("prompt_removal_tags") or [])
        if str(t).strip()
    }

def filter_tag_list(tags: list[str], removal: set[str]) -> list[str]:
    """Drop tags present in the removal set (space/underscore equivalent)."""
    if not removal:
        return list(tags)
    out: list[str] = []
    for t in tags:
        name = str(t or "").strip()
        if not name:
            continue
        if name.lower().replace(" ", "_") in removal:
            continue
        out.append(name)
    return out

def _resolve_weights(sha256s: list[str], raw_weights: list[float]) -> list[float]:
    n = len(sha256s)
    if n == 0:
        return []
    if not raw_weights or len(raw_weights) != n:
        return [1.0 / n] * n
    total = sum(raw_weights)
    if total <= 0:
        return [1.0 / n] * n
    return [w / total for w in raw_weights]

def _tags_conflict(tag_a: str, tag_b: str) -> bool:
    """BM25-style: shared meaningful token (len≥3) → same category → likely conflict."""
    if tag_a == tag_b:
        return False  # identical = duplicate, handled separately
    toks_a = {t for t in tag_a.split("_") if len(t) >= 3}
    toks_b = {t for t in tag_b.split("_") if len(t) >= 3}
    return bool(toks_a & toks_b)

def _filter_tags_for_role(scored: list[tuple[str, float]], role: str) -> list[tuple[str, float]]:
    """Filter an image's scored WD14 tags according to its reference role.

    'style'  — contribute only aesthetics: drop character-identity tags
               (subject counts, hair/eyes/clothing/accessories).
    'content' — contribute only the subject: drop scene/background tags.
    Anything else ('both') passes through unchanged.
    """
    if role not in ("style", "content"):
        return scored
    from ..invoke.vocab_bank import _classify_resonance_tag

    if role == "style":
        return [
            (t, s) for t, s in scored
            if t.lower() not in _SUBJECT_ANCHOR_TAGS
            and _classify_resonance_tag(t) != "character"
        ]
    return [(t, s) for t, s in scored if _classify_resonance_tag(t) != "scene"]

_ROLE_CONTEXT_LABELS = {
    "style": " — STYLE reference (aesthetics only: lighting / palette / background / art style)",
    "content": " — CONTENT reference (subject only: character / pose)",
}

def _build_weighted_wd14_context(
    raw_docs: list[tuple[dict, int]],
    weights: list[float],
    conflict_tags: set[str],
    *,
    common_ratio: float = 0.3,
    unique_count: int = 20,
    must_threshold: float = _WD14_MUST_INCLUDE_THRESHOLD,
    roles: list[str] | None = None,
    conflicts: Callable[[str, str], bool] = _tags_conflict,
) -> tuple[str, dict]:
    """Build VLM context with common/unique tag decomposition.

    roles: optional per-image role aligned by original index ('both'|'style'|'content').

    conflicts: how to decide that two tags cannot both survive. The default is
    right for several images of one subject, where a shared word usually does
    mean a disagreement and losing a borderline tag costs nothing. It is wrong
    when the documents describe *different* things — Muse merges a scene with a
    person, and `wet_ground` on the pavement deleted `wet_legs` on the girl.

    Returns (context_str, analysis_dict).
    analysis_dict: common_tags, unique_by_image.
    """
    if not raw_docs:
        return "", {}

    def _role_of(img_idx: int) -> str:
        if roles and img_idx < len(roles):
            return roles[img_idx] or "both"
        return "both"

    # Collect scored tags per image, using correct weight by original index
    image_scored: list[list[tuple[str, float]]] = []
    image_tag_sets: list[set[str]] = []
    image_weights: list[float] = []
    image_indices: list[int] = []

    for doc, img_idx in raw_docs:
        w = weights[img_idx] if img_idx < len(weights) else 0.0
        wd14 = doc.get("wd14_tags", [])
        scores = doc.get("wd14_tags_scores", [])
        if scores and len(scores) == len(wd14):
            scored = sorted(
                [(t, s) for t, s in zip(wd14, scores) if t not in conflict_tags],
                key=lambda x: -x[1],
            )
        else:
            scored = [(t, 0.5) for t in wd14 if t not in conflict_tags]
        scored = _filter_tags_for_role(scored, _role_of(img_idx))
        image_scored.append(scored)
        image_tag_sets.append({t for t, _ in scored})
        image_weights.append(w)
        image_indices.append(img_idx)

    # Pre-build weight and score-map lookups (O(1) access throughout)
    weight_by_idx = dict(zip(image_indices, image_weights))
    score_map_by_idx = {idx: {t: s for t, s in sc} for idx, sc in zip(image_indices, image_scored)}

    # Common tags: intersection of ACTIVE (weight>0) images only
    active_sets = [ts for ts, w in zip(image_tag_sets, image_weights) if w > 0]
    active_scored = [sc for sc, w in zip(image_scored, image_weights) if w > 0]
    common_set: set[str] = active_sets[0].intersection(*active_sets[1:]) if len(active_sets) > 1 else set()

    # Rank common tags by average confidence across active images
    active_score_maps = [{t: s for t, s in sc} for sc in active_scored]
    common_with_scores = sorted(
        [
            (tag, sum(m.get(tag, 0.0) for m in active_score_maps) / len(active_score_maps))
            for tag in common_set
        ],
        key=lambda x: -x[1],
    )
    n_common = max(0, round(len(common_with_scores) * common_ratio))
    selected_common = [t for t, _ in common_with_scores[:n_common]]
    selected_common_set = set(selected_common)

    # Per-image unique tags: budget proportional to weight (weight=0 → 0 tags)
    n_active = max(1, sum(1 for w in image_weights if w > 0))
    raw_unique_by_idx: dict[int, list[str]] = {}

    for img_idx, scored, weight in zip(image_indices, image_scored, image_weights):
        if weight <= 0:
            raw_unique_by_idx[img_idx] = []
            continue
        unique_scored = [(t, s) for t, s in scored if t not in selected_common_set]
        budget = max(0, round(unique_count * weight * n_active))
        must_unique = [t for t, s in unique_scored if s >= must_threshold]
        ref_unique = [t for t, s in unique_scored if s < must_threshold]
        raw_unique_by_idx[img_idx] = (must_unique + ref_unique)[:budget]

    # Cross-image dedup + BM25 conflict resolution
    tag_to_imgs: dict[str, list[tuple[int, float]]] = {}
    for img_idx, tags in raw_unique_by_idx.items():
        w = weight_by_idx.get(img_idx, 0.0)
        for tag in tags:
            tag_to_imgs.setdefault(tag, []).append((img_idx, w))

    removal: dict[int, set[str]] = {}

    # Pass 1: exact duplicates — keep only highest-weight image
    for tag, occurrences in tag_to_imgs.items():
        if len(occurrences) > 1:
            best_idx = max(occurrences, key=lambda x: x[1])[0]
            for img_idx, _ in occurrences:
                if img_idx != best_idx:
                    removal.setdefault(img_idx, set()).add(tag)

    # Pass 2: BM25 conflicts — active images only, remove from lower-weight
    imgs_list = [
        (img_idx, raw_unique_by_idx[img_idx], weight_by_idx[img_idx])
        for img_idx in image_indices
        if weight_by_idx[img_idx] > 0
    ]
    for i, (idx_i, tags_i, w_i) in enumerate(imgs_list):
        for idx_j, tags_j, w_j in imgs_list[i + 1:]:
            lower_idx, lower_tags, higher_tags = (
                (idx_j, tags_j, tags_i) if w_i >= w_j else (idx_i, tags_i, tags_j)
            )
            for tag_low in lower_tags:
                if tag_low in removal.get(lower_idx, set()):
                    continue
                for tag_high in higher_tags:
                    if conflicts(tag_low, tag_high):
                        removal.setdefault(lower_idx, set()).add(tag_low)
                        break

    # Apply removals and build per-image analysis
    unique_by_image: dict[int, dict] = {}  # keyed as str in analysis output
    context_parts: list[str] = []

    for doc, img_idx in raw_docs:
        weight = weight_by_idx.get(img_idx, 0.0)
        raw_tags = raw_unique_by_idx.get(img_idx, [])
        final_tags = [t for t in raw_tags if t not in removal.get(img_idx, set())]

        sm = score_map_by_idx.get(img_idx, {})
        must_final = [t for t in final_tags if sm.get(t, 0.0) >= must_threshold]
        ref_final = [t for t in final_tags if t not in must_final]

        unique_by_image[img_idx] = {
            "must": must_final,
            "ref": ref_final,
            "weight": weight,
            "selected_count": len(final_tags),
            "budget": max(0, round(unique_count * weight * n_active)),
        }

        if weight <= 0:
            continue  # weight=0 の画像はコンテキストに含めない

        pct = round(weight * 100)
        lines: list[str] = []
        prompt_txt = doc.get("positive_prompt", "")
        if prompt_txt:
            lines.append(f"Prompt: {prompt_txt}")
        all_unique = must_final + ref_final
        if all_unique:
            lines.append(f"Style/aesthetic reference tags (influence {pct}%): {', '.join(all_unique)}")
        role_label = _ROLE_CONTEXT_LABELS.get(_role_of(img_idx), "")
        part = f"[Image {len(context_parts) + 1} — influence weight: {pct}%{role_label} — distinctive elements]"
        if lines:
            part += "\n" + "\n".join(lines)
        context_parts.append(part)

    # Assemble context string with priority guide
    sections: list[str] = [
        "When image elements conflict (e.g. different hair colors), prioritize the higher influence weight image."
    ]
    if selected_common:
        sections.append(
            f"[Shared traits — present in all images]:\n{', '.join(selected_common)}"
        )
    sections.extend(context_parts)
    context = "\n\n---\n\n".join(sections)

    analysis = {
        "common_tags": selected_common,
        "common_total": len(common_with_scores),
        "common_selected": len(selected_common),
        "unique_by_image": {str(k): v for k, v in unique_by_image.items()},
    }
    return context, analysis


# ── Visual Spec / subject anchors: see prompt.visual_spec + tags.subject_anchors ──

def _inject_wd14_must_tags(tags_text: str, wd14_analysis: dict) -> str:
    """Merge WD14 must_unique into the tag line after VLM Pass1.

    High-budget (high-weight) images' tags are inserted first.
    Tags already present (case-insensitive) are skipped.
    """
    sorted_images = sorted(
        wd14_analysis.get("unique_by_image", {}).items(),
        key=lambda x: x[1].get("budget", 0),
        reverse=True,
    )
    new_must: list[str] = []
    existing = {t.strip().lower() for t in tags_text.split(",") if t.strip()}
    for _, info in sorted_images:
        for tag in info.get("must", []) + info.get("ref", []):
            key = tag.lower()
            if key not in existing:
                new_must.append(tag)
                existing.add(key)

    if not new_must:
        return tags_text
    return insert_after_anchors(tags_text, new_must)

def _build_all_must(wd14_analysis: dict) -> list[str]:
    """Return weight-descending deduped list of all unique tags (must+ref) for conflict resolution."""
    seen: set[str] = set()
    result: list[str] = []
    for _, info in sorted(
        wd14_analysis.get("unique_by_image", {}).items(),
        key=lambda x: x[1].get("weight", 0),
        reverse=True,
    ):
        for t in info.get("must", []) + info.get("ref", []):
            if t not in seen:
                result.append(t)
                seen.add(t)
    return result

def _apply_must_replacements(tags: list[str], all_must: list[str]) -> list[str]:
    """Replace each tag with a conflicting WD14 must_unique tag (BM25-style), dedup."""
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        rep = next((m for m in all_must if _tags_conflict(tag, m) and tag != m), tag)
        if rep not in seen:
            result.append(rep)
            seen.add(rep)
    return result

def _correct_prose_wd14_conflicts(prose: str, all_must: list[str]) -> str:
    """Replace inline (tag) groups in prose where tags conflict with WD14 must_unique.

    Example: (golden_hair, long_hair) + must=[purple_hair] → (purple_hair, long_hair)
    """
    def _fix_group(m: re.Match) -> str:
        tags = [t.strip() for t in m.group(1).split(",")]
        return f"({', '.join(_apply_must_replacements(tags, all_must))})"

    return re.sub(r"\(([^)]+)\)", _fix_group, prose)

def _enforce_wd14_on_cat_tags(cat_tags: dict[str, list[str]], all_must: list[str]) -> dict[str, list[str]]:
    """Override VLM-generated category tags with WD14 must_unique where they conflict."""
    return {field: _apply_must_replacements(tags, all_must) for field, tags in cat_tags.items()}


# ── Routes ─────────────────────────────────────────────────────────────────────
