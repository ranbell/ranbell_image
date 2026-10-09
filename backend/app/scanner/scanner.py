"""Bring Qdrant in line with the image files on disk — touching as little as possible.

What a pass costs, cheapest first:

- **A directory whose mtime has not moved** since it was last reconciled is not
  listed. Adding, removing or renaming an entry is what moves a directory's
  mtime, so nothing about its set of files can have changed. A heal stats every
  known directory (in parallel) and nothing else. `scan_dirs` in Qdrant holds
  what was seen.
- **A file whose (size, mtime_ns) matches** what its point recorded is not read.
- **A file that vanished from one place and appeared at another** with the same
  (size, mtime_ns) — a rename, a move, a folder dragged elsewhere — is not read
  either: the point's path is rewritten.
- **Only what is left is read**, once each: the bytes are hashed and the same
  bytes feed metadata extraction and the thumbnail. Reads run in parallel and
  existence checks and writes go to Qdrant in batches.

One image (sha256) can sit at several paths — the same bytes copied twice. The
point keeps one as `path` and the rest in `dup_paths`, so every copy is known
and none of them is re-read on the next pass.

The directory snapshot is trusted only while the image count matches the one
recorded at the end of the last pass (plus what `register_image` added since).
Anything else that removed or added points behind the scanner's back turns the
next heal into a strict one: every directory listed, still nothing read unless
it differs.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..api.images import invalidate_image_caches
from ..config import settings
from ..db.qdrant_client import QdrantDBClient
from ..ingest import extract_from_image
from ..thumbnails.generator import generate_thumbnail_sync
from .drafts import is_draft_path
from .walk import SCAN_EXTENSIONS, DirListing, FileStat, dir_mtime_ns, list_dir, read_and_hash

__all__ = ["SCAN_EXTENSIONS"]

logger = logging.getLogger(__name__)

# Disk work runs here, not in the default executor, so a long scan does not
# queue behind (or in front of) everything else the app sends to threads.
# Network mounts are latency-bound, so this is wider than the CPU count.
_IO_WORKERS = 16
_io_pool = ThreadPoolExecutor(max_workers=_IO_WORKERS, thread_name_prefix="scan-io")

# One reconcile at a time. A second request waits its turn instead of being
# dropped — the old heal returned early when one was running, and whatever
# changed in the meantime waited for some unrelated next event.
_scan_lock = asyncio.Lock()

_registering: set[Path] = set()
_self_registered: set[Path] = set()

# Points register_image() added since the last pass recorded the count.
_points_delta = 0

# Directories the watcher has seen change, waiting for the next scan_sync job.
_pending_dirs: set[str] = set()
_sync_job_waiting = False


async def _io(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(_io_pool, fn, *args)


async def wait_for_registration(path: Path) -> None:
    """Wait until path is no longer being registered."""
    while path in _registering:
        await asyncio.sleep(0.05)


def consume_self_registered(path: Path) -> bool:
    """True and clears the mark: was this path successfully registered by
    register_image()? One-shot check — the watcher uses this to decide whether
    a generated_dir event still needs a sync, or whether the synchronous save
    path already handled it.
    """
    if path in _self_registered:
        _self_registered.discard(path)
        return True
    return False


def request_sync(dirs: set[str]) -> bool:
    """Queue directories for the next scan_sync. True when the caller must
    submit that job — False when one is already waiting and will pick these up."""
    global _sync_job_waiting
    _pending_dirs.update(dirs)
    if _sync_job_waiting:
        return False
    _sync_job_waiting = True
    return True


def _take_pending_dirs() -> set[str]:
    global _sync_job_waiting
    dirs = set(_pending_dirs)
    _pending_dirs.clear()
    _sync_job_waiting = False
    return dirs


class ScanState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    running: bool = False
    mode: str | None = None
    phase: str | None = None      # "dirs" while checking folders, "files" while reading
    dirs_total: int = 0
    dirs_done: int = 0
    total: int = 0                # files that have to be read
    processed: int = 0
    skipped: int = 0              # files left alone because nothing changed
    added: int = 0
    updated: int = 0
    moved: int = 0                # renames/moves resolved without reading
    deleted: int = 0
    errors: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    current_file: str | None = None

    def reset(self, mode: str) -> None:
        self.running = True
        self.mode = mode
        self.phase = "dirs"
        self.dirs_total = 0
        self.dirs_done = 0
        self.total = 0
        self.processed = 0
        self.skipped = 0
        self.added = 0
        self.updated = 0
        self.moved = 0
        self.deleted = 0
        self.errors = 0
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at = None
        self.current_file = None

    def finish(self) -> None:
        self.running = False
        self.phase = None
        self.finished_at = datetime.now(timezone.utc).isoformat()
        self.current_file = None


scan_state = ScanState()
_legacy_shoot_reclass_done = False


async def _reclassify_legacy_muse_shoots(db: QdrantDBClient) -> None:
    """Once per process: unhide old playground/muse_shoot_* still marked draft."""
    global _legacy_shoot_reclass_done
    if _legacy_shoot_reclass_done:
        return
    unmark = getattr(db, "unmark_legacy_muse_shoot_drafts", None)
    if not callable(unmark):
        _legacy_shoot_reclass_done = True
        return
    try:
        n = await unmark()
        _legacy_shoot_reclass_done = True
        if n:
            invalidate_image_caches()
    except Exception:
        logger.exception("legacy muse_shoot reclass failed")


def _configured_roots() -> list[str]:
    return [str(settings.source_images_dir), str(settings.generated_images_dir)]


def _roots() -> list[str]:
    return [r for r in _configured_roots() if os.path.isdir(r)]


def _is_mount(path: str) -> bool:
    """A root, or a folder directly under the source root — each of those is
    normally its own volume mount (docker-compose.override.yml)."""
    return path in _configured_roots() or os.path.dirname(path) == str(settings.source_images_dir)


def _under_any(path: str, dirs) -> bool:
    return any(path == d or path.startswith(d.rstrip("/") + "/") for d in dirs)


def _iso(mtime_ns: int) -> str:
    return datetime.fromtimestamp(mtime_ns / 1e9, tz=timezone.utc).isoformat()


# ── One image's places on disk ─────────────────────────────────────────────────


@dataclass(slots=True)
class _Loc:
    size: int
    mtime_ns: int | None   # None on points registered before mtime_ns existed
    mtime: str


@dataclass(slots=True)
class _Point:
    sha256: str
    primary: str | None
    locs: dict[str, _Loc]
    doc: dict = field(default_factory=dict)
    new: bool = False
    dirty: bool = False
    written: dict[str, _Loc] | None = None   # locs as last written, for new points
    star_from: int | None = None             # star_rating inherited from a replaced point

    @classmethod
    def from_doc(cls, doc: dict) -> "_Point":
        locs: dict[str, _Loc] = {}
        if doc.get("path"):
            locs[doc["path"]] = _Loc(int(doc.get("size") or 0), doc.get("mtime_ns"), doc.get("mtime") or "")
        for d in doc.get("dup_paths") or []:
            if d.get("path"):
                ns = d.get("mtime_ns")
                locs[d["path"]] = _Loc(int(d.get("size") or 0), ns, _iso(ns) if ns else "")
        return cls(sha256=doc["sha256"], primary=doc.get("path"), locs=locs, doc=doc)

    def add(self, path: str, f: FileStat) -> None:
        self.locs[path] = _Loc(f.size, f.mtime_ns, _iso(f.mtime_ns))
        self.dirty = True

    def drop(self, path: str) -> None:
        if self.locs.pop(path, None) is not None:
            self.dirty = True


def _matches(loc: _Loc, f: FileStat) -> bool:
    if loc.size != f.size:
        return False
    if loc.mtime_ns is not None:
        return loc.mtime_ns == f.mtime_ns
    return loc.mtime == _iso(f.mtime_ns)


def _location_fields(point: _Point) -> dict:
    """Everything in the payload that says where the image is."""
    primary = point.primary if point.primary in point.locs else min(point.locs)
    point.primary = primary
    loc = point.locs[primary]
    p = Path(primary)
    dups = [
        {"path": q, "size": l.size, "mtime_ns": l.mtime_ns}
        for q, l in sorted(point.locs.items()) if q != primary
    ]
    return {
        "path": primary,
        "dir": os.path.dirname(primary),
        "name": p.name,
        "ext": p.suffix.lower(),
        "size": loc.size,
        "mtime": loc.mtime,
        "mtime_ns": loc.mtime_ns,
        "is_reference": p.is_relative_to(settings.source_images_dir),
        # Board sketches stay searchable but are kept out of the default gallery.
        "is_draft": is_draft_path(p),
        "dup_paths": dups,
        "dup_dirs": sorted({os.path.dirname(d["path"]) for d in dups}),
    }


def _new_payload(point: _Point, result) -> dict:
    return {
        "sha256": point.sha256,
        **_location_fields(point),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "positive_prompt": result.positive_prompt,
        "negative_prompt": result.negative_prompt,
        "params": result.params,
        "model_name": result.model_info.model_name or "",
        "model_info": result.model_info.to_dict(),
        "extraction": result.extraction.to_dict(),
        "raw_metadata": result.raw_metadata.to_dict(),
        "wd14_tags": [],
        "embedding_status": "pending",
        "batch_category": "AI" if result.raw_metadata.format in ("a1111", "comfyui") else "NR",
    }


def _ingest_sync(path: str, sha256: str, data: bytes):
    """Metadata and thumbnail from bytes already in memory. Blocking."""
    result = extract_from_image(Path(path), data)
    generate_thumbnail_sync(Path(path), sha256, data)
    return result


# ── Reconcile ──────────────────────────────────────────────────────────────────


async def _list_tree(
    start_dirs: set[str], snapshot: dict[str, dict], strict: bool, keep_mounts: bool,
) -> tuple[dict[str, DirListing], set[str], set[str]]:
    """List `start_dirs`, then any subdirectory the snapshot has never seen (or
    every one, when strict).

    Returns (listings, directories that are gone, mounts that look unavailable).
    With `keep_mounts`, a mount that held images last time but is now missing
    or empty is taken as not mounted right now — a NAS slow to come up, a
    volume missing at boot — not as everything in it deleted. Its images stay.
    """
    children: defaultdict[str, list[str]] = defaultdict(list)
    for path, entry in snapshot.items():
        children[entry.get("parent") or os.path.dirname(path)].append(path)

    def subtree(root: str) -> set[str]:
        out, stack = set(), [root]
        while stack:
            d = stack.pop()
            out.add(d)
            stack.extend(children.get(d, ()))
        return out

    def held_images(d: str) -> bool:
        return any(int(snapshot.get(x, {}).get("n_images") or 0) for x in subtree(d))

    def unavailable_mount(d: str) -> bool:
        return keep_mounts and _is_mount(d) and held_images(d)

    listed: dict[str, DirListing] = {}
    gone: set[str] = set()
    unavailable: set[str] = set()
    frontier = set(start_dirs)
    while frontier:
        batch = sorted(frontier - listed.keys())
        frontier = set()
        scan_state.dirs_total += len(batch)
        results = await asyncio.gather(*(_io(list_dir, d) for d in batch))
        for d, listing in zip(batch, results):
            scan_state.dirs_done += 1
            if listing is None or (not listing.files and not listing.subdirs and unavailable_mount(d)):
                if unavailable_mount(d):
                    unavailable.add(d)
                else:
                    gone |= subtree(d)
                continue
            listed[d] = listing
            present = set(listing.subdirs)
            for sub in listing.subdirs:
                if strict or sub not in snapshot:
                    frontier.add(sub)
            for child in children.get(d, ()):
                if child not in present:
                    if unavailable_mount(child):
                        unavailable.add(child)
                    else:
                        gone |= subtree(child)
    return listed, gone - listed.keys(), unavailable


async def _reconcile(
    db: QdrantDBClient,
    *,
    start_dirs: set[str],
    snapshot: dict[str, dict],
    strict: bool,
    concurrency: int,
    also_gone: set[str] = frozenset(),
    keep_mounts: bool = True,
) -> bool:
    """Make Qdrant match the directories reached from `start_dirs`. Returns
    whether any image point changed. `also_gone` are directories known to be
    gone (or out of reach) without listing them; any of them that was listed
    after all is kept."""
    listed, gone_dirs, unavailable = await _list_tree(start_dirs, snapshot, strict, keep_mounts)
    if keep_mounts:
        unavailable |= {r for r in _configured_roots() if not os.path.isdir(r)}
    gone_dirs |= set(also_gone) - listed.keys()
    if unavailable:
        logger.warning(
            "Scan: %s look unmounted or empty — keeping their images. "
            "If a mount was removed on purpose, run the strict check from the admin screen.",
            ", ".join(sorted(unavailable)),
        )
        gone_dirs = {d for d in gone_dirs if not _under_any(d, unavailable)}
    scope = listed.keys() | gone_dirs
    if not scope:
        return False

    # ── What Qdrant has for these directories ─────────────────────────────────
    points: dict[str, _Point] = {}
    for doc in await db.find_by_dirs(sorted(scope)):
        points[doc["sha256"]] = _Point.from_doc(doc)
    claims: defaultdict[str, list[str]] = defaultdict(list)   # path → sha256s
    for pt in points.values():
        for path in pt.locs:
            if os.path.dirname(path) in scope:
                claims[path].append(pt.sha256)

    on_disk: dict[str, FileStat] = {
        f.path: f for listing in listed.values() for f in listing.files
    }

    # ── Leave alone what has not changed ──────────────────────────────────────
    fresh: list[FileStat] = []
    to_read: list[FileStat] = []
    for path, f in on_disk.items():
        shas = claims.get(path, [])
        if len(shas) == 1:
            pt = points[shas[0]]
            loc = pt.locs[path]
            if _matches(loc, f):
                # An older point without mtime_ns matched on size and the ISO
                # mtime. It is not rewritten just to add mtime_ns: on the live
                # install every per-point write is ~90 ms, which made the first
                # heal over 16k images a 25-minute job of no value.
                scan_state.skipped += 1
                continue
        (to_read if shas else fresh).append(f)

    # ── Renames and moves: same (size, mtime) gone here, appeared there ───────
    gone_locs = [
        (path, sha) for path, shas in claims.items() if path not in on_disk for sha in shas
    ]
    gone_by_key: defaultdict[tuple, list[tuple[str, str]]] = defaultdict(list)
    for path, sha in gone_locs:
        loc = points[sha].locs[path]
        key = ("ns", loc.size, loc.mtime_ns) if loc.mtime_ns is not None else ("iso", loc.size, loc.mtime)
        gone_by_key[key].append((path, sha))
    fresh_keys = defaultdict(int)
    for f in fresh:
        fresh_keys[("ns", f.size, f.mtime_ns)] += 1
        fresh_keys[("iso", f.size, _iso(f.mtime_ns))] += 1

    paired_gone: set[tuple[str, str]] = set()
    unpaired: list[FileStat] = []
    for f in fresh:
        hits = [
            (key, gone_by_key[key]) for key in (("ns", f.size, f.mtime_ns), ("iso", f.size, _iso(f.mtime_ns)))
            if gone_by_key.get(key)
        ]
        if len(hits) == 1 and len(hits[0][1]) == 1 and fresh_keys[hits[0][0]] == 1:
            old_path, sha = hits[0][1][0]
            pt = points[sha]
            was_primary = pt.primary == old_path
            pt.drop(old_path)
            pt.add(f.path, f)
            if was_primary:
                pt.primary = f.path
            paired_gone.add((old_path, sha))
            scan_state.moved += 1
        else:
            unpaired.append(f)
    for path, sha in gone_locs:
        if (path, sha) not in paired_gone:
            points[sha].drop(path)

    # ── Read what is left — once each, in parallel ────────────────────────────
    to_read.extend(unpaired)
    scan_state.phase = "files"
    scan_state.total = len(to_read)
    successor: dict[str, str] = {}          # replaced point → the one that took its path
    error_dirs: set[str] = set()
    sem = asyncio.Semaphore(max(1, concurrency))

    async def read_one(f: FileStat):
        async with sem:
            scan_state.current_file = f.path
            try:
                data, sha = await _io(read_and_hash, f.path)
                return f, data, sha
            except Exception:
                logger.exception("Failed to read %s", f.path)
                scan_state.errors += 1
                scan_state.processed += 1
                error_dirs.add(os.path.dirname(f.path))
                return None

    async def ingest_one(f: FileStat, data: bytes, sha: str):
        async with sem:
            try:
                return await _io(_ingest_sync, f.path, sha, data)
            except Exception:
                logger.exception("Failed to process %s", f.path)
                scan_state.errors += 1
                error_dirs.add(os.path.dirname(f.path))
                return None

    chunk_size = max(1, concurrency) * 2   # bounds how many files' bytes are held at once
    for i in range(0, len(to_read), chunk_size):
        chunk = [r for r in await asyncio.gather(*(read_one(f) for f in to_read[i:i + chunk_size])) if r]
        unknown = sorted({sha for _, _, sha in chunk if sha not in points})
        if unknown:
            for sha, doc in (await db.get_locations(unknown)).items():
                points[sha] = _Point.from_doc(doc)

        new_items: list[tuple[FileStat, bytes, str]] = []
        for f, data, sha in chunk:
            for old in claims.get(f.path, []):
                if old != sha:
                    points[old].drop(f.path)
                    successor[old] = sha
            pt = points.get(sha)
            if pt is None:
                new_items.append((f, data, sha))
            else:
                pt.add(f.path, f)
                scan_state.processed += 1

        # Two new copies of the same bytes in one chunk: ingest once.
        first: dict[str, tuple[FileStat, bytes, str]] = {}
        for item in new_items:
            first.setdefault(item[2], item)
        results = await asyncio.gather(*(ingest_one(*item) for item in first.values()))
        to_insert: list[tuple[str, dict]] = []
        for (f, _, sha), result in zip(first.values(), results):
            if result is None:
                error_dirs.update(os.path.dirname(g.path) for g, _, s in new_items if s == sha)
                continue
            pt = _Point(sha256=sha, primary=f.path, locs={}, new=True)
            pt.add(f.path, f)
            points[sha] = pt
            to_insert.append((sha, _new_payload(pt, result)))
            pt.written = dict(pt.locs)
            scan_state.added += 1
        for f, _, sha in new_items:
            scan_state.processed += 1
            if first[sha][0] is not f and sha in points:
                points[sha].add(f.path, f)
        # Written as each chunk finishes: a scan stopped half way keeps what it
        # already read, and the next pass finds those files matching.
        if to_insert:
            await db.upsert_new_batch(to_insert, wait=False)

    # ── Write the rest ─────────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    deletes: list[str] = []
    for pt in points.values():
        if not pt.locs and not pt.new:
            deletes.append(pt.sha256)
            star = pt.doc.get("star_rating")
            heir = points.get(successor.get(pt.sha256, ""))
            if star and heir and not heir.doc.get("star_rating") and heir.star_from is None:
                heir.star_from = star
                heir.dirty = True
    updates: list[tuple[str, dict]] = []
    for pt in points.values():
        if not pt.locs:
            if pt.new:
                deletes.append(pt.sha256)
            continue
        if pt.new and pt.locs == pt.written and pt.star_from is None:
            continue
        if not pt.dirty:
            continue
        upd = {**_location_fields(pt), "scanned_at": now}
        if pt.star_from is not None:
            upd["star_rating"] = pt.star_from
            logger.info("carried star_rating=%d to %s…", pt.star_from, pt.sha256[:8])
        updates.append((pt.sha256, upd))
        if not pt.new:
            scan_state.updated += 1
    if updates:
        await db.set_payload_batch(updates, wait=False)
    if deletes:
        await db.delete_batch(deletes, wait=False)
        scan_state.deleted += len(deletes)
    if scan_state.added or updates or deletes:
        # Everything above went in without waiting; one wait covers it all
        # before the folder snapshot says these folders are done.
        await db.images_barrier()

    # ── Remember what was seen ────────────────────────────────────────────────
    entries = []
    for path, listing in listed.items():
        entries.append({
            "path": path,
            "parent": os.path.dirname(path),
            # A directory with a file that could not be read, or one modified
            # too recently to trust its mtime (walk.RACY_NS), keeps an mtime
            # that can never match, so the next heal comes back for it.
            "mtime_ns": -1 if path in error_dirs or listing.racy else listing.mtime_ns,
            "n_images": len(listing.files),
            "ok_at": now,
        })
    if entries:
        await db.put_scan_dirs(entries)
    stale = sorted(d for d in gone_dirs if d in snapshot)
    if stale:
        await db.delete_scan_dirs(stale)

    return bool(scan_state.added or updates or deletes)


async def _prepare(db: QdrantDBClient) -> tuple[dict[str, dict], dict]:
    snapshot, meta = await db.load_scan_dirs()
    if not meta.get("dir_field"):
        n = await db.backfill_image_dirs()
        logger.info("Scan: wrote dir onto %d older images", n)
        meta = {**meta, "dir_field": True}
        await db.put_scan_meta(meta)
    return snapshot, meta


async def _record_count(db: QdrantDBClient, meta: dict) -> None:
    global _points_delta
    _points_delta = 0
    await db.put_scan_meta({**meta, "points": await db.total_count()})


def _log_done(label: str, t0: float) -> None:
    logger.info(
        "%s done in %.1fs: dirs=%d read=%d added=%d updated=%d moved=%d "
        "skipped=%d deleted=%d errors=%d",
        label, asyncio.get_running_loop().time() - t0,
        scan_state.dirs_done, scan_state.total, scan_state.added, scan_state.updated,
        scan_state.moved, scan_state.skipped, scan_state.deleted, scan_state.errors,
    )


async def run_heal(db: QdrantDBClient, *, strict: bool = False, concurrency: int = 8,
                   mode: str | None = None) -> None:
    """Find and apply every change since the last pass.

    Normally only directories whose mtime moved are listed. `strict` lists every
    directory (for changes a directory's mtime cannot show — a file overwritten
    in place while the app was down). Neither reads a file that still matches.

    A heal that turns strict on its own (the image count moved) still keeps the
    images of a mount that looks missing; only a strict pass asked for by name
    lets them go.
    """
    # Only a strict pass someone asked for may treat a missing mount as gone.
    keep_mounts = not strict
    async with _scan_lock:
        scan_state.reset(mode or ("strict" if strict else "heal"))
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        changed = False
        try:
            roots = _roots()
            snapshot, meta = await _prepare(db)
            await _reclassify_legacy_muse_shoots(db)

            if not strict:
                count = await db.total_count()
                expected = meta.get("points")
                if not snapshot:
                    strict = True
                    logger.info("Heal: no directory snapshot yet — listing everything")
                elif expected is None or count != expected + _points_delta:
                    strict = True
                    logger.info(
                        "Heal: %d images, expected %s — changed outside the scanner, listing everything",
                        count, None if expected is None else expected + _points_delta,
                    )

            # Folders no longer under any root (a mount taken away) go the way
            # of any vanished folder: their images lose those places.
            gone = {p for p in snapshot if not _under_any(p, _configured_roots())}
            if strict:
                start = set(roots)
                # Directories Qdrant still has images in but that are no longer
                # on disk would never be reached by listing what is there.
                gone |= await db.distinct_image_dirs()
            else:
                known = [p for p in snapshot if _under_any(p, roots)]
                scan_state.dirs_total = len(known)
                mtimes = await asyncio.gather(*(_io(dir_mtime_ns, p) for p in known))
                scan_state.dirs_done = len(known)
                start = {p for p, m in zip(known, mtimes) if m != snapshot[p].get("mtime_ns")}
                start |= {r for r in roots if r not in snapshot}
                logger.info("Heal: %d folders checked, %d changed", len(known), len(start))
                scan_state.dirs_total = scan_state.dirs_done = 0

            changed |= await _reconcile(
                db, start_dirs=start, snapshot=snapshot, strict=strict,
                concurrency=concurrency, also_gone=gone, keep_mounts=keep_mounts,
            )
            await _record_count(db, meta)
        finally:
            scan_state.finish()
            if changed:
                invalidate_image_caches()
            _log_done("Heal" if not strict else "Strict heal", t0)


async def run_sync(db: QdrantDBClient, *, concurrency: int = 8) -> None:
    """Reconcile only the directories the watcher saw change."""
    dirs = _take_pending_dirs()
    async with _scan_lock:
        scan_state.reset("sync")
        t0 = asyncio.get_running_loop().time()
        changed = False
        try:
            roots = _roots()
            dirs = {d for d in dirs if _under_any(d, roots)}
            if not dirs:
                return
            snapshot, meta = await _prepare(db)
            count_before = await db.total_count()
            in_step = meta.get("points") is not None and count_before == meta["points"] + _points_delta
            changed = await _reconcile(
                db, start_dirs=dirs, snapshot=snapshot, strict=False, concurrency=concurrency,
            )
            # Keep the heal's guard honest: only re-record the count when it
            # was in step before — otherwise leave the mismatch for heal to see.
            if in_step:
                await _record_count(db, meta)
        finally:
            scan_state.finish()
            if changed:
                invalidate_image_caches()
            _log_done("Sync", t0)


async def run_scan(db: QdrantDBClient, concurrency: int = 8) -> None:
    """Full scan: every directory listed, every file not already in Qdrant read.
    Used after /admin/scan/full emptied the collection (and the snapshot)."""
    await run_heal(db, strict=True, concurrency=concurrency, mode="full")


async def run_refresh_metadata(db: QdrantDBClient, methods: list[str] | None = None) -> None:
    """Re-extract prompts, model and params from the image files.

    `methods` limits it to images whose last extraction came out of those
    methods (e.g. the "longest_text" fallback after the tracer learned a new
    node) instead of re-reading the whole library.
    """
    if scan_state.running:
        return

    scan_state.reset("refresh_metadata")
    scan_state.phase = "files"

    try:
        docs = await (db.scroll_by_extraction_method(methods) if methods else db.scroll_all())
        scan_state.total = len(docs)
        logger.info("Refresh metadata: %d images%s", len(docs), f" ({', '.join(methods)})" if methods else "")

        pending: list[tuple[str, dict]] = []
        for doc in docs:
            path_str = doc.get("path", "")
            sha256 = doc.get("sha256", "")
            scan_state.current_file = path_str
            scan_state.processed += 1
            if not path_str or not sha256 or not await _io(os.path.exists, path_str):
                scan_state.skipped += 1
                continue
            try:
                result = await _io(extract_from_image, Path(path_str))
            except Exception:
                logger.exception("Failed to refresh metadata for %s", path_str)
                scan_state.errors += 1
                continue
            pending.append((sha256, {
                "positive_prompt": result.positive_prompt,
                "negative_prompt": result.negative_prompt,
                "model_info": result.model_info.to_dict(),
                "model_name": result.model_info.model_name or "",
                "params": result.params,
                "extraction": result.extraction.to_dict(),
                "raw_metadata": result.raw_metadata.to_dict(),
                "batch_category": "AI" if result.raw_metadata.format in ("a1111", "comfyui") else "NR",
            }))
            scan_state.updated += 1
            if len(pending) >= 64:
                await db.set_payload_batch(pending, wait=False)
                pending = []
        if pending:
            await db.set_payload_batch(pending, wait=False)
        if scan_state.updated:
            await db.images_barrier()
            invalidate_image_caches()

    finally:
        scan_state.finish()
        logger.info(
            "Refresh metadata done: updated=%d skipped=%d errors=%d",
            scan_state.updated, scan_state.skipped, scan_state.errors,
        )


async def register_image(path: Path, db: QdrantDBClient, data: bytes | None = None) -> str:
    """Register one image file into Qdrant and make its thumbnail. Returns sha256.

    `data` is the file's bytes when the caller still has them (a save just
    wrote them) — then the file is not read back.
    """
    global _points_delta
    if path in _registering:
        logger.debug("register_image: skip (already in-flight) %s", path.name)
        return ""
    _registering.add(path)
    try:
        st = await _io(os.stat, path)
        if data is None:
            data, sha256 = await _io(read_and_hash, str(path))
        else:
            sha256 = hashlib.sha256(data).hexdigest()
        f = FileStat(str(path), st.st_size, st.st_mtime_ns)

        found = await db.get_locations([sha256])
        if sha256 in found:
            pt = _Point.from_doc(found[sha256])
            pt.add(f.path, f)
            await db.set_payload_batch([(sha256, {
                **_location_fields(pt), "scanned_at": datetime.now(timezone.utc).isoformat(),
            })])
        else:
            result = await _io(_ingest_sync, f.path, sha256, data)
            pt = _Point(sha256=sha256, primary=f.path, locs={}, new=True)
            pt.add(f.path, f)
            await db.upsert_new_batch([(sha256, _new_payload(pt, result))])
            _points_delta += 1
        _self_registered.add(path)
        return sha256
    finally:
        _registering.discard(path)
