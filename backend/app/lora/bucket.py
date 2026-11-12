"""A LoRA bucket and the rules for moving its pictures through captioning.

Each picture goes through two steps:

1. **describe** — the VLM looks at the picture once and writes everything it
   sees (``full_caption``).
2. **strip** — a text model removes what the bucket is meant to teach
   (``targets``) from that description, and the trigger word goes in front
   (``caption``). Left undescribed, those things are what the trigger word
   absorbs in training.

``full_caption`` is kept, so changing the targets or the trigger only re-runs
the strip step and never looks at the pictures again.

A caption the Showrunner corrected by hand (``edited``) is never overwritten
by a recompute; it is marked ``stale`` instead, and only an explicit
``force`` replaces it.

Everything in this module that decides state works on plain dicts, so it can
be tested without Qdrant; the store functions at the bottom only load and save.
"""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Any

from qdrant_client import models as qm

from ..db.qdrant_client import LORA_BUCKETS_COLLECTION
from . import targets as target_models

# Item states.
PENDING_DESCRIBE = "pending_describe"
PENDING_STRIP = "pending_strip"
DONE = "done"
ERROR = "error"
PENDING = (PENDING_DESCRIBE, PENDING_STRIP)

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class BucketError(ValueError):
    """A request that cannot be applied to the bucket as it is."""


def slugify(name: str) -> str:
    """Folder-safe form of a bucket name. Never empty."""
    s = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return s[:64] or "bucket"


def unique_slug(name: str, taken: set[str]) -> str:
    base = slugify(name)
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def clean_targets(targets: list[str] | None) -> list[str]:
    """Trimmed, de-duplicated (case-insensitively), order kept."""
    out: list[str] = []
    seen: set[str] = set()
    for t in targets or []:
        t = str(t).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def new_bucket(name: str, *, slug: str, trigger: str = "", targets: list[str] | None = None,
               target_model: str = "krea2") -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise BucketError("name is required")
    _check_target(target_model)
    now = time.time()
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "slug": slug,
        "trigger": (trigger or "").strip(),
        "targets": clean_targets(targets),
        "target_model": target_model,
        "items": [],
        "created_at": now,
        "updated_at": now,
        "exported_at": None,
        "export_path": "",
    }


def _check_target(target_model: str) -> None:
    t = target_models.TARGETS.get(target_model)
    if t is None or not t.available:
        names = ", ".join(x.id for x in target_models.available())
        raise BucketError(f"unknown or unavailable target model: {target_model} (available: {names})")


def _item(sha: str) -> dict[str, Any]:
    return {
        "sha256": sha,
        "full_caption": "",
        "caption": "",
        "edited": False,
        "stale": False,
        "status": PENDING_DESCRIBE,
        "error": "",
        "added_at": time.time(),
    }


def find_item(bucket: dict, sha: str) -> dict | None:
    return next((it for it in bucket["items"] if it["sha256"] == sha), None)


def clean_sha(sha: str) -> str:
    """A picture id as stored: 64 lowercase hex digits. Anything else is
    refused — the id becomes a file name in the export."""
    s = str(sha or "").strip().lower()
    if not _SHA_RE.match(s):
        raise BucketError(f"not a picture id: {str(sha)[:80]!r}")
    return s


def add_items(bucket: dict, shas: list[str]) -> list[str]:
    """Add pictures not already in the bucket. Returns the ones added."""
    have = {it["sha256"] for it in bucket["items"]}
    added: list[str] = []
    for sha in shas:
        sha = str(sha).strip()
        if sha and sha not in have:
            bucket["items"].append(_item(sha))
            have.add(sha)
            added.append(sha)
    return added


def remove_items(bucket: dict, shas: list[str]) -> int:
    drop = set(shas)
    before = len(bucket["items"])
    bucket["items"] = [it for it in bucket["items"] if it["sha256"] not in drop]
    return before - len(bucket["items"])


def set_caption(bucket: dict, sha: str, caption: str) -> dict:
    """The Showrunner's own words for one picture."""
    it = find_item(bucket, sha)
    if it is None:
        raise BucketError("picture is not in this bucket")
    it["caption"] = (caption or "").strip()
    it["edited"] = True
    it["stale"] = False
    it["status"] = DONE
    it["error"] = ""
    return it


