"""Character presets: Qdrant-backed registry + deterministic → character mapping.

Presets carry danbooru tags already, so applying one needs no LLM: the mapping
below is a pure function. That matters — the character is the one part of a run
that must stay identical across every image, and an LLM in that path is exactly
what used to make hair and eye colour drift between panels.
"""
from __future__ import annotations

import asyncio
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

# Duet wrap jobs can append seeds to both partners at once — serialize RMW.
_social_seeds_lock = asyncio.Lock()

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

# Runtime fields that live on the Qdrant point but never in the asset file.
# Sync / re-seed must carry these across or diaries and boards are destroyed.
_RUNTIME_KEYS: tuple[str, ...] = (
    "board",
    "gallery",
    "diaries",
    "chemistry",
    "social_seeds",
    "user_created",
    "created_at",
    "updated_at",
)

# Short-lived lounge whispers — trend tips and friend feedback for the next few shoots.
MAX_SOCIAL_SEEDS = 5


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


def reload_seed_presets() -> list[dict[str, Any]]:
    """Drop the in-process cache and re-read the asset (tests / hot reload)."""
    global _seed_cache
    _seed_cache = None
    return load_seed_presets()


def preset_version(payload: dict[str, Any] | None) -> int:
    """Asset / stored Muse schema version. Missing means older than any shipped row."""
    if not payload:
        return -1
    try:
        return int(payload.get("version"))
    except (TypeError, ValueError):
        return -1


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
        # 成人であること、いまいる場所、そして**学生時代の記憶**と**将来の夢**。
        # 過去は消さずに本人の記憶へ移してある —— 消すと人格が薄くなる。
        "age": int(preset.get("age") or 0) or None,
        "occupation": str(preset.get("occupation") or ""),
        "occupation_ja": str(
            preset.get("occupation_ja") or preset.get("occupation") or ""
        ),
        "student_past": str(preset.get("student_past") or ""),
        "student_past_ja": str(
            preset.get("student_past_ja") or preset.get("student_past") or ""
        ),
        "dream": str(preset.get("dream") or ""),
        "dream_ja": str(preset.get("dream_ja") or preset.get("dream") or ""),
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
        # Direct dialogue & duet personality traits for natural conversation.
        "first_person_ja": str(preset.get("first_person_ja") or "私"),
        "user_address_ja": str(preset.get("user_address_ja") or "総監督"),
        "duet_say_examples": _strings(preset.get("duet_say_examples")),
        "talk_quirks": str(preset.get("talk_quirks") or ""),
        "first_person_en": str(preset.get("first_person_en") or "I"),
        "user_address_en": str(preset.get("user_address_en") or "Showrunner"),
        "duet_say_examples_en": _strings(preset.get("duet_say_examples_en")),
        "talk_quirks_en": str(preset.get("talk_quirks_en") or ""),
    }
    return {
        "personality": personality,
        "identity_tags": identity,
        # Carried beside identity, not inside it — the cast decides the count.
        "subject_tag": subject,
        "outfit_tags": outfit,
        "prop_tags": props,
        "signature_prop": signature,
        "name": str(preset.get("name") or ""),
        "name_ja": str(preset.get("name_ja") or preset.get("name") or ""),
        "first_person_ja": str(preset.get("first_person_ja") or "私"),
        "user_address_ja": str(preset.get("user_address_ja") or "総監督"),
        "duet_say_examples": _strings(preset.get("duet_say_examples")),
        "talk_quirks": str(preset.get("talk_quirks") or ""),
        "first_person_en": str(preset.get("first_person_en") or "I"),
        "user_address_en": str(preset.get("user_address_en") or "Showrunner"),
        "duet_say_examples_en": _strings(preset.get("duet_say_examples_en")),
        "talk_quirks_en": str(preset.get("talk_quirks_en") or ""),
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
        # **全員が成人。** 学生設定のままだと、未成年と取られる余地が構造的に
        # 残る。年齢は数字で持つ —— 職業の書きぶりだけだと曖昧さが残るため。
        "age": int(preset.get("age") or 0) or None,
        "occupation": str(preset.get("occupation") or ""),
        "occupation_ja": str(
            preset.get("occupation_ja") or preset.get("occupation") or ""
        ),
        "subject_tag": str(preset.get("subject_tag") or ""),
        # Flavour chips on the card — not a filter (traits never cover the roster).
        "traits": _strings(preset.get("personality")),
        "title": str(preset.get("title") or ""),
        "title_ja": str(preset.get("title_ja") or preset.get("title") or ""),
        "charm": str(preset.get("charm") or ""),
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
        # Free off the payload the gallery already loads — an unread badge per
        # card used to mean one `/diaries` fetch per character just to count.
        "diary_unread_count": sum(
            1 for d in (preset.get("diaries") or []) if not d.get("read")
        ),
        # Lifetime co-shoots (recaps keep only the last few). Fallback to the
        # sticky window so older rows still show a badge before the next shoot.
        "shoot_count": _shoot_count(preset),
        "last_shoot_at": _last_shoot_at(preset),
    }


