"""Reference-image identity mix: hair/eyes from WD14, props stay inferred."""
from __future__ import annotations

import logging
from typing import Any

from .split_tags import enforce_identity_prop_split, soft_normalize_tag

logger = logging.getLogger(__name__)


def _is_hair_tag(t: str) -> bool:
    t = soft_normalize_tag(t)
    return bool(
        t.endswith("_hair")
        or "bangs" in t
        or "ponytail" in t
        or "twintails" in t
        or "braid" in t
        or t in {"ahoge", "sidelocks"}
    )


def _is_eyes_tag(t: str) -> bool:
    t = soft_normalize_tag(t)
    return t.endswith("_eyes") or t.endswith("_eye")


def mix_reference_hair_eyes(
    identity_tags: list[str],
    wd14_tags: list[str],
) -> tuple[list[str], list[str]]:
    """Replace hair/eyes in identity with reference WD14 candidates.

    Returns (new_identity_tags, added_from_reference).
    """
    try:
        from ...story.compose import identity_candidates_from_wd14
    except Exception:  # pragma: no cover
        identity_candidates_from_wd14 = None  # type: ignore

    if not wd14_tags:
        return list(identity_tags), []

    if identity_candidates_from_wd14 is not None:
        cands = [
            soft_normalize_tag(t)
            for t in identity_candidates_from_wd14(wd14_tags)
        ]
    else:
        cands = [soft_normalize_tag(t) for t in wd14_tags]

    ref_hair = [t for t in cands if _is_hair_tag(t)]
    ref_eyes = [t for t in cands if _is_eyes_tag(t)]
    if not ref_hair and not ref_eyes:
        # Fallback: scan raw WD14 for hair/eyes families.
        for t in wd14_tags:
            n = soft_normalize_tag(t)
            if _is_hair_tag(n):
                ref_hair.append(n)
            elif _is_eyes_tag(n):
                ref_eyes.append(n)
        ref_hair = list(dict.fromkeys(ref_hair))
        ref_eyes = list(dict.fromkeys(ref_eyes))

    if not ref_hair and not ref_eyes:
        return list(identity_tags), []

    kept = [
        soft_normalize_tag(t)
        for t in identity_tags
        if not _is_hair_tag(t) and not _is_eyes_tag(t)
    ]
    added: list[str] = []
    # Prefer first (usually highest-confidence WD14 order)
    for t in (ref_hair[:2] + ref_eyes[:2]):
        if t and t not in kept:
            kept.append(t)
            added.append(t)
    return list(dict.fromkeys(kept)), added


async def apply_reference_mix(session: dict[str, Any], db) -> dict[str, Any]:
    """Mutate character identity with reference hair/eyes. Soft-fail safe."""
    empty = {"applied": False, "added_from_reference": [], "reason": "no_reference"}
    ref = str((session.get("inputs") or {}).get("reference_image_id") or "").strip()
    if not ref or db is None:
        return empty
    try:
        doc = await db.get(ref) or {}
    except Exception as exc:
        logger.info("[weave.reference_mix] get failed: %s", exc)
        return {"applied": False, "added_from_reference": [], "reason": f"fetch_error:{exc}"}

    wd14 = list(doc.get("wd14_tags") or [])
    if not wd14:
        return {"applied": False, "added_from_reference": [], "reason": "no_wd14"}

    character = session.setdefault("character", {})
    new_id, added = mix_reference_hair_eyes(
        list(character.get("identity_tags") or []),
        wd14,
    )
    if not added:
        return {"applied": False, "added_from_reference": [], "reason": "no_hair_eyes"}

    identity, props, sig = enforce_identity_prop_split(
        new_id,
        character.get("prop_tags"),
        signature_prop=str(character.get("signature_prop") or ""),
    )
    character["identity_tags"] = identity
    character["prop_tags"] = props
    character["signature_prop"] = sig
    src = str(character.get("source") or "personality")
    if "reference" not in src:
        character["source"] = f"{src}+reference"
    return {"applied": True, "added_from_reference": added, "reason": "ok"}
