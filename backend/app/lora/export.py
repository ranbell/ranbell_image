"""Hand a bucket over as a folder (or a zip of the same folder).

Layout, the plain one every trainer reads (musubi-tuner, kohya, ai-toolkit):

    <export_dir>/<slug>/
      <sha>.png     the picture, re-encoded as PNG with no metadata at all
      <sha>.txt     its caption
      meta.json     trigger, targets, target model, and each picture's size

Every picture is decoded and written again as a bare PNG: the prompt, the
ComfyUI workflow, EXIF, XMP and ICC profiles of the original do not travel
with the dataset (EXIF orientation and a non-sRGB profile are applied to the
pixels first, so nothing changes visually). Nothing in the folder says where
the originals live, so a dataset can be handed on as it is.

The folder is built beside the old one and swapped in by rename, so a trainer
reading it never sees half a set.
"""
from __future__ import annotations

import io
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
    source: Path | None  # re-encode this picture as a bare PNG …
    text: str = ""      # … or write this text


def _source_path(doc: dict) -> Path | None:
    for p in [doc.get("path") or ""] + [d.get("path") or "" for d in doc.get("dup_paths") or []]:
        if p and Path(p).is_file():
            return Path(p)
    return None


def _open(path: Path):
    from PIL import Image

    from ..scanner.walk import PIL_FORMATS

    return Image.open(path, formats=PIL_FORMATS)


def _image_size(path: Path) -> tuple[int, int] | None:
    """Size as exported, i.e. after EXIF orientation. Reads the header only."""
    try:
        with _open(path) as im:
            w, h = im.size
            if im.getexif().get(0x0112) in (5, 6, 7, 8):  # rotated by 90°
                w, h = h, w
            return w, h
    except Exception:
        return None


def _to_srgb(im):
    """Apply an embedded colour profile to the pixels, since it is not kept."""
    icc = im.info.get("icc_profile")
    if not icc:
        return im
    try:
        from PIL import ImageCms

        src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        if "srgb" in (ImageCms.getProfileDescription(src) or "").lower():
            return im
        mode = "RGBA" if "A" in im.getbands() else "RGB"
        return ImageCms.profileToProfile(im, src, ImageCms.createProfile("sRGB"),
                                         outputMode=mode)
    except Exception:
        return im  # an unreadable profile: keep the pixels as they are


def clean_png(path: Path) -> bytes:
    """The picture as a PNG carrying pixels only. Blocking."""
    from PIL import ImageOps

    with _open(path) as src:
        src.load()
        im = ImageOps.exif_transpose(src)
        im = _to_srgb(im)
        has_alpha = "A" in im.getbands() or (im.mode == "P" and "transparency" in im.info)
        want = "RGBA" if has_alpha else "RGB"
        if im.mode != want:
            im = im.convert(want)
        # A fresh image object: no info dict, no text chunks, no exif, no icc.
        from PIL import Image

        bare = Image.new(want, im.size)
        bare.paste(im)
        buf = io.BytesIO()
        bare.save(buf, format="PNG", optimize=False, compress_level=6)
        return buf.getvalue()


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
        size = _image_size(src)
        entries.append(Entry(f"{sha}.png", src))
        entries.append(Entry(f"{sha}.txt", None, it["caption"].strip() + "\n"))
        meta_items.append({
            "image": f"{sha}.png",
            "caption": f"{sha}.txt",
            "sha256": sha,
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
        dest.write_bytes(clean_png(entry.source))
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
    """The same folder as a zip in a temp file (stored: PNGs are compressed already)."""
    fd, name = tempfile.mkstemp(prefix=f"lora-{slug}-", suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(name, "w", compression=zipfile.ZIP_STORED) as zf:
            for entry in entries:
                arc = f"{slug}/{entry.name}"
                if entry.source is not None:
                    zf.writestr(arc, clean_png(entry.source))
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
