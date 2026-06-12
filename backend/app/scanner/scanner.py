import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..config import settings
from ..db.qdrant_client import QdrantDBClient
from ..ingest import extract_from_image
from ..thumbnails.generator import ensure_thumbnail, thumbnail_exists

logger = logging.getLogger(__name__)

SCAN_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})

_registering: set[Path] = set()


async def wait_for_registration(path: Path) -> None:
    """Wait until path is no longer being registered."""
    while path in _registering:
        await asyncio.sleep(0.05)


class ScanState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    running: bool = False
    mode: str | None = None
    total: int = 0
    processed: int = 0
    skipped: int = 0
    added: int = 0
    updated: int = 0
    deleted: int = 0
    errors: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    current_file: str | None = None

    def reset(self, mode: str) -> None:
        self.running = True
        self.mode = mode
        self.total = 0
        self.processed = 0
        self.skipped = 0
        self.added = 0
        self.updated = 0
        self.deleted = 0
        self.errors = 0
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.finished_at = None
        self.current_file = None

    def finish(self) -> None:
        self.running = False
        self.finished_at = datetime.now(timezone.utc).isoformat()
        self.current_file = None


scan_state = ScanState()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _collect_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SCAN_EXTENSIONS]


def _collect_all_files() -> list[Path]:
    files: list[Path] = []
    for d in [settings.source_images_dir, settings.generated_images_dir]:
        if d.exists():
            files.extend(_collect_files(d))
    return files


async def _dedup_paths(db: QdrantDBClient) -> int:
    """Remove Qdrant entries whose SHA256 doesn't match the actual file on disk.

    Targets the race-condition artifact where the same path has two entries with
    different SHA256s (one from a partial write, one from the complete file).
    Returns the number of stale entries removed.
    """
    duplicates = await db.find_duplicate_path_sha256s()
    if not duplicates:
        return 0
    loop = asyncio.get_event_loop()
    removed = 0
    for path_str, sha256s in duplicates.items():
        path = Path(path_str)
        if not path.exists():
            continue  # run_heal's delete step will handle missing files
        actual = await loop.run_in_executor(None, _sha256_file, path)
        for sha256 in sha256s:
            if sha256 != actual:
                old_doc = await db.get(sha256)
                if old_doc and old_doc.get("star_rating"):
                    new_doc = await db.get(actual)
                    if new_doc and not new_doc.get("star_rating"):
                        await db.set_payload(actual, {"star_rating": old_doc["star_rating"]})
                        logger.info(
                            "dedup: carried star_rating=%d from %s… to %s…",
                            old_doc["star_rating"], sha256[:8], actual[:8],
                        )
                await db.delete(sha256)
                removed += 1
                logger.info("dedup: removed stale entry %s… (path=%s)", sha256[:8], path.name)
    return removed


async def run_heal(db: QdrantDBClient) -> None:
    """
    Incremental heal scan — path+mtime-first strategy:
      0. Remove duplicate-path entries (race-condition artifacts)
      1. Load path→{sha256,mtime} index from Qdrant (minimal field projection)
      2. Walk filesystem; skip SHA256 hashing when path+mtime match
      3. Detect and remove points whose files no longer exist
    """
    if scan_state.running:
        return

    scan_state.reset("heal")

    try:
        # ── 0. Remove duplicate-path entries ────────────────────────────────
        dedup_count = await _dedup_paths(db)
        if dedup_count:
            logger.info("Heal: removed %d duplicate-path entries", dedup_count)

        # ── 1. Build path index from Qdrant ─────────────────────────────────
        scan_state.current_file = "Loading index…"
        known: dict[str, dict] = await db.find_path_mtime_index()
        known_paths = set(known.keys())
        logger.info("Heal: %d known docs in Qdrant", len(known))

        # ── 2. Walk filesystem (both source and generated dirs) ──────────────
        loop = asyncio.get_event_loop()
        files = await loop.run_in_executor(None, _collect_all_files)
        scan_state.total = len(files)
        logger.info("Heal: %d files on disk", len(files))

        seen_paths: set[str] = set()

        for path in files:
            str_path = str(path)
            seen_paths.add(str_path)
            scan_state.current_file = str_path
            try:
                stat = path.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

                if str_path in known and known[str_path]["mtime"] == mtime:
                    sha256 = known[str_path]["sha256"]
                    if not thumbnail_exists(sha256):
                        await ensure_thumbnail(path, sha256)
                    scan_state.skipped += 1
                else:
                    was_known = str_path in known
                    await _process_image(path, db)
                    scan_state.processed += 1
                    if was_known:
                        scan_state.updated += 1
                    else:
                        scan_state.added += 1
            except Exception:
                logger.exception("Failed to process %s", path)
                scan_state.errors += 1

        # ── 3. Detect deleted files ──────────────────────────────────────────
        deleted_candidates = known_paths - seen_paths
        if deleted_candidates:
            scan_state.current_file = "Detecting deleted files…"
            logger.info("Heal: checking %d potentially deleted paths", len(deleted_candidates))
            for str_path in deleted_candidates:
                sha256 = known[str_path]["sha256"]
                try:
                    doc = await db.get(sha256)
                    if not doc:
                        continue
                    if doc.get("path") == str_path and not Path(str_path).exists():
                        await db.delete(sha256)
                        scan_state.deleted += 1
                        logger.info("Removed stale doc for: %s", str_path)
                except Exception:
                    logger.exception("Failed to remove stale doc %s", sha256)

    finally:
        scan_state.finish()
        logger.info(
            "Heal done: added=%d updated=%d skipped=%d deleted=%d errors=%d",
            scan_state.added, scan_state.updated,
            scan_state.skipped, scan_state.deleted, scan_state.errors,
        )


