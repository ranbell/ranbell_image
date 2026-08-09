"""Chemistry's relationship math: appearance/personality embeddings, co-appearance
amplification, and the resulting tier (顔見知り / 仲良し / 大親友).

Vectors live in CHARACTER_COMPAT_COLLECTION rather than on the character preset
itself — see the comment on that constant. Point ids there are the same uuid
as the matching character's point in CHARACTER_PRESETS_COLLECTION, so the two
never need a lookup table between them.
"""
from __future__ import annotations

import logging
import math
from typing import Any

from qdrant_client import models as qm

from ..db.qdrant_client import (
    CHARACTER_COMPAT_COLLECTION,
    CHARACTER_PRESETS_COLLECTION,
    MUSE_SESSIONS_COLLECTION,
)

logger = logging.getLogger(__name__)

# Inner life reads as "who she is" more than her hairstyle does — weighted
# accordingly. Both are tunable; nothing downstream assumes they sum to 1.
WEIGHT_APPEARANCE = 0.3
WEIGHT_PERSONALITY = 0.7
# Each finished duet shoot together nudges the score up a little, independent
# of how alike the two characters are on paper.
AMPLIFY_STEP = 0.03

_TIER_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.70, "best_friend"),
    (0.40, "close"),
)

# Appearance is read from these `tags` groups — the ones that describe how she
# looks, not how she acts. `hobby_actions` and the rest stay out on purpose.
_APPEARANCE_TAG_GROUPS = (
    "hair_color", "hair_style", "eyes", "body", "expression",
    "favorite_clothes", "headwear_accessory", "footwear", "ears_tails_wings",
)


def appearance_text(preset: dict[str, Any]) -> str:
    tags = preset.get("tags") or {}
    parts: list[str] = [str(preset.get("title_ja") or preset.get("title") or "")]
    for group in _APPEARANCE_TAG_GROUPS:
        parts.extend(str(t) for t in (tags.get(group) or []) if str(t).strip())
    text = " ".join(p for p in parts if p).strip()
    return text or str(preset.get("name_ja") or preset.get("name") or "")


def personality_text(preset: dict[str, Any]) -> str:
    parts: list[str] = [str(t) for t in (preset.get("personality") or []) if str(t).strip()]
    summary = str(preset.get("summary_ja") or preset.get("summary") or "")
    if summary:
        parts.append(summary)
    parts.extend(str(line) for line in (preset.get("inner_ja") or []) if str(line).strip())
    text = " ".join(parts).strip()
    return text or str(preset.get("name_ja") or preset.get("name") or "")


async def embed_character(ollama, preset: dict[str, Any]) -> dict[str, list[float]]:
    appearance, personality = await ollama.embed_batch(
        [appearance_text(preset), personality_text(preset)],
    )
    return {"appearance": appearance, "personality": personality}


async def upsert_character_compat(db, character_id: str, vectors: dict[str, list[float]]) -> None:
    await db._qc.upsert(
        collection_name=CHARACTER_COMPAT_COLLECTION,
        points=[qm.PointStruct(
            id=character_id,
            vector={"appearance": vectors["appearance"], "personality": vectors["personality"]},
            payload={"character_id": character_id},
        )],
    )


async def _get_vectors(db, character_id: str) -> dict[str, list[float]] | None:
    if not character_id:
        return None
    points = await db._qc.retrieve(
        collection_name=CHARACTER_COMPAT_COLLECTION,
        ids=[character_id], with_payload=False, with_vectors=True,
    )
    if not points or not points[0].vector:
        return None
    vec = points[0].vector
    appearance = vec.get("appearance") if isinstance(vec, dict) else None
    personality = vec.get("personality") if isinstance(vec, dict) else None
    if not appearance or not personality:
        return None
    return {"appearance": appearance, "personality": personality}


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _tier_for(score: float) -> str:
    for threshold, tier in _TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return "acquaintance"


async def co_appearance_count(db, char_a_id: str, char_b_id: str) -> int:
    """How many finished duet shoots this pair has already had together."""
    pair = {char_a_id, char_b_id}
    count = 0
    offset = None
    while True:
        points, offset = await db._qc.scroll(
            collection_name=MUSE_SESSIONS_COLLECTION,
            limit=256, offset=offset,
            with_payload=["status", "inputs"],
        )
        for p in points:
            payload = p.payload or {}
            if str(payload.get("status") or "") != "finished":
                continue
            inputs = payload.get("inputs") or {}
            session_pair = {
                str(inputs.get("character_id") or ""),
                str(inputs.get("partner_preset") or ""),
            }
            if session_pair == pair:
                count += 1
        if offset is None or not points:
            break
    return count


