from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ── Species/race tag blocklist ────────────────────────────────────────────────
# Prevents vocab hints from injecting character-transforming species tags
# (dragon_girl, fox_girl, etc.) into Wander/Surge spirits when the user never
# asked for them. WD14 classifies these as category=0 (General), so they would
# otherwise pass the Qdrant filter and be injected as "guest" or "wild" tags.
#
# Users who explicitly specify a species tag in their prompt are unaffected —
# those tags enter via axis decomposition, not via vocab hints.

_SPECIES_PREFIXES = frozenset({
    "dragon", "fox", "cat", "dog", "wolf", "bunny", "rabbit", "deer",
    "cow", "horse", "tiger", "bear", "lion", "fish", "bird", "frog",
    "lizard", "snake", "spider", "bee", "slime", "ghost", "demon",
    "angel", "oni", "elf", "goblin", "orc", "fairy",
    "lamia", "harpy", "mermaid", "succubus", "vampire",
})

_SPECIES_EXACT = frozenset({
    "kemonomimi_mode", "monster_girl", "furry", "anthro",
    "kemono", "furry_female", "furry_male", "beastman",
    "animal_humanoid", "centaur",
})


def _is_species_tag(tag: str) -> bool:
    if tag in _SPECIES_EXACT:
        return True
    for suffix in ("_girl", "_boy"):
        if tag.endswith(suffix) and tag[: -len(suffix)] in _SPECIES_PREFIXES:
            return True
    return False

# Module-level cache so Qdrant is only queried once per process lifetime.
_vocab_count_cache: int | None = None


async def _get_vocab_count(db) -> int:
    global _vocab_count_cache
    if _vocab_count_cache is None:
        _vocab_count_cache = await db.count_wd14_vocab()
    return _vocab_count_cache


def invalidate_vocab_cache() -> None:
    """Call after a successful import to force re-check on next use."""
    global _vocab_count_cache
    _vocab_count_cache = None


async def _get_library_tag_freq(db) -> dict[str, int]:
    """Scroll WD14 tags from Qdrant and count frequency in user library."""
    freq: dict[str, int] = {}
    offset = None
    try:
        while True:
            from qdrant_client import models as qm
            points, next_offset = await db._qc.scroll(
                collection_name="images",
                limit=500,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["wd14_tags"]),
                with_vectors=False,
            )
            for p in points:
                tags = (p.payload or {}).get("wd14_tags") or []
                for t in tags:
                    freq[t] = freq.get(t, 0) + 1
            if next_offset is None:
                break
            offset = next_offset
    except Exception as e:
        logger.warning("vocab_bank library scan failed: %s", e)
    return freq


async def get_vocab_hints(
    db,
    ollama,
    axis_tags: list[str],
    stranger_count: int = 1,
    lunatic_count: int = 2,
) -> dict[str, list[str]]:
    """Return {"stranger": [...], "lunatic": [...]} using Qdrant semantic search.

    Stranger: tags semantically related to the axis, medium Danbooru frequency.
    Lunatic: tags semantically distant from the axis, high Danbooru frequency,
             absent from the user's personal library.

    Falls back to empty lists if vocab is not imported yet.
    """
    count = await _get_vocab_count(db)
    if count == 0:
        logger.warning(
            "WD14 vocab not imported — run POST /api/admin/invoke/import-wd14-vocab "
            "to enable stranger/lunatic tag hints"
        )
        return {"stranger": [], "lunatic": []}

    axis_set = {t.lower().replace(" ", "_") for t in axis_tags}
    axis_text = " ".join(axis_tags) or "general anime artwork"

    try:
        axis_vec = await ollama.embed(axis_text)
    except Exception as e:
        logger.warning("vocab_bank embed failed: %s", e)
        return {"stranger": [], "lunatic": []}

    lib_freq = await _get_library_tag_freq(db)

    # ── Stranger: semantically RELATED to axis, medium Danbooru frequency ──
    try:
        stranger_hits = await db.search_wd14_vocab(
            axis_vec, min_freq=0.04, max_freq=0.40, category=0, limit=40
        )
        # Filter out axis tags and prefer those in user library (medium presence)
        stranger_pool = [
            h for h in stranger_hits
            if h["name"] not in axis_set and not _is_species_tag(h["name"])
        ]
        # Sort by medium library presence (not too common, not absent)
        def _stranger_score(h):
            lc = lib_freq.get(h["name"], 0)
            return abs(lc - 3)  # prefer count ≈ 3 (medium)
        stranger_pool.sort(key=_stranger_score)
        stranger = [h["name"] for h in stranger_pool[:stranger_count]]
    except Exception as e:
        logger.warning("stranger hint failed: %s", e)
        stranger = []

    # ── Lunatic: high Danbooru frequency, absent from user library, DISTANT from axis ──
    try:
        lunatic_hits = await db.search_wd14_vocab(
            axis_vec, min_freq=0.40, max_freq=1.0, category=0, limit=200
        )
        # Filter: axis exclusion + absent from user library
        lunatic_pool = [
            h for h in lunatic_hits
            if h["name"] not in axis_set
            and lib_freq.get(h["name"], 0) <= 2
            and not _is_species_tag(h["name"])
        ]
        # Low score = semantically distant from axis = most "lunatic"
        lunatic_pool.sort(key=lambda h: h["score"])
        lunatic = [h["name"] for h in lunatic_pool[:lunatic_count]]
    except Exception as e:
        logger.warning("lunatic hint failed: %s", e)
        lunatic = []

    return {"stranger": stranger, "lunatic": lunatic}


