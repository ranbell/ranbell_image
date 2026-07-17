"""Chronicle pose/action tag retrieval: hybrid semantic + lexical ranking.

The story LLM only writes each act's activity as a short ENGLISH sentence;
this module grounds it into real WD14 vocabulary tags. Raw cosine top-k
suffers from hub tags (standing/holding are near everything) and score-band
compression (short tags x sentences cluster in a narrow cosine band), so the
ranker combines:

  adjusted = cosine
           + LEX_BONUS  * lexical_overlap   (verb-weighted stem match)
           - HUB_PEN    * hub_tag           (unless lexically anchored)
           - REUSE_PEN  * used_in_other_act (cross-axis diversity)
           - 1.0        * solo_conflict     (multi-person tag, solo sentence)

with a RELATIVE cutoff (top1 - margin, absolute thresholds are useless in the
compressed band) and MMR de-duplication. Validated against real Ollama
nomic-embed-text embeds on the ~650-tag subset (see tests golden fixtures).

Contract: activity sentences MUST be English (the expand pipeline translates
ja acts first). CJK input returns [] so callers fall back to catalog matching.
"""
from __future__ import annotations

import logging
from typing import Iterable

import numpy as np

from ..tags import catalog

logger = logging.getLogger("uvicorn")

# Query/tag texts share one framing prefix for embedding symmetry.
EMBED_PREFIX = "pose action: "

LEX_BONUS = 0.12
HUB_PEN = 0.10
REUSE_PEN = 0.08
MARGIN = 0.10
MMR_LAMBDA = 0.7
POOL_SIZE = 40

# Hub tags: semantically near everything; demoted unless lexically anchored.
HUB_TAGS = frozenset({
    "standing", "sitting", "kneeling", "lying", "crouching", "squatting",
    "holding", "walking", "posing", "arms_at_sides", "own_hands_together",
})

_STOP = frozenset({
    "the", "a", "an", "at", "in", "on", "of", "to", "with", "her", "his",
    "she", "he", "into", "under", "over", "and", "for", "from", "by", "out",
    "up", "onto", "against", "while", "as", "is", "are", "was", "their",
})
_TAG_STOP = frozenset({"at", "on", "in", "of", "to", "from", "with", "the", "a", "an"})
# Verbs that accompany almost any object interaction — neutral, never a
# contradicting action.
_NEUTRAL_VERBS = frozenset({"holding", "carrying"})

# Multi-person tags: hard-drop when the sentence has no second person.
_PERSON_PARTS = frozenset({"person", "another", "another's", "couple"})
_PERSON_STEMS = frozenset({
    "person", "friend", "together", "couple", "two", "peopl", "partner",
    "boy", "girl", "mother", "father", "sister", "brother", "child",
})


