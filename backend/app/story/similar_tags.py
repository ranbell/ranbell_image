"""Harvest WD14 tags from situation-similar gallery images."""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


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
) -> str:
    """Compact text query for embedding a desired shot situation."""
    parts = [
        str(situation or "").strip(),
        str(gesture or "").strip(),
        str(shot or "").strip(),
        ", ".join(str(t).strip() for t in (focal or []) if str(t).strip()),
        str(user_topic or "").strip(),
    ]
    return " ".join(p for p in parts if p)


async def harvest_similar_situation_tags(
    *,
    ollama: Any,
    db: Any,
    query: str,
    n_results: int = 5,
    mix_ratio: float = 0.3,
    budget: int = 20,
    exclude_sha256s: list[str] | None = None,
    lock_exclude: list[str] | None = None,
    filter_fn: Callable[[list[str]], list[str]] | None = None,
) -> list[str]:
    """Embed situation → similar images → sample mix_ratio of WD14 tags.

    Failures return [] so the caller can continue without similar mix.
    """
    q = (query or "").strip()
    if not q or mix_ratio <= 0 or budget <= 0:
        return []
    try:
        vec = await ollama.embed(q)
        docs = await db.search_by_vector(
            vec,
            n_results=max(1, int(n_results or 5)),
            exclude_sha256s=exclude_sha256s or [],
            exclude_reference=True,
        )
    except Exception as exc:
        logger.warning("[chronicle] similar-image tag harvest failed: %s", exc)
        return []

    exclude = {
        str(t).strip().replace(" ", "_").lower()
        for t in (lock_exclude or [])
        if t
    }
    harvested = harvest_wd14_from_docs(docs, exclude=exclude)
    if filter_fn is not None:
        try:
            harvested = filter_fn(harvested)
        except Exception as exc:
            logger.warning("[chronicle] similar-tag filter failed: %s", exc)
    return sample_tags_by_ratio(harvested, mix_ratio, budget=budget)
