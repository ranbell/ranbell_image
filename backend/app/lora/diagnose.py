"""Before training: what a dataset will teach besides what it means to.

A LoRA learns its trigger word from whatever the pictures share and the
captions leave unsaid. That is the whole point of stripping the targets out
of the captions — and the whole danger: a uniform in every picture that no
caption mentions is learned too, and comes back every time the trigger is
used. The trainer cannot see this, and the caption step cannot either,
because each caption is written alone.

The library already knows enough to say it before the card spends an hour
on it: every picture's WD14 tags, every picture's caption, the slots a tag
belongs to (`tags.conflict`). Three readings, no model calls:

- **burn-in**: tags most pictures have and most captions do not say
- **skew**:    one camera distance, one angle, one room, one hour for nearly all
- **duplicates**: pictures that are the same picture (dHash), which weigh twice

All three are advice. Nothing here blocks an export — a tag the Showrunner
wants learned is marked ``absorb_ok`` and shown as intended instead.

`tags.junk.strip_junk` is deliberately not used: it is a prompt filter and
throws away ``watermark``, ``signature`` and ``white_background`` — the
classic burn-ins. Only tags that say nothing about how a picture looks
(rating, head count, gender, negations) are left out.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..tags import conflict
from . import export

logger = logging.getLogger(__name__)

#: A WD14 tag below this confidence is not counted as present.
TAG_MIN_SCORE = 0.35
#: Share of pictures a tag has to be in before it can burn in.
BURN_PRESENT = 0.6
#: Share of those pictures whose caption does not say it.
BURN_UNCAPTIONED = 0.7
#: Below this many captioned, tagged pictures, burn-in is not read at all:
#: with three pictures every tag is "in most of them".
MIN_IMAGES = 4
#: One value taking this share of all pictures in a slot is a skew.
SKEW_SHARE = 0.7
#: dHash bits that may differ for two pictures to count as the same picture.
DUP_DISTANCE = 6
#: Example pictures returned per burn-in tag.
EXAMPLES = 3

#: The slots that say how a set was shot, not who is in it.
SKEW_SLOTS = (
    "camera_distance", "camera_pitch", "camera_side", "gaze_target",
    "posture", "room", "time_of_day",
)

_RATING = frozenset({
    "general", "sensitive", "questionable", "explicit",
    "rating_general", "rating_safe", "rating_sensitive",
    "rating_questionable", "rating_explicit",
})
_WORD_RE = re.compile(r"[a-z0-9]+")


def norm_tag(tag: str) -> str:
    return str(tag or "").strip().lower().replace(" ", "_")


def clean_tags(values: list[str] | None) -> list[str]:
    """Normalised, de-duplicated, order kept — how ``absorb_ok`` is stored."""
    out: list[str] = []
    for v in values or []:
        t = norm_tag(v)
        if t and t not in out:
            out.append(t)
    return out


def ignored(tag: str) -> bool:
    """A tag that says nothing a LoRA could learn by accident."""
    name = norm_tag(tag)
    return (not name or name in _RATING or name.startswith("no_")
            or conflict.count_of(name) is not None or conflict.gender_of(name) is not None)


def image_tags(doc: dict, min_score: float = TAG_MIN_SCORE) -> list[str]:
    """A picture's WD14 tags, most confident first, uninformative ones left out.

    Without scores (an old payload) every stored tag counts."""
    tags = list(doc.get("wd14_tags") or [])
    scores = list(doc.get("wd14_tags_scores") or [])
    if len(scores) == len(tags):
        pairs = sorted(zip(tags, scores), key=lambda p: -float(p[1] or 0))
        tags = [t for t, s in pairs if float(s or 0) >= min_score]
    out: list[str] = []
    for t in tags:
        name = norm_tag(t)
        if not ignored(name) and name not in out:
            out.append(name)
    return out


# ── Does a caption say it? ────────────────────────────────────────────────────

def _words(text: str) -> list[str]:
    return _WORD_RE.findall(str(text or "").lower())


def _forms(word: str) -> frozenset[str]:
    """The word and its plural. Not a stemmer: "uniform" must not match
    "uniformly", which says nothing about clothes."""
    forms = {word, word + "s", word + "es"}
    if word.endswith("y") and len(word) > 2:
        forms.add(word[:-1] + "ies")
    return frozenset(forms)


def mentions(caption: str | list[str], tag: str) -> bool:
    """True when every word of the tag appears in the caption close together.

    ``school_uniform`` is said by "a navy school uniform" and by "her uniform
    for school"; ``black_hair`` is not said by "a black jacket ... her hair".
    Prose and tag-style captions both read this way. A tag with no letters or
    digits at all (``^_^``) counts as said — there is nothing to look for."""
    words = caption if isinstance(caption, list) else _words(caption)
    want = [_forms(w) for w in _words(norm_tag(tag).replace("_", " "))]
    if not want:
        return True
    window = len(want) + 3
    for start in range(len(words)):
        span = words[start:start + window]
        if all(any(w in forms for w in span) for forms in want):
            return True
    return False


# ── The three readings ────────────────────────────────────────────────────────

def burn_in(rows: list[tuple[str, list[str], str]], absorb_ok: list[str] | None = None) -> list[dict]:
    """Tags the trigger is likely to absorb.

    ``rows`` is (sha, tags, caption) for each picture that has both. A tag is
    a candidate when it is in at least BURN_PRESENT of the pictures and the
    caption leaves it out in at least BURN_UNCAPTIONED of those. Candidates
    in ``absorb_ok`` are "intended", the rest "risk"; risks come first, the
    surest first."""
    n = len(rows)
    if n < MIN_IMAGES:
        return []
    ok = set(clean_tags(absorb_ok))
    present: dict[str, list[str]] = {}
    unsaid: dict[str, list[str]] = {}
    for sha, tags, caption in rows:
        words = _words(caption)
        for tag in tags:
            present.setdefault(tag, []).append(sha)
            if not mentions(words, tag):
                unsaid.setdefault(tag, []).append(sha)
    out: list[dict] = []
    for tag, shas in present.items():
        share = len(shas) / n
        missed = unsaid.get(tag, [])
        missed_share = len(missed) / len(shas)
        if share < BURN_PRESENT or missed_share < BURN_UNCAPTIONED:
            continue
        out.append({
            "tag": tag,
            "count": len(shas),
            "present": round(share, 3),
            "uncaptioned": round(missed_share, 3),
            "status": "intended" if tag in ok else "risk",
            "examples": missed[:EXAMPLES],
        })
    out.sort(key=lambda r: (r["status"] != "risk", -r["present"] * r["uncaptioned"], r["tag"]))
    return out


def _slot_value(slot: str, tag: str) -> str:
    """Synonyms of one camera angle count as one value."""
    if slot == "camera_pitch":
        family = conflict.pitch_family(tag)
        if family:
            return min(family)
    return tag


def skew(tag_lists: list[list[str]]) -> list[dict]:
    """For each shooting slot, how the pictures spread over its values.

    Each picture gives a slot at most one value: its most confident tag in
    that slot (``tag_lists`` come most confident first). A picture with no
    tag in the slot is "unknown" and counts against the share, so a slot
    WD14 rarely reads is not called skewed on three pictures."""
    n = len(tag_lists)
    out: list[dict] = []
    for slot in SKEW_SLOTS:
        counts: Counter[str] = Counter()
        for tags in tag_lists:
            value = next((_slot_value(slot, t) for t in tags if conflict.slot_of(t) == slot), None)
            if value is not None:
                counts[value] += 1
        if not counts:
            continue
        top = counts.most_common(1)[0][1]
        share = top / n
        out.append({
            "slot": slot,
            "total": n,
            "unknown": n - sum(counts.values()),
            "values": [{"tag": t, "count": c} for t, c in counts.most_common()],
            "top_share": round(share, 3),
            "skewed": n >= MIN_IMAGES and share >= SKEW_SHARE,
        })
    out.sort(key=lambda r: (not r["skewed"], -r["top_share"], r["slot"]))
    return out


def dhash(path: Path) -> int:
    """64-bit difference hash: each bit says whether a pixel is brighter than
    the one to its right, on a 9×8 grey thumbnail. A resized or recompressed
    copy keeps nearly every bit."""
    from PIL import Image

    with export._open(path) as im:
        im.draft("L", (64, 64))  # JPEG: decode small, the hash needs 9×8
        small = im.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        px = small.tobytes()  # mode "L": one byte per pixel, row by row
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return bits


def near_duplicates(hashes: dict[str, int], max_distance: int = DUP_DISTANCE) -> list[dict]:
    """Pairs of pictures whose hashes differ in at most ``max_distance`` bits."""
    items = list(hashes.items())
    pairs: list[dict] = []
    for i, (a, ha) in enumerate(items):
        for b, hb in items[i + 1:]:
            d = (ha ^ hb).bit_count()
            if d <= max_distance:
                pairs.append({"a": a, "b": b, "distance": d})
    pairs.sort(key=lambda p: (p["distance"], p["a"], p["b"]))
    return pairs


# ── Reading a bucket ──────────────────────────────────────────────────────────

#: sha256 → dHash. A sha names the file's bytes, so an entry never goes stale.
_hash_cache: dict[str, int] = {}
_HASH_CACHE_MAX = 50_000


def _hash_docs(docs: dict[str, dict]) -> tuple[dict[str, int], int]:
    """Hashes for every picture that can be read; the count that could not."""
    out: dict[str, int] = {}
    unreadable = 0
    for sha, doc in docs.items():
        h = _hash_cache.get(sha)
        if h is None:
            src = export._source_path(doc)
            if src is None:
                unreadable += 1
                continue
            try:
                h = dhash(src)
            except Exception as exc:
                logger.debug("dhash failed for %s: %s", sha[:12], exc)
                unreadable += 1
                continue
            if len(_hash_cache) >= _HASH_CACHE_MAX:
                _hash_cache.clear()
            _hash_cache[sha] = h
        out[sha] = h
    return out, unreadable


async def diagnose(db, bucket: dict) -> dict[str, Any]:
    """All three readings for one bucket. Computed on request, never stored:
    captions and tags change under it and a stale diagnosis would mislead."""
    items = bucket.get("items") or []
    shas = [it["sha256"] for it in items]
    docs = {d["sha256"]: d for d in await db.get_by_sha256s(shas)} if shas else {}

    tag_lists: list[list[str]] = []
    rows: list[tuple[str, list[str], str]] = []
    untagged = uncaptioned = 0
    for it in items:
        doc = docs.get(it["sha256"])
        tags = image_tags(doc) if doc else []
        if not tags:
            untagged += 1
            continue
        tag_lists.append(tags)
        caption = (it.get("caption") or "").strip()
        if not caption:
            uncaptioned += 1
            continue
        rows.append((it["sha256"], tags, caption))

    hashes, unreadable = await asyncio.to_thread(_hash_docs, docs)
    return {
        "images": len(items),
        "coverage": {
            "tagged": len(tag_lists),
            "untagged": untagged,
            "uncaptioned": uncaptioned,
            "unreadable": unreadable + (len(shas) - len(docs)),
        },
        "too_few": len(rows) < MIN_IMAGES,
        "burn_in": burn_in(rows, bucket.get("absorb_ok")),
        "skew": skew(tag_lists),
        "duplicates": near_duplicates(hashes),
        "thresholds": {
            "burn_present": BURN_PRESENT, "burn_uncaptioned": BURN_UNCAPTIONED,
            "skew_share": SKEW_SHARE, "min_images": MIN_IMAGES,
        },
    }