async def compatibility(db, char_a_id: str, char_b_id: str) -> dict[str, Any]:
    """Base similarity (from embeddings) amplified by shared history, then tiered."""
    va = await _get_vectors(db, char_a_id)
    vb = await _get_vectors(db, char_b_id)
    if not va or not vb:
        # Not embedded yet (new character, or backfill hasn't run) — neutral
        # rather than an error, since callers use this to colour a UI hint,
        # not to gate anything.
        return {"base": 0.0, "co_appearances": 0, "score": 0.0, "tier": "acquaintance"}
    base = (
        WEIGHT_APPEARANCE * cosine(va["appearance"], vb["appearance"])
        + WEIGHT_PERSONALITY * cosine(va["personality"], vb["personality"])
    )
    co = await co_appearance_count(db, char_a_id, char_b_id)
    score = min(1.0, max(0.0, base + co * AMPLIFY_STEP))
    return {"base": base, "co_appearances": co, "score": score, "tier": _tier_for(score)}


# ── background jobs (spooler contract: reporter, cancel, **kwargs) ──────────

async def run_character_compat_embed(
    reporter, cancel, *, db, ollama, character_id: str,
) -> dict[str, Any]:
    """(Re)embed one character after she is created or edited."""
    from . import presets as presets_db

    reporter.indeterminate()
    preset = await presets_db.get_preset(db, character_id)
    if preset is None:
        return {"status": "skipped", "reason": "character not found"}
    try:
        vectors = await embed_character(ollama, preset)
    except Exception as exc:
        logger.warning("[compat] embed failed for %s: %s", character_id, exc)
        return {"status": "failed", "reason": str(exc)}
    await upsert_character_compat(db, character_id, vectors)
    return {"status": "ok"}


async def run_character_compat_backfill(reporter, cancel, *, db, ollama) -> dict[str, Any]:
    """Embed every character missing appearance/personality vectors.

    Two passes: list everyone first (cheap, no embed calls) so the reporter
    can show done/total instead of the indeterminate spinner the images-side
    MRL backfill has to use for lack of a cheap total.
    """
    presets: list[tuple[str, dict[str, Any]]] = []
    offset = None
    while True:
        points, offset = await db._qc.scroll(
            collection_name=CHARACTER_PRESETS_COLLECTION,
            limit=64, offset=offset, with_payload=True, with_vectors=False,
        )
        presets.extend((str(p.id), p.payload or {}) for p in points)
        if offset is None or not points:
            break

    total = len(presets)
    done = 0
    reporter.update(0.0, f"0 / {total}")
    for character_id, payload in presets:
        raise_if_cancelled = getattr(cancel, "raise_if_set", None)
        if raise_if_cancelled is not None:
            raise_if_cancelled()
        if await _get_vectors(db, character_id):
            continue
        try:
            vectors = await embed_character(ollama, payload)
        except Exception as exc:
            logger.warning("[compat] backfill embed failed for %s: %s", character_id, exc)
            continue
        await upsert_character_compat(db, character_id, vectors)
        done += 1
        reporter.update(done / total if total else 1.0, f"{done} / {total}")
    return {"done": done, "total": total}


async def compat_status(db) -> dict[str, Any]:
    """How many characters already have chemistry vectors, for the admin panel."""
    embedded_ids: set[str] = set()
    offset = None
    while True:
        points, offset = await db._qc.scroll(
            collection_name=CHARACTER_COMPAT_COLLECTION,
            limit=256, offset=offset, with_payload=False, with_vectors=True,
        )
        for p in points:
            vec = p.vector if isinstance(p.vector, dict) else None
            if vec and vec.get("appearance") and vec.get("personality"):
                embedded_ids.add(str(p.id))
        if offset is None or not points:
            break

    total = 0
    offset = None
    while True:
        points, offset = await db._qc.scroll(
            collection_name=CHARACTER_PRESETS_COLLECTION,
            limit=256, offset=offset, with_payload=False, with_vectors=False,
        )
        total += len(points)
        if offset is None or not points:
            break

    return {
        "total": total, "embedded": len(embedded_ids),
        "needs_backfill": len(embedded_ids) < total,
    }
