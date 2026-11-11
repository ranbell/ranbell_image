"""Hand a bucket over as a folder (or a zip of the same folder).

Layout, the plain one every trainer reads (musubi-tuner, kohya, ai-toolkit):

    <export_dir>/<slug>/
      <sha>.<ext>   the original picture, copied
      <sha>.txt     its caption
      meta.json     trigger, targets, target model, and where each picture came from

The folder is built beside the old one and swapped in by rename, so a trainer
reading it never sees half a set.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..spooler.models import CancelToken, ProgressReporter
from . import bucket as bk
from . import targets as target_models

logger = logging.getLogger(__name__)

FORMAT = "ranbell-lora-dataset/1"


class ExportError(RuntimeError):
    pass


@dataclass
class Entry:
    name: str           # file name inside the dataset folder
    source: Path | None  # copy this file …
    text: str = ""      # … or write this text


def _source_path(doc: dict) -> Path | None:
    for p in [doc.get("path") or ""] + [d.get("path") or "" for d in doc.get("dup_paths") or []]:
        if p and Path(p).is_file():
            return Path(p)
    return None


def _image_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image

        from ..scanner.walk import PIL_FORMATS

        with Image.open(path, formats=PIL_FORMATS) as im:  # reads the header only
            return im.size
    except Exception:
        return None


async def plan(db, bucket: dict) -> list[Entry]:
    """Every file of the dataset. Raises when the bucket is not ready."""
    blockers = bk.export_blockers(bucket)
    if blockers:
        raise ExportError("not ready: " + ", ".join(blockers))

    entries: list[Entry] = []
    meta_items: list[dict] = []
    missing: list[str] = []
    for it in bucket["items"]:
        sha = it["sha256"]
        doc = await db.get(sha) or {}
        src = _source_path(doc)
        if src is None:
            missing.append(sha[:12])
            continue
        ext = src.suffix.lower() or ".png"
        size = _image_size(src)
        entries.append(Entry(f"{sha}{ext}", src))
        entries.append(Entry(f"{sha}.txt", None, it["caption"].strip() + "\n"))
        meta_items.append({
            "image": f"{sha}{ext}",
            "caption": f"{sha}.txt",
            "sha256": sha,
            "source_path": str(src),
            "width": size[0] if size else None,
            "height": size[1] if size else None,
            "edited": bool(it.get("edited")),
        })
    if missing:
        raise ExportError("picture files missing: " + ", ".join(missing))

    meta = {
        "format": FORMAT,
        "name": bucket["name"],
        "slug": bucket["slug"],
        "trigger": bucket["trigger"],
        "targets": bucket["targets"],
        "target_model": target_models.get(bucket.get("target_model")).id,
        "caption_style": target_models.get(bucket.get("target_model")).caption_style,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "count": len(meta_items),
        "items": meta_items,
    }
    entries.append(Entry("meta.json", None, json.dumps(meta, ensure_ascii=False, indent=2) + "\n"))
    return entries


def _write_entry(entry: Entry, dest: Path) -> None:
    if entry.source is not None:
        shutil.copyfile(entry.source, dest)
    else:
        dest.write_text(entry.text, encoding="utf-8")


def write_folder(entries: list[Entry], export_dir: Path, slug: str,
                 cancel: CancelToken | None = None,
                 on_file=None) -> Path:
    """Build the folder beside the live one, then swap it in. Blocking."""
    if not export_dir.is_dir():
        raise ExportError(f"export folder does not exist: {export_dir}")
    final = export_dir / slug
    tag = uuid.uuid4().hex[:8]
    tmp = export_dir / f".{slug}.tmp-{tag}"
    old = export_dir / f".{slug}.old-{tag}"
    tmp.mkdir()
    try:
        for i, entry in enumerate(entries):
            if cancel is not None:
                cancel.raise_if_set()
            _write_entry(entry, tmp / entry.name)
            if on_file is not None:
                on_file(i + 1, len(entries))
        if final.exists():
            os.rename(final, old)
        os.rename(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        if old.exists() and not final.exists():
            os.rename(old, final)  # put the previous set back
        raise
    shutil.rmtree(old, ignore_errors=True)
    return final


def write_zip(entries: list[Entry], slug: str) -> Path:
    """The same folder as a zip in a temp file (stored: pictures are compressed already)."""
    fd, name = tempfile.mkstemp(prefix=f"lora-{slug}-", suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(name, "w", compression=zipfile.ZIP_STORED) as zf:
            for entry in entries:
                arc = f"{slug}/{entry.name}"
                if entry.source is not None:
                    zf.write(entry.source, arc)
                else:
                    zf.writestr(arc, entry.text)
    except BaseException:
        os.unlink(name)
        raise
    return Path(name)


async def run_lora_export(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    bucket_id: str,
    export_dir: str,
) -> dict:
    import asyncio

    async with bk.lock(bucket_id):
        bucket = await bk.load(db, bucket_id)
    if bucket is None:
        raise ExportError("bucket not found")
    entries = await plan(db, bucket)
    cancel.raise_if_set()

    def _progress(done: int, total: int) -> None:
        reporter.update(done / total, f"{done}/{total} files", key="files", done=done, total=total)

    final = await asyncio.get_running_loop().run_in_executor(
        None, lambda: write_folder(entries, Path(export_dir), bucket["slug"], cancel, _progress),
    )

    async with bk.lock(bucket_id):
        bucket = await bk.load(db, bucket_id)
        if bucket is not None:
            bucket["exported_at"] = datetime.now(timezone.utc).isoformat()
            bucket["export_path"] = str(final)
            await bk.save(db, bucket)
    count = sum(1 for e in entries if e.name.endswith(".txt"))
    logger.info("[lora_export] %s → %s (%d pictures)", bucket_id[:8], final, count)
    return {"bucket_id": bucket_id, "path": str(final), "count": count}