async def run_scan(db: QdrantDBClient) -> None:
    """Full scan: processes every file. Use for first-time setup or corruption recovery."""
    if scan_state.running:
        return

    scan_state.reset("full")

    try:
        loop = asyncio.get_event_loop()
        files = await loop.run_in_executor(None, _collect_all_files)
        scan_state.total = len(files)
        logger.info("Full scan: %d files", len(files))

        for path in files:
            scan_state.current_file = str(path)
            try:
                await _process_image(path, db)
                scan_state.processed += 1
                scan_state.added += 1
            except Exception:
                logger.exception("Failed to process %s", path)
                scan_state.errors += 1

    finally:
        scan_state.finish()
        logger.info(
            "Full scan done: %d processed, %d errors",
            scan_state.processed, scan_state.errors,
        )


async def run_refresh_metadata(db: QdrantDBClient) -> None:
    """Re-extract positive_prompt/negative_prompt/model_info/extraction/raw_metadata from all images."""
    if scan_state.running:
        return

    scan_state.reset("refresh_metadata")

    try:
        loop = asyncio.get_event_loop()
        all_docs = await db.scroll_all()
        scan_state.total = len(all_docs)
        logger.info("Refresh metadata: %d images", len(all_docs))

        for doc in all_docs:
            path_str = doc.get("path", "")
            sha256 = doc.get("sha256", "")
            scan_state.current_file = path_str
            if not path_str or not sha256:
                scan_state.skipped += 1
                continue
            path = Path(path_str)
            if not path.exists():
                scan_state.skipped += 1
                continue
            try:
                result = await loop.run_in_executor(None, extract_from_image, path)
                update: dict = {
                    "positive_prompt": result.positive_prompt,
                    "negative_prompt": result.negative_prompt,
                    "model_info": result.model_info.to_dict(),
                    "model_name": result.model_info.model_name or "",
                    "params": result.params,
                    "extraction": result.extraction.to_dict(),
                    "raw_metadata": result.raw_metadata.to_dict(),
                }
                update["batch_category"] = "AI" if result.raw_metadata.format in ("a1111", "comfyui") else "NR"
                await db.set_payload(sha256, update)
                scan_state.updated += 1
            except Exception:
                logger.exception("Failed to refresh metadata for %s", path_str)
                scan_state.errors += 1
            scan_state.processed += 1

    finally:
        scan_state.finish()
        logger.info(
            "Refresh metadata done: updated=%d skipped=%d errors=%d",
            scan_state.updated, scan_state.skipped, scan_state.errors,
        )


async def register_image(path: Path, db: QdrantDBClient) -> str:
    """Register a single image file into Qdrant and generate its thumbnail. Returns sha256."""
    if path in _registering:
        logger.debug("register_image: skip (already in-flight) %s", path.name)
        return ""
    _registering.add(path)
    try:
        await _process_image(path, db)
        loop = asyncio.get_event_loop()
        sha256 = await loop.run_in_executor(None, _sha256_file, path)
        return sha256
    finally:
        _registering.discard(path)


async def _process_image(path: Path, db: QdrantDBClient) -> None:
    stat = path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    now = datetime.now(timezone.utc).isoformat()

    loop = asyncio.get_event_loop()
    sha256 = await loop.run_in_executor(None, _sha256_file, path)

    existing = await db.get(sha256)

    if existing:
        # Same content (SHA256 match) — update location metadata only, preserve AI fields
        await db.set_payload(sha256, {
            "path": str(path),
            "name": path.name,
            "ext": path.suffix.lower(),
            "size": stat.st_size,
            "mtime": mtime,
            "scanned_at": now,
            "is_reference": path.is_relative_to(settings.source_images_dir),
        })
        if not thumbnail_exists(sha256):
            await ensure_thumbnail(path, sha256)
        return

    result = await loop.run_in_executor(None, extract_from_image, path)

    payload: dict = {
        "sha256": sha256,
        "path": str(path),
        "name": path.name,
        "ext": path.suffix.lower(),
        "size": stat.st_size,
        "mtime": mtime,
        "scanned_at": now,
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
        "is_reference": path.is_relative_to(settings.source_images_dir),
    }

    await db.upsert_new(sha256, payload)
    await ensure_thumbnail(path, sha256)
