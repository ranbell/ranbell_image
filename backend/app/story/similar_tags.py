"""Harvest WD14 tags from near-but-different gallery images."""
from __future__ import annotations

import logging
import random
from typing import Any, Callable

from ..alignment.bm25_matcher import tokenize

logger = logging.getLogger(__name__)

# Pose/action tokens — never drop via BM25-vs-fixed (identity fights only).
_POSE_KEEP_TOKENS = frozenset({
    "standing", "sitting", "kneeling", "lying", "crouching", "squatting",
    "leaning", "reaching", "pointing", "walking", "running", "jumping",
    "falling", "holding", "gripping", "clenched", "touching", "grabbing",
    "hugging", "carrying", "lifting", "pushing", "pulling", "waving",
    "bowing", "turning", "bending", "stretching", "outstretched", "raised",
    "covering", "hiding", "pouring", "wiping", "writing", "reading",
    "eating", "drinking", "cooking", "dancing", "singing", "fighting",
    "dynamic", "pose", "looking",
})

# Identity-ish tokens where BM25 near-match to fixed tags is dangerous.
_IDENTITY_RISK_TOKENS = frozenset({
    "hair", "eyes", "eye", "blonde", "blond", "silver", "brown", "black",
    "white", "red", "blue", "green", "purple", "pink", "orange", "aqua",
    "teal", "violet", "grey", "gray", "ahoge", "twintails", "ponytail",
})


def harvest_wd14_from_docs(
    docs: list[dict] | None,
    *,
    per_image: int = 20,
    cap: int = 60,
    exclude: set[str] | None = None,
) -> list[str]:
    """Pull unique WD14 tags from similar-image search hits (order preserved)."""
    blocked = {t.lower() for t in (exclude or set())}
    out: list[str] = []
    seen: set[str] = set()
    for doc in docs or []:
        for raw in (doc.get("wd14_tags") or [])[:per_image]:
            t = str(raw).strip().replace(" ", "_")
            k = t.lower()
            if not t or k in seen or k in blocked:
                continue
            seen.add(k)
            out.append(t)
            if len(out) >= cap:
                return out
    return out


def sample_tags_by_ratio(
    tags: list[str],
    ratio: float,
    *,
    budget: int,
) -> list[str]:
    """Take up to ``budget * ratio`` tags from ``tags`` (at least 1 if ratio>0)."""
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        r = 0.0
    r = max(0.0, min(1.0, r))
    if r <= 0 or budget <= 0 or not tags:
        return []
    n = max(1, int(round(budget * r)))
    n = min(n, budget, len(tags))
    return list(tags[:n])


def situation_embed_query(
    *,
    situation: str = "",
    gesture: str = "",
    focal: list[str] | None = None,
    user_topic: str = "",
    shot: str = "",
    mood: str = "",
) -> str:
    """Compact text query for embedding a desired shot situation."""
    parts = [
        str(situation or "").strip(),
        str(gesture or "").strip(),
        str(shot or "").strip(),
        str(mood or "").strip(),
        ", ".join(str(t).strip() for t in (focal or []) if str(t).strip()),
        str(user_topic or "").strip(),
    ]
    return " ".join(p for p in parts if p)


def _tag_jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _dedup_scored_docs(
    scored: list[tuple[dict, float]],
    *,
    threshold: float = 0.70,
) -> list[tuple[dict, float]]:
    kept: list[tuple[dict, float]] = []
    kept_sets: list[set[str]] = []
    for doc, score in scored:
        tags = {
            str(t).strip().replace(" ", "_").lower()
            for t in (doc.get("wd14_tags") or [])
            if t
        }
        if tags and any(_tag_jaccard(tags, ks) >= threshold for ks in kept_sets):
            continue
        kept.append((doc, score))
        kept_sets.append(tags)
    return kept