# ── Qdrant CRUD ────────────────────────────────────────────────────────────
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
    inserted, _ = await _insert_seed_presets(db, vector_dim=vector_dim, stored={})
    return inserted


def seed_point_ids() -> set[str]:
    """The rows the bundled asset currently claims."""
    return {preset_point_id(str(p.get("id") or "")) for p in load_seed_presets()}


def plan_reset(
    stored: dict[str, dict[str, Any]], *, wipe: bool = False,
) -> dict[str, Any]:
    """What a reset would do to ``{point_id: payload}``. Pure, so it is testable
    and so the UI can show the numbers before anything is destroyed.

    A row is the user's if it says so. Everything else came from a version of
    the asset file, and the file is a *set*, not an accumulation: a row that the
    file no longer claims is a character who no longer exists.
    """
    seed_ids = seed_point_ids()
    mine = {pid: p for pid, p in stored.items() if not p.get("user_created")}
    refreshed = sorted(seed_ids & set(mine))
    stale = sorted(set(mine) - seed_ids)
    users = sorted(set(stored) - set(mine))
    doomed = stale + (users if wipe else [])
    return {
        "seeds": len(seed_ids),
        # Bundled rows already present that are written over in place.
        "refreshed": len(refreshed),
        # Bundled rows from an older asset that nothing claims any more.
        "stale": stale,
        # User-authored rows: left alone unless `wipe`.
        "kept": 0 if wipe else len(users),
        "removed": doomed,
        # Reference images belonging to rows about to go. They stay in the
        # gallery — this is here so a removal never happens silently.
        "orphan_images": sorted({
            sha
            for pid in doomed
            for sha in _artwork_shas(stored.get(pid) or {})
        }),
        "labels": [preset_label(stored[pid]) for pid in doomed][:60],
    }


async def reset_presets_to_defaults(
    db, *, vector_dim: int, wipe: bool = False, dry_run: bool = False,
) -> dict[str, Any]:
    """Make the collection say what the asset file says.

    Three rules, in the order they were learned:

    1. Do not drop the collection. That took every character's pictures with it
       — one run left all 100 with no portrait, because a preset's images are
       stored on the preset. Bundled ids are `uuid5(namespace, preset_key)` and
       therefore stable, so rows are written over in place instead.
    2. Do delete what the file has stopped claiming. Writing over in place alone
       is not a reset: renumbering the roster (`darkroom_photo` → `c007`) moved
       every point, so the previous hundred characters stayed in the collection
       for good — no id the UI knew, no delete button, and a re-seed that could
       only ever add to them.
    3. Carry the artwork by name as well as by id, so rule 2 does not become the
       next version of the problem the first time a character is renumbered.

    ``wipe`` extends the deletion to user-authored characters — the only way to
    get an empty roster back. ``dry_run`` reports the same numbers and changes
    nothing.
    """
    await db.ensure_character_presets_collection()
    stored = await _stored_rows(db)
    plan = plan_reset(stored, wipe=wipe)

    if dry_run:
        return {"dry_run": True, "inserted": 0, **_reset_report(plan, carried=0)}

    await _delete_points(db, plan["removed"])
    # `stored` is the pre-deletion snapshot on purpose: a row on its way out is
    # exactly where a renumbered character's pictures are, and it is the last
    # moment they can be claimed.
    inserted, carried = await _insert_seed_presets(
        db, vector_dim=vector_dim, stored=stored,
    )
    result = {"dry_run": False, "inserted": inserted,
              **_reset_report(plan, carried=carried)}
    logger.info(
        "[presets] reset: %d written, %d removed (%s), %d user rows kept, "
        "%d boards carried across, %d images now unreferenced",
        inserted, len(plan["removed"]), ", ".join(plan["labels"][:10]) or "-",
        plan["kept"], carried, len(plan["orphan_images"]),
    )
    return result


