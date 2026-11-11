"""HTTP API for LoRA buckets: gather, caption, export."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from . import bucket as bk
from . import captioner, export
from . import targets as target_models

router = APIRouter(prefix="/api/lora", tags=["lora"])


class BucketCreate(BaseModel):
    name: str
    trigger: str = ""
    targets: list[str] = Field(default_factory=list)
    target_model: str = "krea2"
    sha256s: list[str] = Field(default_factory=list)


class BucketPatch(BaseModel):
    name: str | None = None
    trigger: str | None = None
    targets: list[str] | None = None
    target_model: str | None = None


class ImagesBody(BaseModel):
    sha256s: list[str]


class CaptionBody(BaseModel):
    caption: str


class RecaptionBody(BaseModel):
    sha256s: list[str] = Field(default_factory=list)  # empty = every picture
    step: Literal["describe", "strip"] = "strip"
    force: bool = False  # also replace captions corrected by hand


def _db(request: Request):
    return request.app.state.db


def _view(bucket: dict) -> dict:
    return bk.summary(bucket) | {
        "items": bucket["items"],
        "captioning": captioner.is_captioning(bucket["id"]),
        "blockers": bk.export_blockers(bucket),
    }


async def _load(request: Request, bucket_id: str) -> dict:
    bucket = await bk.load(_db(request), bucket_id)
    if bucket is None:
        raise HTTPException(404, "bucket not found")
    return bucket


def _start_captions(request: Request, bucket: dict) -> None:
    spooler = getattr(request.app.state, "spooler", None)
    if spooler is not None:
        captioner.ensure_job(spooler, db=_db(request), ollama=request.app.state.ollama, bucket=bucket)


async def _export_dir(request: Request) -> Path:
    from ..runtime_config import get_runtime_config

    cfg = await get_runtime_config(_db(request))
    path = Path(str(cfg.get("lora_export_dir") or "").strip() or "/mnt/lora_export")
    return path


@router.get("/targets")
async def list_targets():
    """The models a bucket can be captioned and exported for."""
    return {
        "targets": [
            {"id": t.id, "label": t.label, "caption_style": t.caption_style}
            for t in target_models.available()
        ],
        "default": target_models.DEFAULT_TARGET,
    }


@router.get("/buckets")
async def list_buckets(request: Request):
    rows = await bk.list_all(_db(request))
    return {"buckets": [bk.summary(b) | {"captioning": captioner.is_captioning(b["id"])}
                        for b in rows]}


@router.post("/buckets")
async def create_bucket(body: BucketCreate, request: Request):
    db = _db(request)
    taken = {b.get("slug", "") for b in await bk.list_all(db)}
    try:
        bucket = bk.new_bucket(
            body.name, slug=bk.unique_slug(body.name, taken), trigger=body.trigger,
            targets=body.targets, target_model=body.target_model,
        )
    except bk.BucketError as exc:
        raise HTTPException(400, str(exc))
    bk.add_items(bucket, body.sha256s)
    async with bk.lock(bucket["id"]):
        await bk.save(db, bucket)
    _start_captions(request, bucket)
    return _view(bucket)


@router.get("/buckets/{bucket_id}")
async def get_bucket(bucket_id: str, request: Request):
    return _view(await _load(request, bucket_id))


@router.patch("/buckets/{bucket_id}")
async def patch_bucket(bucket_id: str, body: BucketPatch, request: Request):
    async with bk.lock(bucket_id):
        bucket = await _load(request, bucket_id)
        try:
            bk.update_settings(bucket, name=body.name, trigger=body.trigger, targets=body.targets,
                               target_model=body.target_model)
        except bk.BucketError as exc:
            raise HTTPException(400, str(exc))
        await bk.save(_db(request), bucket)
    _start_captions(request, bucket)
    return _view(bucket)


@router.delete("/buckets/{bucket_id}")
async def delete_bucket(bucket_id: str, request: Request):
    """Removes the bucket only; an exported folder stays where it is."""
    await _load(request, bucket_id)
    await bk.delete(_db(request), bucket_id)
    return {"ok": True}


@router.post("/buckets/{bucket_id}/images")
async def add_images(bucket_id: str, body: ImagesBody, request: Request):
    async with bk.lock(bucket_id):
        bucket = await _load(request, bucket_id)
        added = bk.add_items(bucket, body.sha256s)
        await bk.save(_db(request), bucket)
    _start_captions(request, bucket)
    return _view(bucket) | {"added": len(added)}


@router.delete("/buckets/{bucket_id}/images/{sha256}")
async def remove_image(bucket_id: str, sha256: str, request: Request):
    async with bk.lock(bucket_id):
        bucket = await _load(request, bucket_id)
        bk.remove_items(bucket, [sha256])
        await bk.save(_db(request), bucket)
    return _view(bucket)


@router.put("/buckets/{bucket_id}/images/{sha256}/caption")
async def put_caption(bucket_id: str, sha256: str, body: CaptionBody, request: Request):
    async with bk.lock(bucket_id):
        bucket = await _load(request, bucket_id)
        try:
            bk.set_caption(bucket, sha256, body.caption)
        except bk.BucketError as exc:
            raise HTTPException(404, str(exc))
        await bk.save(_db(request), bucket)
    return _view(bucket)


@router.post("/buckets/{bucket_id}/recaption")
async def recaption(bucket_id: str, body: RecaptionBody, request: Request):
    """Queue pictures again. Also resumes a bucket whose caption job was stopped."""
    async with bk.lock(bucket_id):
        bucket = await _load(request, bucket_id)
        try:
            n = bk.request_recaption(bucket, body.sha256s, step=body.step, force=body.force)
        except bk.BucketError as exc:
            raise HTTPException(400, str(exc))
        await bk.save(_db(request), bucket)
    _start_captions(request, bucket)
    return _view(bucket) | {"queued": n}


@router.post("/buckets/{bucket_id}/export")
async def export_bucket(bucket_id: str, request: Request):
    from ..spooler.models import JobLane

    bucket = await _load(request, bucket_id)
    blockers = bk.export_blockers(bucket)
    if blockers:
        raise HTTPException(409, {"error": "not_ready", "blockers": blockers})
    export_dir = await _export_dir(request)
    if not export_dir.is_dir():
        raise HTTPException(409, {"error": "no_export_dir", "path": str(export_dir)})
    job_id = request.app.state.spooler.submit(
        JobLane.SYNC, "lora_export", export.run_lora_export,
        meta={"bucket_id": bucket_id, "bucket_name": bucket["name"]},
        db=_db(request), bucket_id=bucket_id, export_dir=str(export_dir),
    )
    return {"job_id": job_id, "path": str(export_dir / bucket["slug"])}


@router.get("/buckets/{bucket_id}/export.zip")
async def export_zip(bucket_id: str, request: Request):
    bucket = await _load(request, bucket_id)
    try:
        entries = await export.plan(_db(request), bucket)
    except export.ExportError as exc:
        raise HTTPException(409, str(exc))
    path = await asyncio.get_running_loop().run_in_executor(
        None, export.write_zip, entries, bucket["slug"],
    )
    return FileResponse(
        path, media_type="application/zip", filename=f"{bucket['slug']}.zip",
        background=BackgroundTask(os.unlink, path),
    )
