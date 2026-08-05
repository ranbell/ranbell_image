"""Character presets: Qdrant-backed registry + deterministic → character mapping.

Presets carry danbooru tags already, so applying one needs no LLM: the mapping
below is a pure function. That matters — the character is the one part of a run
that must stay identical across every image, and an LLM in that path is exactly
what used to make hair and eye colour drift between panels.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from qdrant_client import models as qm

from ..db.qdrant_client import CHARACTER_PRESETS_COLLECTION
from ..tags.body import filter_body_tags
from ..tags.split_tags import (
    enforce_identity_prop_split,
    soft_normalize_tag,
    split_identity_and_outfit,
)

logger = logging.getLogger(__name__)

_ASSET = Path(__file__).resolve().parent / "assets" / "personality_presets.json"
# Stable point ids: the same preset key always maps to the same Qdrant id, so a
# re-seed does not invalidate ids the UI already holds.
_ID_NAMESPACE = uuid.UUID("6f9b8f4e-1c7a-5d2b-9a31-7c0e5b2a4d18")

# Preset tag buckets that describe the body — locked, same in every panel.
# ``body`` is the one bucket that is filtered rather than trusted: see
# ``app.tags.body`` for why an age tag must never reach identity.
_IDENTITY_BUCKETS = (
    "hair_color",
    "hair_style",
    "eyes",
    "body",
    "ears_tails_wings",
)
# Her usual wardrobe. Kept separate so a topic can dress her for the occasion
# (a beach story must not put her in a cardigan and loafers).
_OUTFIT_BUCKETS = ("favorite_clothes", "footwear")
# Worn things → prop layer. The story's throughline prop is the top-level
# ``signature_prop`` field, which takes precedence over these.
_PROP_BUCKETS = ("headwear_accessory",)
# Deliberately NOT identity: these are per-panel performance, and baking them
# into identity_tags would fight the panel's own emotion / gesture.
_EXPRESSION_BUCKET = "expression"
_GESTURE_BUCKET = "hobby_actions"

# The reference board a character carries around: one full-body, one portrait.
# Two is enough to recognise her in a picker and to check that identity tags
# actually render the way they read.
BOARD_SLOTS: tuple[str, ...] = ("sheet", "portrait")

# How many portraits of one character to keep around to choose between. Enough
# to re-roll a few times and still see the one you liked three tries ago.
GALLERY_LIMIT = 12

_seed_cache: list[dict[str, Any]] | None = None


def preset_point_id(preset_key: str) -> str:
    return str(uuid.uuid5(_ID_NAMESPACE, str(preset_key or "")))


def preset_label(preset: dict[str, Any]) -> str:
    """What to call her in a log line.

    Ids are sequential now, and "c014: refused body tags" says nothing about
    which character that was. The descriptive key is kept on the preset for
    exactly this.
    """
    slug = str(preset.get("slug") or "").strip()
    pid = str(preset.get("id") or "?").strip()
    return f"{pid}/{slug}" if slug else pid


def load_seed_presets() -> list[dict[str, Any]]:
    """Read the bundled preset asset once per process."""
    global _seed_cache
    if _seed_cache is None:
        try:
            with _ASSET.open(encoding="utf-8") as fh:
                data = json.load(fh)
            _seed_cache = [p for p in data if isinstance(p, dict) and p.get("id")]
        except Exception as exc:
            logger.warning("[presets] asset load failed: %s", exc)
            _seed_cache = []
    return _seed_cache


def _tags(preset: dict[str, Any], *buckets: str) -> list[str]:
    out: list[str] = []
    tags = preset.get("tags") or {}
    for bucket in buckets:
        values = [str(v) for v in (tags.get(bucket) or [])]
        if bucket == "body":
            values, refused = filter_body_tags(values)
            if refused:
                logger.info(
                    "[presets] %s: refused body tags %s (age or unknown build)",
                    preset_label(preset), ", ".join(refused),
                )
        for raw in values:
            t = soft_normalize_tag(raw)
            if t and t not in out:
                out.append(t)
    return out


def _strings(values: Any) -> list[str]:
    """Flatten to clean strings — presets have historically nested their lists."""
    out: list[str] = []
    for raw in values or []:
        if isinstance(raw, (list, tuple)):
            out.extend(_strings(raw))
            continue
        s = str(raw).strip()
        if s:
            out.append(s)
    return out


def personality_text_from_preset(preset: dict[str, Any], *, locale: str = "ja") -> str:
    """Human-readable brief — also what an optional re-infer would run on."""
    ja = str(locale or "ja").startswith("ja")
    summary = str(
        (preset.get("summary_ja") if ja else "") or preset.get("summary") or "",
    ).strip()
    bits = [summary]
    appearance = preset.get("appearance") or {}
    for key in ("hair", "eyes", "body"):
        val = str(appearance.get(key) or "").strip()
        if val:
            bits.append(f"{key}: {val}")
    inner = _strings(preset.get("inner_ja") if ja else None) or _strings(preset.get("inner"))
    if inner:
        bits.append("inner: " + " / ".join(inner))
    return "\n".join(b for b in bits if b)


def preset_to_character(preset: dict[str, Any]) -> dict[str, Any]:
    """Deterministic preset → character fields (no LLM)."""
    # `subject_tag` is deliberately NOT part of identity. It says how many people
    # are in the picture, which is a fact about the scene, not about her — baked
    # into her identity it insisted there was one girl in frame and made a second
    # character impossible. `app.muse.identity.subject_tags` derives the count
    # from the cast instead; the field is still carried for that.
    subject = soft_normalize_tag(str(preset.get("subject_tag") or ""))
    identity = _tags(preset, *_IDENTITY_BUCKETS)
    # The carried item the story can put through its paces, then worn accessories.
    signature = soft_normalize_tag(str(preset.get("signature_prop") or ""))
    props = ([signature] if signature else []) + _tags(preset, *_PROP_BUCKETS)
    if not signature and props:
        signature = props[0]
    identity, props, signature = enforce_identity_prop_split(
        identity, props, signature_prop=signature,
    )
    # Anything wearable that slipped into identity joins the default wardrobe.
    identity, stray_outfit = split_identity_and_outfit(identity)
    outfit = stray_outfit + [
        t for t in _tags(preset, *_OUTFIT_BUCKETS) if t not in stray_outfit
    ]

    preferences = preset.get("preferences") or {}
    scene = preset.get("default_scene") or {}
    # English is the primary text (the LLM prompts are English); *_ja is what the
    # UI shows.
    personality = {
        "traits": _strings(preset.get("personality")),
        "summary": str(preset.get("summary") or ""),
        "summary_ja": str(preset.get("summary_ja") or preset.get("summary") or ""),
        "inner": _strings(preset.get("inner")),
        "inner_ja": _strings(preset.get("inner_ja")),
        # The gap between how she reads and what she actually is. It is the
        # reason to draw her rather than a generically pretty face, so it goes
        # to the acting seat by name instead of being buried in the summary.
        "charm": str(preset.get("charm") or ""),
        "charm_ja": str(preset.get("charm_ja") or preset.get("charm") or ""),
        "likes": _strings(preferences.get("likes")),
        "dislikes": _strings(preferences.get("dislikes")),
        "appearance": dict(preset.get("appearance") or {}),
        "outfit_style": str(scene.get("outfit_style") or ""),
        "vibe_keywords": _strings(scene.get("vibe_keywords")),
        # The one image that is most her. The reference board builds its centre
        # frame around it, so the sheet shows this person rather than a
        # mannequin in her clothes.
        "signature_moment": str(scene.get("signature_moment") or ""),
        "preset_key": str(preset.get("id") or ""),
        "preset_slug": str(preset.get("slug") or ""),
        "preset_name": str(preset.get("name") or ""),
        "preset_name_ja": str(preset.get("name_ja") or preset.get("name") or ""),
        # What she is known for. The reference board uses it where it needs an
        # occupation, which the roster does not otherwise record.
        "title": str(preset.get("title") or ""),
        "title_ja": str(preset.get("title_ja") or preset.get("title") or ""),
    }
    return {
        "personality": personality,
        "identity_tags": identity,
        # Carried beside identity, not inside it — the cast decides the count.
        "subject_tag": subject,
        "outfit_tags": outfit,
        "prop_tags": props,
        "signature_prop": signature,
        "palette": _strings(preferences.get("favorite_colors")),
        "do_not": [],
        "reasoning_ja": str(preset.get("summary_ja") or preset.get("summary") or ""),
        "expression_vocab": _tags(preset, _EXPRESSION_BUCKET),
        "gesture_vocab": _tags(preset, _GESTURE_BUCKET),
        "source": "preset",
    }


def preset_summary(preset: dict[str, Any], *, point_id: str = "") -> dict[str, Any]:
    """Light row for the picker — the full payload never needs to reach the UI."""
    tags = preset.get("tags") or {}
    board = preset.get("board") or {}
    return {
        "id": point_id or preset_point_id(str(preset.get("id") or "")),
        "preset_key": str(preset.get("id") or ""),
        "slug": str(preset.get("slug") or ""),
        "name": str(preset.get("name") or ""),
        "name_ja": str(preset.get("name_ja") or preset.get("name") or ""),
        "summary": str(preset.get("summary") or ""),
        "summary_ja": str(preset.get("summary_ja") or preset.get("summary") or ""),
        "gender": str(preset.get("gender") or ""),
        "subject_tag": str(preset.get("subject_tag") or ""),
        # All of them, not the first five. The card shows three and the deck
        # shows the rest, but the picker also filters and searches on this, and
        # the cap quietly made two of every character's seven traits
        # unsearchable — enough that only 20 of 30 could be reached by any
        # trait chip at all.
        "traits": _strings(preset.get("personality")),
        "title": str(preset.get("title") or ""),
        "title_ja": str(preset.get("title_ja") or preset.get("title") or ""),
        "charm_ja": str(preset.get("charm_ja") or preset.get("charm") or ""),
        "tag_count": sum(len(v or []) for v in tags.values()),
        # What the picker shows. A bundled preset has none until one is drawn.
        "board": {slot: str(board.get(slot) or "") for slot in BOARD_SLOTS},
        # Everything drawn for her, per slot, so the picker can offer the choice
        # without fetching the whole preset.
        "gallery": normalise_gallery(preset.get("gallery")),
        # What the gallery filters by. Hair and eye colour are the two things a
        # person searching a hundred characters actually has in mind, and they
        # were buried inside `tags` where a list view could not reach them.
        "hair_color": (tags.get("hair_color") or [""])[0],
        "eye_color": (tags.get("eyes") or [""])[0],
        "user_created": bool(preset.get("user_created")),
    }


# ── Qdrant CRUD (mirrors authors/authors.py) ──────────────────────────────────
def _dummy_vector(dim: int) -> list[float]:
    return [0.0] * dim


async def list_presets(db, *, limit: int = 300) -> list[dict[str, Any]]:
    points, _ = await db._qc.scroll(
        collection_name=CHARACTER_PRESETS_COLLECTION,
        limit=limit,
        with_payload=True,
    )
    out = [preset_summary(p.payload or {}, point_id=str(p.id)) for p in points]
    out.sort(key=lambda x: (x.get("name") or ""))
    return out


async def get_preset(db, preset_id: str) -> dict[str, Any] | None:
    points = await db._qc.retrieve(
        collection_name=CHARACTER_PRESETS_COLLECTION,
        ids=[preset_id],
        with_payload=True,
    )
    if not points:
        return None
    return {**(points[0].payload or {}), "_point_id": str(points[0].id)}


async def seed_presets_if_empty(db, *, vector_dim: int) -> int:
    """Insert bundled presets when the collection has no points."""
    existing, _ = await db._qc.scroll(
        collection_name=CHARACTER_PRESETS_COLLECTION,
        limit=1,
        with_payload=False,
    )
    if existing:
        return 0
    return await _insert_seed_presets(db, vector_dim=vector_dim)


async def reset_presets_to_defaults(db, *, vector_dim: int) -> dict[str, int]:
    """Re-read the bundled presets from the asset file.

    This used to drop the collection, which took every character's pictures with
    it — one run of it left all 100 with no portrait, because a preset's images
    are stored on the preset. It no longer deletes anything: the bundled ids are
    `uuid5(namespace, preset_key)` and therefore stable, so the same rows can be
    written over in place, and `_insert_seed_presets` carries the artwork across.

    User-authored characters have random ids, so nothing here can reach them.
    """
    await db.ensure_character_presets_collection()
    inserted = await _insert_seed_presets(db, vector_dim=vector_dim)
    return {"inserted": inserted}


async def create_preset(db, payload: dict[str, Any], *, vector_dim: int) -> dict[str, Any]:
    """Store a new, user-authored character. Returns its summary row."""
    doc = dict(payload)
    doc.setdefault("id", f"user-{uuid.uuid4().hex[:12]}")
    doc["user_created"] = True
    doc["created_at"] = time.time()
    doc.setdefault("board", {})
    point_id = str(uuid.uuid4())
    await db._qc.upsert(
        collection_name=CHARACTER_PRESETS_COLLECTION,
        points=[qm.PointStruct(
            id=point_id,
            vector={"embedding": _dummy_vector(vector_dim)},
            payload=doc,
        )],
    )
    return preset_summary(doc, point_id=point_id)


async def update_preset(db, preset_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    """Merge ``patch`` onto an existing character. Bundled presets are editable
    too — the edit simply makes that point diverge from the shipped asset, and
    a re-seed will not overwrite it because seeding only runs on an empty
    collection."""
    current = await get_preset(db, preset_id)
    if current is None:
        return None
    current.pop("_point_id", None)
    merged = {**current, **{k: v for k, v in patch.items() if v is not None}}
    merged["updated_at"] = time.time()
    await db._qc.set_payload(
        collection_name=CHARACTER_PRESETS_COLLECTION,
        payload=merged,
        points=[preset_id],
    )
    return preset_summary(merged, point_id=preset_id)


async def delete_preset(db, preset_id: str) -> bool:
    if await get_preset(db, preset_id) is None:
        return False
    await db._qc.delete(
        collection_name=CHARACTER_PRESETS_COLLECTION,
        points_selector=qm.PointIdsList(points=[preset_id]),
    )
    return True


def normalise_gallery(gallery: Any) -> dict[str, list[dict[str, Any]]]:
    """``{slot: [{sha, workflow, at}]}`` from whatever is stored.

    The first version of this was a flat list of portrait shas, which could not
    say which checkpoint had drawn one — and drawing the same character on two
    checkpoints to compare them is the whole point of keeping more than one.
    """
    out: dict[str, list[dict[str, Any]]] = {slot: [] for slot in BOARD_SLOTS}
    if isinstance(gallery, list):        # the old shape: portraits, no provenance
        out["portrait"] = [
            {"sha": str(s), "workflow": "", "at": 0.0} for s in gallery if s
        ]
        return out
    for slot in BOARD_SLOTS:
        for row in (gallery or {}).get(slot) or []:
            sha = str((row or {}).get("sha") or row or "")
            if not sha:
                continue
            out[slot].append({
                "sha": sha,
                "workflow": str((row or {}).get("workflow") or "")
                if isinstance(row, dict) else "",
                "at": float((row or {}).get("at") or 0.0)
                if isinstance(row, dict) else 0.0,
            })
    return out


async def attach_board_image(
    db, preset_id: str, slot: str, image_id: str, *, workflow: str = "",
) -> dict | None:
    """Record a rendered reference image against one board slot.

    The newest render becomes the slot, and every image ever drawn for that slot
    is kept as a candidate, with the checkpoint that drew it. Re-rolling is a
    choice rather than a replacement: the fifth attempt is not automatically
    better than the second, and comparing two checkpoints only works if both
    stay around and you can tell them apart.
    """
    if slot not in BOARD_SLOTS:
        return None
    current = await get_preset(db, preset_id)
    if current is None:
        return None
    board = dict(current.get("board") or {})
    board[slot] = image_id
    gallery = normalise_gallery(current.get("gallery"))
    gallery[slot] = _with_candidate(gallery[slot], image_id, workflow)
    await db._qc.set_payload(
        collection_name=CHARACTER_PRESETS_COLLECTION,
        payload={"board": board, "gallery": gallery},
        points=[preset_id],
    )
    return board


def _with_candidate(
    candidates: list[dict[str, Any]], image_id: str, workflow: str,
) -> list[dict[str, Any]]:
    """Newest first, no duplicates, oldest dropped past the limit."""
    kept = [c for c in candidates if c.get("sha") != image_id]
    entry = {"sha": image_id, "workflow": workflow, "at": time.time()}
    return [entry, *kept][:GALLERY_LIMIT]


async def choose_board_image(
    db, preset_id: str, slot: str, image_id: str,
) -> dict | None:
    """Adopt one of her candidates as the image that slot shows."""
    if slot not in BOARD_SLOTS:
        return None
    current = await get_preset(db, preset_id)
    if current is None:
        return None
    gallery = normalise_gallery(current.get("gallery"))
    if image_id not in {c["sha"] for c in gallery[slot]}:
        return None
    board = {**(current.get("board") or {}), slot: image_id}
    await db._qc.set_payload(
        collection_name=CHARACTER_PRESETS_COLLECTION,
        payload={"board": board},
        points=[preset_id],
    )
    return board


async def _existing_artwork(db) -> dict[str, dict[str, Any]]:
    """``{point_id: {board, gallery}}`` for whatever is already stored."""
    out: dict[str, dict[str, Any]] = {}
    offset = None
    try:
        while True:
            points, offset = await db._qc.scroll(
                collection_name=CHARACTER_PRESETS_COLLECTION,
                limit=256, offset=offset, with_payload=True, with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                art = {k: payload[k] for k in ("board", "gallery") if payload.get(k)}
                if art:
                    out[str(point.id)] = art
            if offset is None:
                break
    except Exception as exc:
        # An empty or missing collection is the normal first-run case.
        logger.debug("[presets] no existing artwork to carry over: %s", exc)
    return out


async def _insert_seed_presets(db, *, vector_dim: int) -> int:
    """Write the bundled presets in, keeping whatever has been drawn for them.

    The asset file describes who a character is; it says nothing about her
    pictures, and it never will — those are rendered later and recorded on the
    row. Writing the file over the row therefore has to carry `board` and
    `gallery` across, or re-reading the file destroys every portrait in the app.
    """
    seeds = load_seed_presets()
    if not seeds:
        return 0
    kept = await _existing_artwork(db)
    points = [
        qm.PointStruct(
            id=point_id,
            vector={"embedding": _dummy_vector(vector_dim)},
            payload={**p, **kept.get(point_id, {})},
        )
        for p, point_id in (
            (p, preset_point_id(str(p.get("id") or ""))) for p in seeds
        )
    ]
    for i in range(0, len(points), 100):
        await db._qc.upsert(
            collection_name=CHARACTER_PRESETS_COLLECTION,
            points=points[i:i + 100],
        )
    logger.info("[presets] seeded %d character presets", len(points))
    return len(points)