def _reset_report(plan: dict[str, Any], *, carried: int) -> dict[str, Any]:
    """The plan, sized down to what an API response and a toast can use."""
    return {
        "seeds": plan["seeds"],
        "refreshed": plan["refreshed"],
        "removed": len(plan["removed"]),
        "kept": plan["kept"],
        "carried_over": carried,
        "orphan_images": len(plan["orphan_images"]),
        "removed_labels": plan["labels"],
    }


async def _delete_points(db, point_ids: list[str]) -> None:
    for i in range(0, len(point_ids), 100):
        await db._qc.delete(
            collection_name=CHARACTER_PRESETS_COLLECTION,
            points_selector=qm.PointIdsList(points=point_ids[i:i + 100]),
        )


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


async def _stored_rows(db) -> dict[str, dict[str, Any]]:
    """``{point_id: payload}`` for everything already in the collection."""
    out: dict[str, dict[str, Any]] = {}
    offset = None
    try:
        while True:
            points, offset = await db._qc.scroll(
                collection_name=CHARACTER_PRESETS_COLLECTION,
                limit=256, offset=offset, with_payload=True, with_vectors=False,
            )
            for point in points:
                out[str(point.id)] = point.payload or {}
            if offset is None:
                break
    except Exception as exc:
        # An empty or missing collection is the normal first-run case.
        logger.debug("[presets] nothing stored yet: %s", exc)
    return out


def _artwork(payload: dict[str, Any]) -> dict[str, Any]:
    """Board / gallery only — used when matching artwork across renumbered ids."""
    return {k: payload[k] for k in ("board", "gallery") if payload.get(k)}


