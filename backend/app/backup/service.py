"""Two layers of backup, because there are two ways to lose the data.

Layer 1 is a Qdrant snapshot per collection, kept for a handful of generations.
It restores fast and completely, and it lives inside Qdrant's own storage —
which is exactly what makes it insufficient on its own, because a storage
problem takes the snapshots with it.

Layer 2 is the append-only lineage ledger (see ``ledger.py``): the payload that
no rescan can rebuild, written outside Qdrant as plain gzipped JSONL. It is
small, it is greppable, and it survives losing the database entirely.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .ledger import LINEAGE_FIELDS, WHOLE_COLLECTIONS, Ledger, _digest

logger = logging.getLogger(__name__)

SNAPSHOT_COLLECTIONS = (
    "images", "images_color", "app_config", "wd14_vocab", "character_compat",
    *WHOLE_COLLECTIONS,
)


async def _physical(db, name: str) -> str:
    """Resolve an alias to the collection it points at.

    Snapshots are taken through the alias quite happily, but Qdrant files them
    under the *physical* collection name — `snapshots/images_v3/…`, never
    `snapshots/images/…`. A restore that builds its path from the alias looks
    right and cannot find anything.
    """
    try:
        res = await db._qc.get_aliases()
    except Exception:
        return name
    for a in getattr(res, "aliases", None) or []:
        if getattr(a, "alias_name", None) == name:
            return getattr(a, "collection_name", None) or name
    return name


async def collect_lineage(db, ledger: Ledger) -> tuple[list[dict], dict[str, str]]:
    """Everything worth keeping that has changed since the last run.

    Returns (records to append, the new manifest).
    """
    from ..db.qdrant_client import IMAGES_COLLECTION

    manifest = ledger.load_manifest()
    new_manifest = dict(manifest)
    records: list[dict] = []

    offset = None
    while True:
        pts, offset = await db._qc.scroll(
            IMAGES_COLLECTION, limit=500, with_payload=True,
            with_vectors=False, offset=offset,
        )
        for p in pts:
            payload = p.payload or {}
            lineage = {k: payload[k] for k in LINEAGE_FIELDS if k in payload}
            if not lineage:
                continue
            sha = str(payload.get("sha256") or "")
            if not sha:
                continue
            key = f"images:{sha}"
            digest = _digest(lineage)
            if manifest.get(key) == digest:
                continue
            new_manifest[key] = digest
            records.append({"kind": "images", "id": sha, "payload": lineage})
        if offset is None:
            break

    for name in WHOLE_COLLECTIONS:
        try:
            exists = await db._qc.collection_exists(name)
        except Exception:
            exists = False
        if not exists:
            continue
        offset = None
        while True:
            pts, offset = await db._qc.scroll(
                name, limit=500, with_payload=True, with_vectors=False, offset=offset,
            )
            for p in pts:
                payload = dict(p.payload or {})
                key = f"{name}:{p.id}"
                digest = _digest(payload)
                if manifest.get(key) == digest:
                    continue
                new_manifest[key] = digest
                records.append({"kind": name, "id": str(p.id), "payload": payload})
            if offset is None:
                break

    return records, new_manifest


async def run_lineage_backup(db, root: str | Path) -> dict[str, Any]:
    ledger = Ledger(root)
    ok, why = ledger.writable()
    if not ok:
        # A backup nobody can write is worse than none, because it looks like
        # one. Fail loudly rather than logging and moving on.
        raise RuntimeError(f"lineage backup directory is not writable: {why}")

    records, manifest = await collect_lineage(db, ledger)
    written = ledger.append(records) if records else 0
    if records:
        ledger.save_manifest(manifest)
    logger.info("lineage ledger: appended %d records (%d bytes total)",
                written, ledger.size_bytes())
    return {"appended": written, "bytes": ledger.size_bytes()}


async def run_snapshots(db, *, keep: int) -> dict[str, Any]:
    """Snapshot every collection, then trim to the newest `keep` per collection."""
    made: list[str] = []
    dropped: list[str] = []
    failures: dict[str, str] = {}

    for name in SNAPSHOT_COLLECTIONS:
        try:
            if not await db._qc.collection_exists(name):
                continue
        except Exception:
            continue
        physical = await _physical(db, name)
        try:
            desc = await db._qc.create_snapshot(collection_name=name)
            if desc is not None:
                # Label with the physical name, which is where the file is and
                # what a restore has to be given.
                made.append(f"{physical}/{desc.name}")
        except Exception as e:
            # Most often this is the snapshots directory not being writable by
            # the qdrant container's user — which is silent unless we say so.
            failures[name] = str(e)
            logger.warning("snapshot failed for %s", name, exc_info=True)
            continue

        try:
            snaps = await db._qc.list_snapshots(collection_name=name)
            ordered = sorted(snaps, key=lambda s: getattr(s, "creation_time", "") or "")
            for old in ordered[:-keep] if keep > 0 else []:
                await db._qc.delete_snapshot(collection_name=name, snapshot_name=old.name)
                dropped.append(f"{name}/{old.name}")
        except Exception:
            logger.warning("snapshot rotation failed for %s", name, exc_info=True)

    return {"created": made, "removed": dropped, "failures": failures}


async def list_backups(db, root: str | Path) -> dict[str, Any]:
    ledger = Ledger(root)
    writable, why = ledger.writable()
    snapshots: dict[str, list[dict]] = {}
    for name in SNAPSHOT_COLLECTIONS:
        try:
            if not await db._qc.collection_exists(name):
                continue
            snaps = await db._qc.list_snapshots(collection_name=name)
        except Exception:
            continue
        # Keyed by the physical name: that is the directory the snapshots are
        # in, and the name a restore has to be handed.
        snapshots[await _physical(db, name)] = [
            {"name": s.name, "created": getattr(s, "creation_time", None),
             "size": getattr(s, "size", None)}
            for s in sorted(snaps, key=lambda s: getattr(s, "creation_time", "") or "",
                            reverse=True)
        ]
    return {
        "snapshots": snapshots,
        "ledger": {
            "writable": writable,
            "error": why,
            "files": [p.name for p in reversed(ledger.files())],
            "bytes": ledger.size_bytes(),
        },
    }


async def import_lineage(db, root: str | Path) -> dict[str, Any]:
    """Put back what the ledger has and the database does not.

    Never overwrites. A field already in Qdrant is the live value and this is a
    record of an older one, so the live value wins every time — which is what
    makes it safe to run this at any moment, including straight after a heal
    scan, without thinking about it first.
    """
    from ..db.qdrant_client import IMAGES_COLLECTION
    from ..db.id_utils import sha256_to_point_id

    ledger = Ledger(root)
    entries = ledger.read_all()
    restored = 0
    skipped = 0
    missing = 0

    images = {k: v for k, v in entries.items() if k[0] == "images"}
    for (_, sha), rec in images.items():
        point_id = sha256_to_point_id(sha)
        found = await db._qc.retrieve(
            collection_name=IMAGES_COLLECTION, ids=[point_id],
            with_payload=True, with_vectors=False,
        )
        if not found:
            # The image itself is gone. A heal scan brings it back, and this can
            # be run again afterwards.
            missing += 1
            continue
        live = found[0].payload or {}
        fill = {k: v for k, v in (rec.get("payload") or {}).items()
                if k in LINEAGE_FIELDS and k not in live}
        if not fill:
            skipped += 1
            continue
        await db._qc.set_payload(
            collection_name=IMAGES_COLLECTION, payload=fill, points=[point_id],
        )
        restored += 1

    logger.info("lineage import: filled %d, already present %d, image missing %d",
                restored, skipped, missing)
    return {"restored": restored, "already_present": skipped, "image_missing": missing}