# ── Character/scene Danbooru hint patterns ───────────────────────────────────

_EXPRESSION_EXACT = frozenset({
    "smile", "blush", "tears", "pout", "expressionless", "open_mouth", "closed_eyes",
    "winking", "frown", "smirk", "grin", "surprised", "shy", "embarrassed",
    "sad", "angry", "happy", "melancholic", "serious", "neutral",
})
_HAIR_SUFFIXES = ("_hair", "_braid", "_ponytail", "_bun", "_bangs", "twin_tails", "twintails",
                  "short_hair", "long_hair", "medium_hair", "very_long_hair")
_CLOTHING_WORDS = frozenset({
    "dress", "uniform", "skirt", "shirt", "jacket", "hoodie", "outfit", "suit",
    "coat", "blouse", "sweater", "kimono", "yukata", "swimsuit", "bikini",
    "school_uniform", "sailor_uniform", "maid", "cape", "cloak", "apron",
})
_POSE_EXACT = frozenset({
    "standing", "sitting", "lying", "crouching", "kneeling", "jumping",
    "looking_at_viewer", "looking_away", "looking_down", "looking_up",
    "looking_back", "looking_to_the_side", "from_above", "from_below", "from_side",
    "arms_behind_back", "hands_on_hips", "arms_up", "hand_on_own_face",
    "leaning_forward", "walking", "running",
})
_ACCESSORY_EXACT = frozenset({
    "earrings", "glasses", "ribbon", "necklace", "hat", "bag", "bracelet",
    "gloves", "bow", "hair_ribbon", "hair_ornament", "tiara", "crown",
    "scarf", "choker", "ring", "watch",
})
_SCENE_SUFFIXES = (
    "_sky", "_forest", "_mountain", "_city", "_room", "_building", "_garden",
    "_sea", "_ocean", "_lake", "_river", "scenery", "landscape", "outdoors",
    "indoors", "no_humans",
)


def _classify_hint_tag(tag: str) -> str | None:
    """Return category key for a tag, or None if not classifiable."""
    if tag in _EXPRESSION_EXACT or tag.endswith("_smile") or tag.endswith("_eyes"):
        return "expression"
    if any(tag.endswith(s) or tag == s for s in _HAIR_SUFFIXES):
        return "hair"
    # clothing: exact match or ends with a clothing word
    if tag in _CLOTHING_WORDS or any(tag.endswith(f"_{w}") or tag == w for w in _CLOTHING_WORDS):
        return "clothing"
    if tag in _POSE_EXACT or tag.startswith("looking_") or tag.startswith("from_") or tag.startswith("arms_") or tag.startswith("hand_"):
        return "pose"
    if tag in _ACCESSORY_EXACT:
        return "accessories"
    if any(tag.endswith(s) or tag == s for s in _SCENE_SUFFIXES):
        return "scene"
    return None


async def get_character_danbooru_hints(
    db,
    ollama,
    slogan: str,
    person_present: bool,
    max_per_category: int = 4,
) -> dict[str, list[str]]:
    """Search Qdrant WD14 vocab for Danbooru tags relevant to the slogan.

    Returns dict keyed by category (expression/hair/clothing/pose/accessories/scene).
    Falls back to empty dict on any error (Qdrant not imported, embed failure, etc.).
    """
    count = await _get_vocab_count(db)
    if count == 0:
        return {}

    if person_present:
        query = f"{slogan} character expression clothing hairstyle pose accessories"
    else:
        query = f"{slogan} background environment scenery atmosphere detail"

    try:
        vec = await ollama.embed(query)
    except Exception as e:
        logger.debug("get_character_danbooru_hints embed failed: %s", e)
        return {}

    try:
        hits = await db.search_wd14_vocab(vec, min_freq=0.03, max_freq=0.75, category=0, limit=100)
    except Exception as e:
        logger.debug("get_character_danbooru_hints search failed: %s", e)
        return {}

    result: dict[str, list[str]] = {}
    for h in hits:
        tag = h["name"]
        if _is_species_tag(tag):
            continue
        cat = _classify_hint_tag(tag)
        if cat is None:
            continue
        bucket = result.setdefault(cat, [])
        if len(bucket) < max_per_category:
            bucket.append(tag)

    return result


async def get_recent_adopted_tags(db, days: int = 7, limit: int = 200) -> dict[str, int]:
    """Return WD14 tag frequency from images adopted in the last N days."""
    import time
    from qdrant_client import models as qm

    cutoff = time.time() - days * 86400
    freq: dict[str, int] = {}
    offset = None
    try:
        while True:
            points, next_offset = await db._qc.scroll(
                collection_name="images",
                scroll_filter=qm.Filter(must=[
                    qm.FieldCondition(key="genesis.adopted_at_genesis", match=qm.MatchValue(value=True)),
                    qm.FieldCondition(key="mtime", range=qm.Range(gte=cutoff)),
                ]),
                limit=500,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["wd14_tags"]),
                with_vectors=False,
            )
            for p in points:
                tags = (p.payload or {}).get("wd14_tags") or []
                for t in tags[:limit]:
                    freq[t] = freq.get(t, 0) + 1
            if next_offset is None:
                break
            offset = next_offset
    except Exception as e:
        logger.warning("get_recent_adopted_tags failed: %s", e)
    return freq