def pick_near_but_different(
    scored: list[tuple[dict, float]],
    *,
    n: int = 4,
    too_close: float = 0.80,
) -> list[dict]:
    """Pick mid-similarity neighbors (close but not near-identical)."""
    if not scored or n <= 0:
        return []
    filtered = [(d, s) for d, s in scored if float(s) < float(too_close)]
    if not filtered:
        filtered = list(scored)
    scores = sorted(float(s) for _, s in filtered)
    p25 = scores[len(scores) // 4]
    p75 = scores[(len(scores) * 3) // 4]
    in_range = [(d, s) for d, s in filtered if p25 <= float(s) <= p75]
    if not in_range and len(filtered) > n:
        skip = max(1, len(filtered) // 4)
        in_range = filtered[skip:]
    if not in_range:
        in_range = filtered
    in_range = _dedup_scored_docs(in_range, threshold=0.70)
    if len(in_range) > max(n * 3, 12):
        in_range = random.sample(in_range, max(n * 3, 12))
    mid = (p25 + p75) / 2.0 if scores else 0.0
    in_range.sort(key=lambda pair: abs(float(pair[1]) - mid))
    return [d for d, _ in in_range[:n]]


def exclude_near_fixed_tags(
    candidates: list[str],
    fixed: list[str] | None,
) -> list[str]:
    """Drop candidates that BM25-near-match fixed identity tags."""
    fixed_norm = [
        str(t).strip().replace(" ", "_").lower()
        for t in (fixed or [])
        if str(t).strip()
    ]
    if not fixed_norm:
        return [
            str(t).strip().replace(" ", "_")
            for t in candidates
            if str(t).strip()
        ]

    exact = set(fixed_norm)
    fixed_tokens: set[str] = set()
    for tag in fixed_norm:
        fixed_tokens |= tokenize(tag)

    identity_fixed_tokens = set(fixed_tokens)

    out: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        tag = str(raw).strip().replace(" ", "_")
        key = tag.lower()
        if not tag or key in seen:
            continue
        toks = tokenize(tag)
        if toks & _POSE_KEEP_TOKENS:
            seen.add(key)
            out.append(tag)
            continue
        if key in exact:
            continue
        if len(toks) == 1 and (toks & fixed_tokens):
            continue
        overlap = toks & identity_fixed_tokens
        if len(overlap) >= 2:
            continue
        # Fighting hair/eye colors against lock
        if "hair" in toks and any(f.endswith("_hair") or f == "hair" for f in exact):
            if key.endswith("_hair") and key not in exact:
                continue
        if ("eyes" in toks or "eye" in toks) and any(
            f.endswith("_eyes") or f.endswith("_eye") for f in exact
        ):
            if (key.endswith("_eyes") or key.endswith("_eye")) and key not in exact:
                continue
        seen.add(key)
        out.append(tag)
    return out


def assemble_with_similar_budget(
    *,
    lock_tags: list[str] | None = None,
    focal: list[str] | None = None,
    similar_tags: list[str] | None = None,
    other_tags: list[str] | None = None,
    mix_ratio: float = 0.3,
    budget: int = 20,
) -> tuple[str, list[str]]:
    """Build a capped tag line with a reserved similar-mix slot.

    Returns (tag_line, similar_kept).
    """
    budget = max(1, int(budget or 20))
    try:
        ratio = max(0.0, min(1.0, float(mix_ratio)))
    except (TypeError, ValueError):
        ratio = 0.3
    similar_slots = max(1, int(round(budget * ratio))) if ratio > 0 else 0
    similar_slots = min(similar_slots, budget)

    lock = [str(t).strip().replace(" ", "_") for t in (lock_tags or []) if t]
    focal_n = [str(t).strip().replace(" ", "_") for t in (focal or []) if t]
    similar = [str(t).strip().replace(" ", "_") for t in (similar_tags or []) if t]
    other = [str(t).strip().replace(" ", "_") for t in (other_tags or []) if t]

    chosen: list[str] = []
    seen: set[str] = set()

    def _take(tags: list[str], limit: int | None = None) -> None:
        n = 0
        for t in tags:
            k = t.lower()
            if not t or k in seen:
                continue
            if len(chosen) >= budget:
                return
            seen.add(k)
            chosen.append(t)
            n += 1
            if limit is not None and n >= limit:
                return

    _take(lock)
    similar_kept: list[str] = []
    if similar_slots > 0 and len(chosen) < budget:
        room = min(similar_slots, budget - len(chosen))
        before = len(chosen)
        _take(similar, limit=room)
        similar_kept = list(chosen[before:])
    if len(chosen) < budget:
        _take(focal_n)
    if len(chosen) < budget:
        _take(other)
    if similar_slots > 0 and len(similar_kept) < similar_slots and len(chosen) < budget:
        before = len(chosen)
        _take(
            [t for t in similar if t.lower() not in seen],
            limit=similar_slots - len(similar_kept),
        )
        similar_kept.extend(chosen[before:])

    return ", ".join(chosen[:budget]), similar_kept


async def fetch_near_but_different_docs(
    *,
    ollama: Any,
    db: Any,
    query: str,
    n: int = 4,
    pool: int = 120,
    exclude_sha256s: list[str] | None = None,
    too_close: float = 0.80,
) -> list[dict]:
    """Embed query → scored search → mid-band N neighbors."""
    q = (query or "").strip()
    if not q:
        return []
    n = max(1, min(6, int(n or 4)))
    try:
        vec = await ollama.embed(q)
        scored = await db.search_by_vector_scored(
            vec,
            n_results=max(pool, n * 20),
            exclude_sha256s=exclude_sha256s or [],
            exclude_reference=True,
        )
    except Exception as exc:
        logger.warning("[chronicle] near-but-different search failed: %s", exc)
        return []
    return pick_near_but_different(scored or [], n=n, too_close=too_close)


async def harvest_similar_situation_tags(
    *,
    ollama: Any,
    db: Any,
    query: str,
    n_results: int = 4,
    mix_ratio: float = 0.3,
    budget: int = 20,
    exclude_sha256s: list[str] | None = None,
    lock_exclude: list[str] | None = None,
    filter_fn: Callable[[list[str]], list[str]] | None = None,
) -> tuple[list[str], list[str]]:
    """Near-but-different images → BM25-vs-fixed → ratio sample.

    Returns ``(tags, source_sha256s)``. Failures return ``([], [])``.
    """
    q = (query or "").strip()
    if not q or mix_ratio <= 0 or budget <= 0:
        return [], []
    docs = await fetch_near_but_different_docs(
        ollama=ollama,
        db=db,
        query=q,
        n=max(1, min(6, int(n_results or 4))),
        exclude_sha256s=exclude_sha256s or [],
    )
    sources = [
        str(d.get("sha256") or "").strip()
        for d in docs
        if str(d.get("sha256") or "").strip()
    ]
    exact_block = {
        str(t).strip().replace(" ", "_").lower()
        for t in (lock_exclude or [])
        if t
    }
    harvested = harvest_wd14_from_docs(docs, exclude=exact_block)
    harvested = exclude_near_fixed_tags(harvested, lock_exclude)
    if filter_fn is not None:
        try:
            harvested = filter_fn(harvested)
        except Exception as exc:
            logger.warning("[chronicle] similar-tag filter failed: %s", exc)
    return sample_tags_by_ratio(harvested, mix_ratio, budget=budget), sources