def update_settings(bucket: dict, *, name: str | None = None, trigger: str | None = None,
                    targets: list[str] | None = None, target_model: str | None = None) -> bool:
    """Apply a settings change. True when captions have to be recomputed.

    A new target model changes how captions read, so every picture is
    described again (hand edits are kept and marked stale). A new trigger or
    new targets only re-run the strip step.
    """
    new_model = target_model is not None and target_model != bucket.get("target_model")
    if new_model:
        _check_target(target_model)  # before anything changes
    if name is not None:
        if not name.strip():
            raise BucketError("name is required")
        bucket["name"] = name.strip()
    changed = False
    if trigger is not None and trigger.strip() != bucket["trigger"]:
        bucket["trigger"] = trigger.strip()
        changed = True
    if targets is not None:
        new = clean_targets(targets)
        if new != bucket["targets"]:
            bucket["targets"] = new
            changed = True
    if new_model:
        bucket["target_model"] = target_model
        request_recaption(bucket, None, step="describe", force=False)
        return True
    if changed:
        _mark_for_strip(bucket, [it["sha256"] for it in bucket["items"]], force=False)
    return changed


def _mark_for_strip(bucket: dict, shas: list[str], *, force: bool) -> int:
    n = 0
    want = set(shas)
    for it in bucket["items"]:
        if it["sha256"] not in want:
            continue
        if it["edited"] and not force:
            # Before the description check: a hand caption with no description
            # sent back to "describe" would never leave it (the describe result
            # defers to the hand), and the job would pick it up for ever.
            it["stale"] = True
            continue
        if not it["full_caption"]:
            # Never described (or that failed): it has to be looked at first.
            if it["status"] != PENDING_DESCRIBE:
                it["status"], it["error"] = PENDING_DESCRIBE, ""
                it["edited"], it["stale"] = False, False
                n += 1
            continue
        it["status"], it["error"] = PENDING_STRIP, ""
        it["edited"], it["stale"] = False, False
        n += 1
    return n


def request_recaption(bucket: dict, shas: list[str] | None, *, step: str, force: bool) -> int:
    """Queue pictures again. ``step`` is "describe" (look again) or "strip"."""
    shas = shas if shas else [it["sha256"] for it in bucket["items"]]
    if step == "strip":
        return _mark_for_strip(bucket, shas, force=force)
    if step != "describe":
        raise BucketError(f"unknown step: {step}")
    want, n = set(shas), 0
    for it in bucket["items"]:
        if it["sha256"] not in want:
            continue
        if it["edited"] and not force:
            it["stale"] = True
            continue
        it.update(status=PENDING_DESCRIBE, error="", edited=False, stale=False)
        n += 1
    return n


def resume(bucket: dict) -> int:
    """'Resume': pictures that were waiting when the job stopped, and nothing
    else. A picture held as waiting by a hand edit (an older bug) is let go."""
    n = 0
    for it in bucket["items"]:
        if it["status"] in PENDING:
            if it["edited"]:
                it["status"], it["error"] = DONE, ""
            else:
                n += 1
    return n


def next_pending(bucket: dict) -> dict | None:
    """The next picture to work on, oldest first."""
    return next((it for it in bucket["items"] if it["status"] in PENDING), None)


def pending_count(bucket: dict) -> int:
    return sum(1 for it in bucket["items"] if it["status"] in PENDING)


def apply_described(bucket: dict, sha: str, full_caption: str) -> None:
    it = find_item(bucket, sha)
    if it is None:  # removed while it was being read
        return
    it["full_caption"] = full_caption.strip()
    if it["edited"]:
        # Corrected by hand while the VLM was still looking: the hand wins,
        # and the picture is done (never left waiting, or the job loops).
        it["status"], it["error"] = DONE, ""
        return
    it["status"], it["error"] = PENDING_STRIP, ""


def strip_setting(bucket: dict) -> list:
    """What a strip result depends on besides the description."""
    return [bucket.get("trigger", ""), list(bucket.get("targets") or [])]


def apply_stripped(bucket: dict, sha: str, caption: str, *, expected_full: str,
                   expected_setting: list | None = None) -> None:
    """Store a strip result — unless the picture, the trigger or the targets
    changed under it meanwhile (it stays waiting and is stripped again)."""
    it = find_item(bucket, sha)
    if it is None or it["status"] != PENDING_STRIP or it["full_caption"] != expected_full:
        return
    if expected_setting is not None and expected_setting != strip_setting(bucket):
        return
    it["caption"] = caption.strip()
    it["status"], it["error"] = DONE, ""


