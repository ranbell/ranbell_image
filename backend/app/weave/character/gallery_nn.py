"""Optional Qdrant nearest-neighbor enrichment for Weave character tags.

When ``quality_policy.gallery_nn`` (or ``inputs.use_gallery_nn``) is true,
Personalitywright output is enriched with WD14 tags harvested from gallery
neighbors. Soft-fails to a no-op when embeddings / gallery are unavailable.

Search space is the same text embedding used by the AI pipeline
(positive_prompt + WD14 + filename via nomic-embed-text) — not pixel CLIP.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from .split_tags import (
    _is_identity_tag,
    _is_prop_tag,
    enforce_identity_prop_split,
    soft_normalize_tag,
)

logger = logging.getLogger(__name__)

# Skip noise / meta / framing when harvesting spice.
_SPICE_BLOCK = frozenset({
    "solo", "1girl", "1boy", "1other", "multiple_girls", "multiple_boys",
    "rating_safe", "rating_questionable", "rating_explicit",
    "highres", "absurdres", "lowres", "masterpiece", "best_quality",
    "newest", "general", "sensitive", "questionable", "explicit",
    "close-up", "close_up", "upper_body", "full_body", "cowboy_shot",
    "from_side", "from_behind", "from_above", "from_below",
    "looking_at_viewer", "looking_away", "smile", "closed_mouth",
    "open_mouth", "blush", "simple_background", "white_background",
    "artist_name", "signature", "watermark", "text",
})

DEFAULT_N_RESULTS = 6
MAX_SPICE = 12
MAX_IDENTITY_ADD = 10
MIN_TAG_VOTES = 2


def is_gallery_nn_enabled(session: dict[str, Any]) -> bool:
    policy = session.get("quality_policy") or {}
    if "gallery_nn" in policy:
        return bool(policy.get("gallery_nn"))
    inputs = session.get("inputs") or {}
    return bool(inputs.get("use_gallery_nn"))


def set_gallery_nn_enabled(session: dict[str, Any], enabled: bool) -> None:
    session.setdefault("quality_policy", {})["gallery_nn"] = bool(enabled)
    session.setdefault("inputs", {})["use_gallery_nn"] = bool(enabled)


def _norm_tags(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        t = soft_normalize_tag(str(raw))
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _dedup_docs_by_tags(docs: list[dict], *, threshold: float = 0.85) -> list[dict]:
    kept: list[dict] = []
    kept_sets: list[set[str]] = []
    for doc in docs:
        tags = set(_norm_tags(doc.get("wd14_tags") or []))
        if not tags:
            continue
        if any(_jaccard(tags, prev) >= threshold for prev in kept_sets):
            continue
        kept.append(doc)
        kept_sets.append(tags)
    return kept


def merge_gallery_tags(
    identity_tags: list[str],
    prop_tags: list[str],
    *,
    signature_prop: str,
    neighbor_docs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure merge: vote neighbor WD14 into identity / prop / spice."""
    base_i = set(_norm_tags(identity_tags))
    base_p = set(_norm_tags(prop_tags))
    sig = soft_normalize_tag(signature_prop)

    docs = _dedup_docs_by_tags(neighbor_docs)
    identity_votes: Counter[str] = Counter()
    prop_votes: Counter[str] = Counter()
    spice_votes: Counter[str] = Counter()

    for doc in docs:
        for raw in doc.get("wd14_tags") or []:
            t = soft_normalize_tag(str(raw))
            if not t or t in _SPICE_BLOCK:
                continue
            if _is_prop_tag(t):
                prop_votes[t] += 1
            elif _is_identity_tag(t):
                identity_votes[t] += 1
            else:
                # Atmosphere / style / place-ish — spice only (never identity).
                spice_votes[t] += 1

    def _is_hair(t: str) -> bool:
        return t.endswith("_hair") or "bangs" in t or "ponytail" in t or "twintails" in t

    def _is_eyes(t: str) -> bool:
        return t.endswith("_eyes") or t.endswith("_eye")

    has_hair = any(_is_hair(t) for t in base_i)
    has_eyes = any(_is_eyes(t) for t in base_i)

    added_identity: list[str] = []
    for tag, votes in identity_votes.most_common():
        if tag in base_i:
            continue
        hairish, eyesish = _is_hair(tag), _is_eyes(tag)
        if votes >= MIN_TAG_VOTES:
            pass
        elif hairish and not has_hair:
            pass
        elif eyesish and not has_eyes:
            pass
        else:
            continue
        added_identity.append(tag)
        if hairish:
            has_hair = True
        if eyesish:
            has_eyes = True
        if len(added_identity) >= MAX_IDENTITY_ADD:
            break

    added_props: list[str] = []
    for tag, votes in prop_votes.most_common():
        if tag in base_p or tag == sig:
            continue
        if votes < MIN_TAG_VOTES:
            continue
        # Do not invent a new signature prop; only thicken prop_tags when
        # consistent with an existing signature / prop set.
        if not base_p and not sig:
            continue
        added_props.append(tag)
        if len(added_props) >= 4:
            break

    spice: list[str] = []
    blocked = base_i | base_p | set(added_identity) | set(added_props) | ({sig} if sig else set())
    for tag, votes in spice_votes.most_common():
        if tag in blocked or votes < MIN_TAG_VOTES:
            continue
        spice.append(tag)
        if len(spice) >= MAX_SPICE:
            break

    new_identity = _norm_tags(list(identity_tags) + added_identity)
    new_props = _norm_tags(list(prop_tags) + added_props)
    new_identity, new_props, new_sig = enforce_identity_prop_split(
        new_identity, new_props, signature_prop=sig or signature_prop,
    )

    refs = []
    for doc in docs[:DEFAULT_N_RESULTS]:
        sha = str(doc.get("sha256") or "")
        if not sha:
            continue
        refs.append({
            "sha256": sha,
            "score": float(doc.get("_score") or 0.0),
            "name": str(doc.get("name") or ""),
        })

    return {
        "identity_tags": new_identity,
        "prop_tags": new_props,
        "signature_prop": new_sig,
        "gallery_spice": spice,
        "gallery_refs": refs,
        "added_identity": added_identity,
        "added_props": added_props,
        "neighbor_count": len(docs),
    }