def _runtime_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Everything the asset file must not overwrite (diaries, boards, …)."""
    if not payload:
        return {}
    return {k: payload[k] for k in _RUNTIME_KEYS if k in payload and payload[k] is not None}


def _artwork_shas(payload: dict[str, Any]) -> list[str]:
    board = payload.get("board") or {}
    shas = [str(v) for v in board.values() if v]
    for candidates in normalise_gallery(payload.get("gallery")).values():
        shas += [c["sha"] for c in candidates]
    return shas


def _identity_keys(payload: dict[str, Any]) -> set[str]:
    """How to recognise the same character in a row whose point id has moved.

    Her id was descriptive once (`darkroom_photo`) and is `c007` now, and the id
    is the seed for her point — so renumbering the roster orphans everything
    drawn for her. The slug is the name that survives a renumbering, and a
    legacy row's `id` *was* that slug, so both are worth matching on.
    """
    return {str(payload.get(k) or "").strip() for k in ("slug", "id")} - {""}


def _artwork_by_name(stored: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for payload in stored.values():
        art = _artwork(payload)
        if art:
            for key in _identity_keys(payload):
                out.setdefault(key, art)
    return out


async def _insert_seed_presets(
    db, *, vector_dim: int, stored: dict[str, dict[str, Any]] | None = None,
) -> tuple[int, int]:
    """Write the bundled presets in, keeping runtime fields on the row.

    Returns ``(written, carried_from_another_row)``.

    The asset file describes who a character is; it says nothing about her
    pictures or diaries. Writing the file over the row therefore has to carry
    `board`, `gallery`, and `diaries` across, or re-reading the file destroys
    every portrait and every letter in the app. Her own row is the first place
    to look; her slug is the second, which is what makes a renumbered roster
    survive.
    """
    seeds = load_seed_presets()
    if not seeds:
        return 0, 0
    if stored is None:
        stored = await _stored_rows(db)
    by_name = _artwork_by_name(stored)

    points: list[qm.PointStruct] = []
    carried = 0
    for seed in seeds:
        point_id = preset_point_id(str(seed.get("id") or ""))
        existing = stored.get(point_id) or {}
        runtime = _runtime_fields(existing)
        art = _artwork(existing)
        if not art:
            for key in (str(seed.get("slug") or ""), str(seed.get("id") or "")):
                art = by_name.get(key) or {}
                if art:
                    carried += 1
                    runtime = {**runtime, **art}
                    logger.info(
                        "[presets] %s: carried the board over from %s",
                        preset_label(seed), key,
                    )
                    break
        else:
            runtime = {**runtime, **art}
        # Never let the asset invent empty diaries over a full book.
        payload = {**seed, **runtime}
        if "created_at" not in payload:
            payload["created_at"] = time.time()
        payload["updated_at"] = time.time()
        points.append(qm.PointStruct(
            id=point_id,
            vector={"embedding": _dummy_vector(vector_dim)},
            payload=payload,
        ))

    for i in range(0, len(points), 100):
        await db._qc.upsert(
            collection_name=CHARACTER_PRESETS_COLLECTION,
            points=points[i:i + 100],
        )
    logger.info("[presets] seeded %d character presets", len(points))
    return len(points), carried


async def sync_muse_presets_from_asset(
    db, *, vector_dim: int, dry_run: bool = False,
) -> dict[str, Any]:
    """Apply bundled JSON rows whose ``version`` is newer than Qdrant.

    Only asset fields are rewritten. ``diaries``, ``board``, ``gallery``, and
    other runtime keys are kept verbatim. Rows the file does not claim are not
    deleted (that remains ``reset``). User-authored characters are skipped.
    """
    await db.ensure_character_presets_collection()
    # Always re-read the file so an edited asset is visible without restart.
    seeds = reload_seed_presets()
    stored = await _stored_rows(db)

    to_insert: list[dict[str, Any]] = []
    to_update: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    skipped: list[dict[str, str]] = []

    for seed in seeds:
        preset_key = str(seed.get("id") or "")
        point_id = preset_point_id(preset_key)
        existing = stored.get(point_id)
        seed_ver = preset_version(seed)

        if existing and existing.get("user_created"):
            skipped.append({
                "id": preset_key,
                "reason": "user_created",
                "label": preset_label(existing),
            })
            continue

        if existing is None:
            to_insert.append(seed)
            continue

        stored_ver = preset_version(existing)
        if seed_ver > stored_ver:
            to_update.append((point_id, seed, existing))
        else:
            skipped.append({
                "id": preset_key,
                "reason": "up_to_date",
                "label": preset_label(existing),
                "stored_version": stored_ver,
                "asset_version": seed_ver,
            })

    report = {
        "dry_run": dry_run,
        "seeds": len(seeds),
        "inserted": len(to_insert),
        "updated": len(to_update),
        "skipped": len(skipped),
        "inserted_ids": [str(s.get("id") or "") for s in to_insert],
        "updated_ids": [str(s.get("id") or "") for _, s, _ in to_update],
        "skipped_detail": skipped[:60],
    }
    if dry_run:
        return report

    points: list[qm.PointStruct] = []
    now = time.time()
    for seed in to_insert:
        point_id = preset_point_id(str(seed.get("id") or ""))
        points.append(qm.PointStruct(
            id=point_id,
            vector={"embedding": _dummy_vector(vector_dim)},
            payload={**seed, "created_at": now, "updated_at": now},
        ))
    for point_id, seed, existing in to_update:
        runtime = _runtime_fields(existing)
        payload = {**seed, **runtime, "updated_at": now}
        if "created_at" not in payload:
            payload["created_at"] = existing.get("created_at") or now
        points.append(qm.PointStruct(
            id=point_id,
            vector={"embedding": _dummy_vector(vector_dim)},
            payload=payload,
        ))

    for i in range(0, len(points), 100):
        await db._qc.upsert(
            collection_name=CHARACTER_PRESETS_COLLECTION,
            points=points[i:i + 100],
        )
    logger.info(
        "[presets] muse sync: %d inserted, %d updated, %d skipped",
        report["inserted"], report["updated"], report["skipped"],
    )
    return report


# ── Preset Diaries (Qdrant payload) ──────────────────────────────────────────
async def get_preset_diaries(db, preset_id: str) -> list[dict[str, Any]]:
    """Retrieve all diary entries for a given preset_id."""
    preset = await get_preset(db, preset_id)
    if not preset:
        return []
    return list(preset.get("diaries") or [])


# Her diary lives on the character's own Qdrant payload, so it grows every time
# a session is wrapped and nothing ever removed one. Far more than this is not a
# memory, it is a payload.
MAX_DIARIES = 50


def _diary_image_ids(diary: dict[str, Any]) -> list[str]:
    """Every photo attached to a diary page, oldest entries included.

    New entries store `image_ids`. Older ones only have `image_id`.
    """
    ids = [str(x).strip() for x in (diary.get("image_ids") or []) if str(x).strip()]
    single = str(diary.get("image_id") or "").strip()
    if single and single not in ids:
        ids.append(single)
    return ids


async def find_preset_diary_by_image(db, preset_id: str, image_id: str) -> dict[str, Any] | None:
    """Reverse lookup for the Creation Record panel: did this shot get written up?"""
    if not image_id:
        return None
    diaries = await get_preset_diaries(db, preset_id)
    return next((d for d in diaries if image_id in _diary_image_ids(d)), None)


async def add_preset_diary(db, preset_id: str, diary: dict[str, Any]) -> dict[str, Any] | None:
    """Append a new diary entry to a character preset, oldest dropped past the cap."""
    preset = await get_preset(db, preset_id)
    if not preset:
        return None
    diaries = list(preset.get("diaries") or [])
    # Defense in depth against a race in the caller (two `finish_session`
    # calls both queuing a diary job for the same shoot): never file two
    # entries for the same session + character.
    session_id = diary.get("session_id")
    character_id = diary.get("character_id")
    if session_id and character_id:
        existing = next(
            (
                d for d in diaries
                if d.get("session_id") == session_id
                and d.get("character_id") == character_id
            ),
            None,
        )
        if existing is not None:
            return existing
    diaries.append(diary)
    if len(diaries) > MAX_DIARIES:
        diaries.sort(key=lambda d: d.get("timestamp") or 0.0)
        diaries = diaries[-MAX_DIARIES:]
    await update_preset(db, preset_id, {"diaries": diaries})
    return diary


async def backfill_diary_photos(db, *, limit: int = 24) -> dict[str, Any]:
    """Give the pages already written back the photos they never got.

    Every diary filed before `shoots` existed recorded only the take the
    showrunner happened to finish on — one measured session shot eight final
    photos over four ③ presses and its page carries two. The photos were never
    lost; each one stores its own `muse_session_id`, so the shoot can be asked
    of the images and the page repaired in place.

    Read-only for any page it cannot improve: a diary keeps the ids it has, and
    gains only what the image store can prove belongs to the same shoot.
    """
    scanned = 0
    repaired = 0
    added = 0
    # `list_presets` returns light rows for the picker — the diaries are only on
    # the full payload, so each one has to be fetched.
    for row in await list_presets(db):
        pid = str(row.get("id") or "")
        preset = await get_preset(db, pid) if pid else None
        diaries = list((preset or {}).get("diaries") or [])
        if not pid or not diaries:
            continue
        changed = False
        for diary in diaries:
            sid = str(diary.get("session_id") or "")
            if not sid:
                continue
            scanned += 1
            have = _diary_image_ids(diary)
            docs = await db.scroll_all(
                muse_session_id=sid, muse_stage="shoot",
                exclude_drafts=True, gallery_fields=True,
            )
            found = [
                str(d.get("sha256") or "")
                for d in sorted(docs, key=lambda d: str(d.get("mtime") or ""))
                if d.get("sha256")
            ]
            merged = (found + [i for i in have if i not in set(found)])[:limit]
            if merged and merged != have:
                diary["image_ids"] = merged
                # The cover stays whatever the page already showed.
                diary.setdefault("image_id", merged[-1])
                repaired += 1
                added += max(0, len(merged) - len(have))
                changed = True
        if changed:
            await update_preset(db, pid, {"diaries": diaries})
    return {"scanned": scanned, "repaired": repaired, "photos_added": added}


async def delete_preset_diary(db, preset_id: str, diary_id: str) -> bool:
    """Remove one entry. True when something was actually removed."""
    preset = await get_preset(db, preset_id)
    if not preset:
        return False
    diaries = list(preset.get("diaries") or [])
    kept = [d for d in diaries if str(d.get("id") or "") != str(diary_id)]
    if len(kept) == len(diaries):
        return False
    await update_preset(db, preset_id, {"diaries": kept})
    return True


# ── Chemistry (Qdrant payload, mirrors diaries) ──────────────────────────────
# Generated far less often than diaries — one per duet shoot at most, and only
# once both actors' diaries have landed — so the cap is smaller.
MAX_CHEMISTRY = 30


async def add_chemistry_record(
    db, char_a_id: str, char_b_id: str, base_record: dict[str, Any],
) -> None:
    """Store one chemistry entry on both characters — same content, each side's
    copy pointing at the other as `partner_character_id` (plus her name, so the
    dossier can label the entry with no cross-fetch)."""
    presets_by_id = {
        cid: await get_preset(db, cid) for cid in (char_a_id, char_b_id)
    }
    for owner_id, partner_id in ((char_a_id, char_b_id), (char_b_id, char_a_id)):
        preset = presets_by_id.get(owner_id)
        if not preset:
            continue
        partner_preset = presets_by_id.get(partner_id) or {}
        records = list(preset.get("chemistry") or [])
        records.append({
            **base_record,
            "partner_character_id": partner_id,
            "partner_name_ja": partner_preset.get("name_ja") or partner_preset.get("name") or "",
            "partner_name": partner_preset.get("name") or "",
        })
        if len(records) > MAX_CHEMISTRY:
            records.sort(key=lambda r: r.get("timestamp") or 0.0)
            records = records[-MAX_CHEMISTRY:]
        await update_preset(db, owner_id, {"chemistry": records})


async def mark_diary_read(db, preset_id: str, diary_id: str) -> dict[str, Any] | None:
    """Mark a diary entry as read (read = True) and return the updated diary."""
    preset = await get_preset(db, preset_id)
    if not preset:
        return None
    diaries = list(preset.get("diaries") or [])
    target = None
    for d in diaries:
        if str(d.get("id") or "") == str(diary_id):
            d["read"] = True
            target = d
            break
    if target:
        await update_preset(db, preset_id, {"diaries": diaries})
    return target


async def get_unacknowledged_read_diaries(db, preset_id: str) -> list[dict[str, Any]]:
    """Entries the Showrunner has read but she has never said anything about.

    Newest first. This is what makes her bring it up at the top of the next
    session instead of the instant the panel opened the page.
    """
    diaries = await get_preset_diaries(db, preset_id)
    caught = [
        d for d in diaries
        if d.get("read") and not d.get("secret_banter_fired")
    ]
    caught.sort(key=lambda d: d.get("timestamp") or 0.0, reverse=True)
    return caught


async def mark_secret_banter_fired(db, preset_id: str, diary_ids: list[str]) -> None:
    """Mark these entries as ones she has already been caught over.

    Takes the whole set, not one id: being caught is a single moment, however
    many pages they turned. Anything read *after* this becomes a fresh catch.
    """
    wanted = {str(i) for i in diary_ids if i}
    if not wanted:
        return
    preset = await get_preset(db, preset_id)
    if not preset:
        return
    diaries = list(preset.get("diaries") or [])
    touched = False
    for d in diaries:
        if str(d.get("id") or "") in wanted and not d.get("secret_banter_fired"):
            d["secret_banter_fired"] = True
            touched = True
    if touched:
        await update_preset(db, preset_id, {"diaries": diaries})


async def get_recent_diary_summaries(db, preset_id: str, limit: int = 3) -> list[dict[str, Any]]:
    """Get recent diary summaries (up to `limit`) for prompt injection."""
    diaries = await get_preset_diaries(db, preset_id)
    if not diaries:
        return []
    sorted_diaries = sorted(diaries, key=lambda d: d.get("timestamp") or 0.0, reverse=True)
    return sorted_diaries[:limit]


async def get_social_seeds(db, preset_id: str) -> list[dict[str, Any]]:
    preset = await get_preset(db, preset_id)
    if not preset:
        return []
    return [
        s for s in list(preset.get("social_seeds") or [])
        if int(s.get("uses_left") if s.get("uses_left") is not None else 1) > 0
    ]


async def add_social_seed(db, preset_id: str, seed: dict[str, Any]) -> dict[str, Any] | None:
    """Append a lounge whisper; oldest dropped past the cap. Newest first in storage."""
    async with _social_seeds_lock:
        preset = await get_preset(db, preset_id)
        if not preset:
            return None
        seeds = list(preset.get("social_seeds") or [])
        entry = {
            "id": str(seed.get("id") or uuid.uuid4()),
            "timestamp": float(seed.get("timestamp") or time.time()),
            "source_thread_id": str(seed.get("source_thread_id") or ""),
            "kind": str(seed.get("kind") or "trend"),
            "summary_ja": str(seed.get("summary_ja") or "").strip(),
            "summary_en": str(seed.get("summary_en") or "").strip(),
            "stance": str(seed.get("stance") or "try"),
            "uses_left": int(seed.get("uses_left") if seed.get("uses_left") is not None else 3),
        }
        if not entry["summary_ja"] and not entry["summary_en"]:
            return None
        seeds.insert(0, entry)
        seeds = seeds[:MAX_SOCIAL_SEEDS]
        await update_preset(db, preset_id, {"social_seeds": seeds})
        return entry


async def consume_social_seeds(db, preset_id: str, seed_ids: list[str]) -> None:
    """Decrement uses_left for seeds that coloured a session; drop spent ones."""
    wanted = {str(i) for i in seed_ids if i}
    if not wanted:
        return
    async with _social_seeds_lock:
        preset = await get_preset(db, preset_id)
        if not preset:
            return
        kept: list[dict[str, Any]] = []
        for seed in list(preset.get("social_seeds") or []):
            if str(seed.get("id") or "") in wanted:
                left = int(seed.get("uses_left") or 0) - 1
                if left <= 0:
                    continue
                seed = {**seed, "uses_left": left}
            kept.append(seed)
        await update_preset(db, preset_id, {"social_seeds": kept[:MAX_SOCIAL_SEEDS]})


# ── Memory erase (admin) ─────────────────────────────────────────────────────
# "記憶の消去" — reset every character's accrued memory (diary, chemistry notes,
# lounge whispers) while leaving the character sheet, board and gallery photos
# untouched. This is the payload-field half; the sibling collections
# (muse_sessions, muse_lounge, muse_handpost, character_compat) are cleared by
# their own modules — see characters/api.py's erase-memory endpoint.
MEMORY_FIELDS: tuple[str, ...] = (
    "diaries", "chemistry", "social_seeds", "shoot_recaps",
    "bond", "showrunner_taste",
    "shoot_count", "last_shoot_at",
)

# Sticky detailed shoot recaps kept on the character (older ones go to Qdrant).
MAX_SHOOT_RECAPS = 3
TASTE_MAX_LINES = 8


def _shoot_recap_rows(preset: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in list(preset.get("shoot_recaps") or []) if isinstance(r, dict)]


def _shoot_count(preset: dict[str, Any]) -> int:
    """Lifetime shoots; never less than the sticky recap window we still hold."""
    try:
        stored = int(preset.get("shoot_count") or 0)
    except (TypeError, ValueError):
        stored = 0
    return max(0, stored, len(_shoot_recap_rows(preset)))


def _last_shoot_at(preset: dict[str, Any]) -> float:
    try:
        stored = float(preset.get("last_shoot_at") or 0)
    except (TypeError, ValueError):
        stored = 0.0
    if stored > 0:
        return stored
    rows = _shoot_recap_rows(preset)
    if not rows:
        return 0.0
    try:
        return float(rows[0].get("timestamp") or 0)
    except (TypeError, ValueError):
        return 0.0


async def get_shoot_recaps(db, preset_id: str, limit: int = 3) -> list[dict[str, Any]]:
    preset = await get_preset(db, preset_id)
    if not preset:
        return []
    return _shoot_recap_rows(preset)[:limit]


async def push_shoot_recap(
    db, preset_id: str, recap: dict[str, Any],
) -> dict[str, Any] | None:
    """Insert a detailed recap; return the overflowed oldest row if any."""
    preset = await get_preset(db, preset_id)
    if not preset:
        return None
    rows = _shoot_recap_rows(preset)
    entry = {
        "id": str(recap.get("id") or uuid.uuid4()),
        "timestamp": float(recap.get("timestamp") or time.time()),
        "session_id": str(recap.get("session_id") or ""),
        "when": str(recap.get("when") or "").strip(),
        "feel": str(recap.get("feel") or "").strip(),
        "liked": str(recap.get("liked") or "").strip(),
        "shot": str(recap.get("shot") or "").strip(),
    }
    if not any(entry[k] for k in ("when", "feel", "liked", "shot")):
        return None
    count = _shoot_count(preset) + 1
    rows.insert(0, entry)
    overflow = None
    if len(rows) > MAX_SHOOT_RECAPS:
        overflow = rows.pop()
    await update_preset(db, preset_id, {
        "shoot_recaps": rows[:MAX_SHOOT_RECAPS],
        "shoot_count": count,
        "last_shoot_at": entry["timestamp"],
    })
    return overflow


async def get_bond(db, preset_id: str) -> dict[str, str]:
    preset = await get_preset(db, preset_id)
    if not preset:
        return {}
    bond = preset.get("bond") or {}
    if not isinstance(bond, dict):
        return {}
    return {
        "distance": str(bond.get("distance") or "").strip(),
        "inside": str(bond.get("inside") or "").strip(),
        "last": str(bond.get("last") or "").strip(),
    }


async def update_bond(db, preset_id: str, bond: dict[str, Any]) -> dict[str, str]:
    """Absolute rewrite of the short bond card (Muse-only continuity)."""
    entry = {
        "distance": str(bond.get("distance") or "").strip()[:160],
        "inside": str(bond.get("inside") or "").strip()[:240],
        "last": str(bond.get("last") or "").strip()[:240],
        "updated_at": time.time(),
    }
    await update_preset(db, preset_id, {"bond": entry})
    return {
        "distance": entry["distance"],
        "inside": entry["inside"],
        "last": entry["last"],
    }


async def get_showrunner_taste(db, preset_id: str) -> dict[str, str]:
    preset = await get_preset(db, preset_id)
    if not preset:
        return {}
    taste = preset.get("showrunner_taste") or {}
    if not isinstance(taste, dict):
        return {}
    return {
        "prefers": str(taste.get("prefers") or "").strip(),
        "avoids": str(taste.get("avoids") or "").strip(),
        "notes": str(taste.get("notes") or "").strip(),
    }


async def update_showrunner_taste(
    db, preset_id: str, taste: dict[str, Any],
) -> dict[str, str]:
    """Absolute rewrite of showrunner taste (≤8 lines total)."""
    def _cap(text: str) -> str:
        lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
        return "\n".join(lines[:TASTE_MAX_LINES])[:400]

    entry = {
        "prefers": _cap(taste.get("prefers") or ""),
        "avoids": _cap(taste.get("avoids") or ""),
        "notes": _cap(taste.get("notes") or ""),
        "updated_at": time.time(),
    }
    await update_preset(db, preset_id, {"showrunner_taste": entry})
    return {
        "prefers": entry["prefers"],
        "avoids": entry["avoids"],
        "notes": entry["notes"],
    }


async def get_recent_chemistry_notes(
    db, preset_id: str, limit: int = 2,
) -> list[str]:
    """Short prose from stored chemistry — Muse talk only."""
    preset = await get_preset(db, preset_id)
    if not preset:
        return []
    out: list[str] = []
    for row in list(preset.get("chemistry") or [])[: max(0, limit * 2)]:
        if not isinstance(row, dict):
            continue
        text = str(
            row.get("summary_ja") or row.get("summary") or row.get("note") or ""
        ).strip()
        if text:
            out.append(text[:200])
        if len(out) >= limit:
            break
    return out


async def plan_memory_erase(db) -> dict[str, Any]:
    """Counts for the confirm dialog — nothing is changed."""
    stored = await _stored_rows(db)

    def _count(payload: dict[str, Any], field: str) -> int:
        val = payload.get(field)
        if isinstance(val, list):
            return len(val)
        if isinstance(val, dict):
            return 1 if any(str(v).strip() for v in val.values() if not isinstance(v, (int, float))) else 0
        if isinstance(val, (int, float)):
            return 1 if float(val) else 0
        return 0

    counts = {
        field: sum(_count(p, field) for p in stored.values()) for field in MEMORY_FIELDS
    }
    affected = sum(1 for p in stored.values() if any(_count(p, f) for f in MEMORY_FIELDS))
    return {"characters": len(stored), "affected": affected, **counts}


async def erase_all_memory_fields(db) -> int:
    """Clear diaries/chemistry/social_seeds/recaps/bond/taste on every character.

    A real overwrite via `update_preset` (Qdrant `set_payload`), not a flag —
    the cleared lists are gone once this returns. Returns the number of
    characters that had anything to clear.
    """
    stored = await _stored_rows(db)
    touched = 0
    for pid, payload in stored.items():
        if not any(payload.get(field) for field in MEMORY_FIELDS):
            continue
        clear: dict[str, Any] = {}
        for field in MEMORY_FIELDS:
            if field in ("bond", "showrunner_taste"):
                clear[field] = {}
            elif field in ("shoot_count", "last_shoot_at"):
                clear[field] = 0
            else:
                clear[field] = []
        await update_preset(db, pid, clear)
        touched += 1
    return touched