def _stem(w: str) -> str:
    """Cheap suffix stemmer good enough for tag-part vs sentence matching."""
    w = w.lower().removesuffix("'s")
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
        if len(w) > 2 and w[-1] == w[-2]:  # running -> runn -> run
            w = w[:-1]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
        if len(w) > 2 and w[-1] == w[-2]:
            w = w[:-1]
    elif len(w) > 3 and w.endswith("es"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s"):
        w = w[:-1]
    return w


# Story verbs that have no direct tag form → the tag-vocabulary verb family.
_SYNONYM_STEMS: dict[str, tuple[str, ...]] = {
    "spot": ("look",), "see": ("look",), "watch": ("look",),
    "gaze": ("look",), "glanc": ("look",), "notic": ("look",),
    "star": ("look",),  # staring
    "grip": ("hold",), "clutch": ("hold",), "carr": ("hold",),
    "sprint": ("run",), "dash": ("run",), "stroll": ("walk",),
}


def sentence_stems(s: str) -> set[str]:
    words = [w.strip(".,!?;:()\"'") for w in (s or "").lower().split()]
    stems = {_stem(w) for w in words if w and w not in _STOP and len(w) > 2}
    for st in list(stems):
        stems.update(_SYNONYM_STEMS.get(st, ()))
    return stems


def lexical_overlap(stems: set[str], tag: str) -> float:
    """Verb-weighted lexical anchor.

    Matching the action ('-ing') part of a tag is the strong signal, scaled by
    how much of the REST of the tag also matches — 'sleeping' scores 1.0 for a
    sleeping sentence while 'sleeping_on_person' only 0.5. A tag whose verb
    does NOT match asserts a different action -> 0 (kills 'table_humping' for
    'pours tea at the table'). Noun-only matches are weak (0.35 scale).
    """
    parts = [p for p in tag.replace("-", "_").split("_") if p and p not in _TAG_STOP]
    if not parts:
        return 0.0
    verbs = [p for p in parts if p.endswith("ing") and p not in _NEUTRAL_VERBS]
    others = [p for p in parts if p not in verbs and p not in _NEUTRAL_VERBS]
    verb_hit = any(_stem(p) in stems for p in verbs)
    other_hits = sum(1 for p in others if _stem(p) in stems)
    if verb_hit:
        return (1 + other_hits) / (1 + len(others))
    if verbs:
        return 0.0  # tag asserts a DIFFERENT action than the sentence
    if others:
        return 0.35 * other_hits / len(others)
    return 0.0


def strong_anchor(stems: set[str], tag: str) -> bool:
    """True when the tag is trustworthily anchored in the sentence: one of its
    OBJECT parts is confirmed ('holding_cup' for a tea sentence), or it is a
    pure verb tag whose verb matches ('pouring'). A verb match with an
    unconfirmed object ('looking_at_animal' for 'spots her friend') is NOT
    strong — those are exactly the junk class."""
    parts = [p for p in tag.replace("-", "_").split("_") if p and p not in _TAG_STOP]
    verbs = [p for p in parts if p.endswith("ing") and p not in _NEUTRAL_VERBS]
    others = [p for p in parts if p not in verbs and p not in _NEUTRAL_VERBS]
    if any(_stem(p) in stems for p in others):
        return True
    return bool(verbs) and not others and any(_stem(p) in stems for p in verbs)


def solo_conflict(stems: set[str], tag: str) -> bool:
    """True for multi-person tags when the sentence has no second person."""
    parts = set(tag.replace("-", "_").split("_"))
    if not (parts & _PERSON_PARTS):
        return False
    return not (stems & _PERSON_STEMS)


# Body-part / sexual object words: such a tag is only relevant when the
# sentence itself names the part — otherwise a verb match alone must never
# promote it ("spots her friend" → looking_at_breasts).
_EXPLICIT_PARTS = frozenset({
    "breast", "breasts", "nipple", "nipples", "crotch", "groin", "penis",
    "pussy", "ass", "butt", "buttocks", "panties", "thighs", "sex",
    "paizuri", "fellatio", "cunnilingus", "masturbation", "humping",
    "grinding", "grope", "groping", "peeing", "cum",
})


def content_conflict(stems: set[str], tag: str) -> bool:
    """True when a tag names a body part / sexual act the sentence doesn't."""
    parts = set(tag.replace("-", "_").replace("'s", "").split("_"))
    hits = parts & _EXPLICIT_PARTS
    if not hits:
        return False
    return not any(_stem(p) in stems for p in hits)


def _is_mostly_ascii(s: str) -> bool:
    if not s:
        return False
    non_ascii = sum(1 for ch in s if ord(ch) > 0x2FFF)
    return non_ascii <= len(s) * 0.2


def rank_pose_tags(
    tags: list[str],
    vecs: np.ndarray,
    query_vec: np.ndarray,
    sentence: str,
    *,
    k: int = 8,
    used_tags: Iterable[str] = (),
) -> list[str]:
    """Pure hybrid ranker over pre-embedded vocabulary (unit-testable core)."""
    if not tags:
        return []
    stems = sentence_stems(sentence)
    used = {str(t).strip().lower().replace(" ", "_") for t in used_tags}

    cos = vecs @ query_vec
    lex = np.array([lexical_overlap(stems, t) for t in tags])
    hub = np.array([1.0 if t in HUB_TAGS else 0.0 for t in tags])
    reuse = np.array([1.0 if t in used else 0.0 for t in tags])
    solo = np.array([
        1.0 if (solo_conflict(stems, t) or content_conflict(stems, t)) else 0.0
        for t in tags
    ])
    adj = cos + LEX_BONUS * lex - HUB_PEN * hub * (lex == 0) - REUSE_PEN * reuse - solo

    strong = np.array([1.0 if strong_anchor(stems, t) else 0.0 for t in tags])

    order = np.argsort(-adj)[:POOL_SIZE]
    # Per-tag cutoff: only STRONGLY anchored tags (object confirmed in the
    # sentence, or a matching pure-verb tag) earn the full margin; weakly/
    # un-anchored tags must sit very close to top1 — this stops the junk tail
    # ("looking_at_animal", "shared_bathing") that cosine noise otherwise
    # pushes into k for object-less sentences.
    if not any(strong[int(i)] > 0 for i in order):
        k = min(k, 3)
    top1 = adj[order[0]]
    pool = [
        int(i) for i in order
        if adj[i] >= top1 - (MARGIN if strong[i] > 0 else MARGIN / 2)
    ]

    # MMR greedy: keep relevance, drop near-synonyms (running/jogging/...).
    selected: list[int] = []
    while pool and len(selected) < k:
        best, best_score = pool[0], -1e9
        for i in pool:
            div = max((float(vecs[i] @ vecs[j]) for j in selected), default=0.0)
            s = MMR_LAMBDA * float(adj[i]) - (1 - MMR_LAMBDA) * div
            if s > best_score:
                best, best_score = i, s
        selected.append(best)
        pool.remove(best)

    # Anchor guarantee: at least one lexically matched tag when any exists.
    if selected and not any(lex[i] > 0 for i in selected):
        lex_candidates = [int(i) for i in order if lex[i] > 0]
        if lex_candidates:
            selected[-1] = lex_candidates[0]

    return [tags[i] for i in selected]


def fallback_pose_tags(sentence: str, *, k: int = 6) -> list[str]:
    """No-vocab fallback: stem-match the sentence against the catalog POSE
    axis and action keywords. Coarse, but always available offline."""
    stems = sentence_stems(sentence)
    out: list[str] = []
    for tag in sorted(catalog.POSE):
        if lexical_overlap(stems, tag) >= 0.5:
            out.append(tag)
            if len(out) >= k:
                return out
    for kw in catalog.ACTION_KEYWORDS:
        t = kw.rstrip("_")
        if _stem(t) in stems and t not in out:
            out.append(t)
            if len(out) >= k:
                break
    return out


# Query/tag framing prefix for the scene vocabulary (see EMBED_PREFIX).
SCENE_EMBED_PREFIX = "scene location: "

# ── vocab caches (module-level; ≤800 x 768 float32 ≈ 2 MB each) ──────────────
_vocab_cache: dict[str, tuple[list[str], np.ndarray]] = {}


def invalidate_pose_vocab_cache() -> None:
    _vocab_cache.clear()


async def _load_vocab(db, kind: str) -> tuple[list[str], np.ndarray]:
    """Scroll a vocab collection once and cache unit vectors per kind."""
    cached = _vocab_cache.get(kind)
    if cached is not None:
        return cached
    scroll = (
        db.scroll_scene_vocab_all if kind == "scene" else db.scroll_pose_vocab_all
    )
    rows = await scroll()
    if not rows:
        return [], np.zeros((0, 1), dtype=np.float32)
    tags = [name for name, _ in rows]
    vecs = np.array([vec for _, vec in rows], dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs = vecs / norms
    _vocab_cache[kind] = (tags, vecs)
    return _vocab_cache[kind]


async def load_pose_vocab(db) -> tuple[list[str], np.ndarray]:
    return await _load_vocab(db, "pose")


async def retrieve_pose_tags(
    ollama,
    db,
    activity_sentence: str,
    *,
    k: int = 8,
    used_tags: Iterable[str] = (),
) -> list[str]:
    """Ground an English activity sentence into real WD14 pose/action tags.

    Falls back to catalog stem-matching when the vocab collection is empty,
    the embed call fails, or the sentence is not English.
    """
    sentence = (activity_sentence or "").strip()
    if not sentence:
        return []
    if not _is_mostly_ascii(sentence):
        logger.warning("[pose_retrieval] non-English sentence, using fallback: %.40s", sentence)
        return fallback_pose_tags(sentence, k=k)
    try:
        tags, vecs = await load_pose_vocab(db)
        if not tags:
            logger.warning("[pose_retrieval] pose vocab empty — run import-pose-vocab")
            return fallback_pose_tags(sentence, k=k)
        qvec = np.array(await ollama.embed(EMBED_PREFIX + sentence), dtype=np.float32)
        n = float(np.linalg.norm(qvec))
        if n == 0:
            return fallback_pose_tags(sentence, k=k)
        qvec /= n
        result = rank_pose_tags(tags, vecs, qvec, sentence, k=k, used_tags=used_tags)
        return result or fallback_pose_tags(sentence, k=k)
    except Exception as e:
        logger.warning("[pose_retrieval] retrieval failed (%s), using fallback", e)
        return fallback_pose_tags(sentence, k=k)


def fallback_scene_tags(place_text: str, *, k: int = 5) -> list[str]:
    """No-vocab fallback: stem-match the place words against the catalog
    scene axes (location / time_weather / visual)."""
    stems = sentence_stems(place_text)
    out: list[str] = []
    pool = sorted(catalog.BACKGROUND | catalog.ENVIRONMENT)
    for tag in pool:
        parts = [p for p in tag.replace("-", "_").split("_") if p]
        if any(_stem(p) in stems for p in parts):
            out.append(tag)
            if len(out) >= k:
                break
    return out


async def retrieve_scene_tags(
    ollama,
    db,
    place_text: str,
    *,
    k: int = 5,
) -> list[str]:
    """Ground an act's structured `place` string into real scene tags
    (location / time-of-day / weather) via the same hybrid ranker.

    Measured: "classroom, afternoon light"→classroom, "street near the park,
    dusk"→park/dusk/street, "train station platform in the rain"→rain/
    train_station/train — the tiny curated subset removes the junk the full
    wd14_vocab cosine search produced (witch_hat/maid for a classroom)."""
    text = (place_text or "").strip()
    if not text:
        return []
    if not _is_mostly_ascii(text):
        return fallback_scene_tags(text, k=k)
    try:
        tags, vecs = await _load_vocab(db, "scene")
        if not tags:
            logger.warning("[pose_retrieval] scene vocab empty — run import-pose-vocab")
            return fallback_scene_tags(text, k=k)
        qvec = np.array(
            await ollama.embed(SCENE_EMBED_PREFIX + text), dtype=np.float32
        )
        n = float(np.linalg.norm(qvec))
        if n == 0:
            return fallback_scene_tags(text, k=k)
        qvec /= n
        result = rank_pose_tags(tags, vecs, qvec, text, k=k)
        return result or fallback_scene_tags(text, k=k)
    except Exception as e:
        logger.warning("[pose_retrieval] scene retrieval failed (%s), using fallback", e)
        return fallback_scene_tags(text, k=k)