async def _fetch_neighbors(
    *,
    db,
    ollama,
    embed_model: str,
    query_tags: list[str],
    reference_image_id: str,
    n_results: int,
) -> list[dict[str, Any]]:
    exclude = [reference_image_id] if reference_image_id else []
    docs: list[dict[str, Any]] = []

    if reference_image_id:
        try:
            similar = await db.search_similar(reference_image_id, n_results=n_results)
            for d in similar:
                if d.get("sha256") in exclude:
                    continue
                docs.append(d)
        except Exception as exc:
            logger.info("[weave.gallery_nn] search_similar failed: %s", exc)

    if query_tags:
        try:
            vec = await ollama.embed(", ".join(query_tags[:40]), model=embed_model)
            by_vec = await db.search_by_vector(
                vec,
                n_results=n_results,
                exclude_sha256s=exclude,
                exclude_reference=True,
            )
            seen = {d.get("sha256") for d in docs}
            for d in by_vec:
                sha = d.get("sha256")
                if not sha or sha in seen:
                    continue
                docs.append(d)
                seen.add(sha)
        except Exception as exc:
            logger.info("[weave.gallery_nn] tag-vector search failed: %s", exc)

    return docs


async def enrich_character_from_gallery(
    session: dict[str, Any],
    *,
    db,
    ollama,
    embed_model: str = "nomic-embed-text",
    n_results: int = DEFAULT_N_RESULTS,
) -> dict[str, Any]:
    """Mutate session.character with gallery NN merge. Never raises for soft fails."""
    empty = {
        "applied": False,
        "reason": "skipped",
        "neighbor_count": 0,
        "added_identity": [],
        "added_props": [],
        "gallery_spice": [],
        "gallery_refs": [],
    }
    if not is_gallery_nn_enabled(session):
        return empty

    character = session.setdefault("character", {})
    identity = list(character.get("identity_tags") or [])
    props = list(character.get("prop_tags") or [])
    if not identity:
        empty["reason"] = "no_identity"
        character["gallery_nn"] = empty
        return empty

    ref = str((session.get("inputs") or {}).get("reference_image_id") or "")
    query_tags = _norm_tags(identity + props[:6])
    try:
        neighbors = await _fetch_neighbors(
            db=db,
            ollama=ollama,
            embed_model=embed_model or "nomic-embed-text",
            query_tags=query_tags,
            reference_image_id=ref,
            n_results=n_results,
        )
    except Exception as exc:
        logger.warning("[weave.gallery_nn] fetch failed: %s", exc)
        empty["reason"] = f"fetch_error:{exc}"
        character["gallery_nn"] = empty
        character["gallery_refs"] = []
        character["gallery_spice"] = []
        return empty

    if not neighbors:
        empty["reason"] = "no_neighbors"
        character["gallery_nn"] = empty
        character["gallery_refs"] = []
        character["gallery_spice"] = []
        return empty

    merged = merge_gallery_tags(
        identity,
        props,
        signature_prop=str(character.get("signature_prop") or ""),
        neighbor_docs=neighbors,
    )
    character["identity_tags"] = merged["identity_tags"]
    character["prop_tags"] = merged["prop_tags"]
    character["signature_prop"] = merged["signature_prop"]
    character["gallery_spice"] = merged["gallery_spice"]
    character["gallery_refs"] = merged["gallery_refs"]
    character["source"] = "personality+gallery_nn"
    summary = {
        "applied": True,
        "reason": "ok",
        "neighbor_count": merged["neighbor_count"],
        "added_identity": merged["added_identity"],
        "added_props": merged["added_props"],
        "gallery_spice": merged["gallery_spice"],
        "gallery_refs": merged["gallery_refs"],
    }
    character["gallery_nn"] = summary
    return summary