def apply_error(bucket: dict, sha: str, message: str) -> None:
    it = find_item(bucket, sha)
    if it is None:
        return
    if it["edited"]:
        it["status"], it["error"] = DONE, ""  # the hand caption stands
        return
    it["status"], it["error"] = ERROR, message


def with_trigger(trigger: str, text: str) -> str:
    """The trigger word in front, written once."""
    text = (text or "").strip()
    trigger = (trigger or "").strip()
    if not trigger:
        return text
    head = text[len(trigger):len(trigger) + 1]
    exact = text.startswith(trigger) and not (head.isalnum() or head == "_")
    # Another case only when it stands as a tag ("Rnbl." / "RNBL,"): trigger
    # "a" must not eat the "A" of "A woman", nor "zqx" the start of "zqxstyle".
    tagged = text.lower().startswith(trigger.lower()) and head in ("", ",", ".", ":", ";")
    if exact or tagged:
        text = text[len(trigger):].lstrip(" ,.:;")
    return f"{trigger}, {text}" if text else trigger


def export_blockers(bucket: dict) -> list[str]:
    """Why the bucket cannot be exported yet. Empty when it can."""
    reasons: list[str] = []
    if not bucket["items"]:
        reasons.append("empty")
    if pending_count(bucket):
        reasons.append("pending")
    if any(it["status"] == ERROR for it in bucket["items"]):
        reasons.append("error")
    if any(it["status"] == DONE and not it["caption"] for it in bucket["items"]):
        reasons.append("blank_caption")
    return reasons


def pending_buckets(rows: list[dict]) -> list[dict]:
    """Buckets with pictures still waiting for a caption step."""
    return [b for b in rows if pending_count(b)]


def summary(bucket: dict) -> dict[str, Any]:
    """What the bucket list shows."""
    items = bucket["items"]
    return {
        k: bucket.get(k) for k in (
            "id", "name", "slug", "trigger", "targets", "target_model",
            "created_at", "updated_at", "exported_at", "export_path",
        )
    } | {
        "count": len(items),
        "pending": pending_count(bucket),
        "errors": sum(1 for it in items if it["status"] == ERROR),
        "stale": sum(1 for it in items if it.get("stale")),
        "cover": items[-1]["sha256"] if items else "",
    }


# ── Store ─────────────────────────────────────────────────────────────────────
#
# One backend process owns the buckets, so an in-process lock per bucket is
# enough to keep the caption job and the screen from overwriting each other's
# read-modify-write.

_locks: dict[str, asyncio.Lock] = {}


def lock(bucket_id: str) -> asyncio.Lock:
    return _locks.setdefault(bucket_id, asyncio.Lock())


def valid_id(any_id: str) -> bool:
    try:
        uuid.UUID(str(any_id))
        return True
    except ValueError:
        return False


async def load(db, bucket_id: str) -> dict | None:
    """The bucket, or None when there is no such bucket. A malformed id is
    "no such bucket"; a store that is briefly unreachable is an error, not
    "deleted" (that silently ended caption jobs)."""
    if not valid_id(bucket_id):
        return None
    points = await db._qc.retrieve(
        collection_name=LORA_BUCKETS_COLLECTION, ids=[bucket_id], with_payload=True,
    )
    return dict(points[0].payload or {}) if points else None


async def save(db, bucket: dict) -> dict:
    bucket["updated_at"] = time.time()
    await db._qc.upsert(
        collection_name=LORA_BUCKETS_COLLECTION,
        points=[qm.PointStruct(id=bucket["id"], vector={}, payload=bucket)],
    )
    return bucket


async def list_all(db) -> list[dict]:
    rows: list[dict] = []
    offset = None
    while True:
        points, offset = await db._qc.scroll(
            collection_name=LORA_BUCKETS_COLLECTION, limit=128, offset=offset,
            with_payload=True,
        )
        rows.extend(dict(p.payload or {}) for p in points)
        if offset is None or not points:
            break
    rows.sort(key=lambda b: b.get("updated_at") or 0.0, reverse=True)
    return rows


async def delete(db, bucket_id: str) -> None:
    """Call with the bucket's lock held, so a job's load-modify-save cannot
    bring it back right after."""
    await db._qc.delete(
        collection_name=LORA_BUCKETS_COLLECTION,
        points_selector=qm.PointIdsList(points=[bucket_id]),
    )


def forget_lock(bucket_id: str) -> None:
    """Drop a deleted bucket's lock — only when nobody holds or waits for it."""
    lk = _locks.get(bucket_id)
    if lk is not None and not lk.locked():
        _locks.pop(bucket_id, None)
