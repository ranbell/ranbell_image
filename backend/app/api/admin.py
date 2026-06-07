import asyncio
import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ..config import settings
from ..ai import wd14 as wd14_mod
from ..runtime_config import get_runtime_config, invalidate_cache

router = APIRouter(prefix="/api/admin")


# ── Config ────────────────────────────────────────────────────────────────────

class ConfigBody(BaseModel):
    embed_model: str | None = None
    vlm_model: str | None = None
    wd14_threshold: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    wd14_model_dir: str | None = None
    ollama_url: str | None = None
    scan_extensions: list[str] | None = None
    pipeline_batch_size: Annotated[int, Field(ge=1)] | None = None
    pipeline_concurrency: Annotated[int, Field(ge=1)] | None = None
    tags_cache_ttl: Annotated[int, Field(ge=1)] | None = None
    graph_noise_tags: list[str] | None = None
    cluster_common_tags: list[str] | None = None
    prompt_removal_tags: list[str] | None = None
    ollama_num_ctx: Annotated[int, Field(ge=512)] | None = None
    frozenset_classification: bool | None = None
    # GPU priority control
    auto_pause_on_generation: bool | None = None
    auto_pause_lanes: list[str] | None = None
    auto_alignment_evaluate: bool | None = None
    # Processing parallelism
    alignment_concurrency: Annotated[int, Field(ge=1, le=8)] | None = None
    pipeline_auto_continue: bool | None = None


@router.get("/config")
async def get_config(request: Request):
    db = request.app.state.db
    cfg = await get_runtime_config(db)
    cfg["source_images_dir"] = str(settings.source_images_dir)
    cfg["generated_images_dir"] = str(settings.generated_images_dir)
    cfg["thumbnails_dir"] = str(settings.thumbnails_dir)
    return cfg


@router.put("/config")
async def update_config(body: ConfigBody, request: Request):
    db = request.app.state.db
    existing = await db.get_config()
    updates = body.model_dump(exclude_none=True)

    if "wd14_model_dir" in updates and updates["wd14_model_dir"] != existing.get("wd14_model_dir"):
        wd14_mod._session = None
        wd14_mod._tags_df = None
        wd14_mod._loaded_model_dir = None

    existing.update(updates)
    existing["_updated_at"] = time.time()
    await db.put_config(existing)
    invalidate_cache()

    # If pause settings changed, apply them immediately to the running spooler
    if "auto_pause_on_generation" in updates or "auto_pause_lanes" in updates:
        spooler = request.app.state.spooler
        spooler.update_pause_settings(
            auto_pause_on_priority=existing.get("auto_pause_on_generation", True),
            auto_pause_target_lanes=existing.get("auto_pause_lanes", ["embed", "eval"]),
        )

    cfg = await get_runtime_config(db)
    cfg["source_images_dir"] = str(settings.source_images_dir)
    cfg["generated_images_dir"] = str(settings.generated_images_dir)
    cfg["thumbnails_dir"] = str(settings.thumbnails_dir)
    return cfg


# ── Stats ─────────────────────────────────────────────────────────────────────

@router.get("/stats")
async def admin_stats(request: Request):
    db = request.app.state.db

    total, vector_count, cfg = await asyncio.gather(
        db.total_count(),
        db.count_with_embedding(),
        get_runtime_config(db),
    )

    ai_done = vector_count
    ai_pending = total - ai_done

    thumb_dir = Path(settings.thumbnails_dir)
    thumb_count = thumb_size = 0
    if thumb_dir.exists():
        for f in thumb_dir.rglob("*.webp"):
            try:
                thumb_count += 1
                thumb_size += f.stat().st_size
            except OSError:
                pass

    wd14_dir = Path(cfg.get("wd14_model_dir") or settings.wd14_model_dir)
    wd14_model_ok = (wd14_dir / "model.onnx").exists()
    wd14_tags_ok = (wd14_dir / "selected_tags.csv").exists()

    return {
        "images": {
            "total": total,
            "ai_done": ai_done,
            "ai_pending": max(0, ai_pending),
            "ai_unregistered": 0,
            "ai_percent": round(ai_done / total * 100, 1) if total > 0 else 0,
        },
        "vectors": {
            "vector_count": vector_count,
        },
        "thumbnails": {
            "count": thumb_count,
            "size_mb": round(thumb_size / 1_048_576, 1),
        },
        "paths": {
            "source_images_dir": str(settings.source_images_dir),
            "generated_images_dir": str(settings.generated_images_dir),
            "thumbnails_dir": str(settings.thumbnails_dir),
            "wd14_model_dir": str(wd14_dir),
        },
        "wd14": {
            "model_dir": str(wd14_dir),
            "model_ok": wd14_model_ok,
            "tags_ok": wd14_tags_ok,
        },
    }


# ── AI Management ─────────────────────────────────────────────────────────────

class ClearAiRequest(BaseModel):
    scope: Literal["all", "done", "pending"] = "all"


@router.post("/ai/clear")
async def clear_ai_tags(body: ClearAiRequest, request: Request):
    db = request.app.state.db
    count = await db.reset_scope(body.scope)
    return {"cleared": count, "scope": body.scope}


@router.post("/vectors/rebuild")
async def rebuild_vectors(request: Request):
    """Reset all embeddings to pending so the AI pipeline can re-process them."""
    db = request.app.state.db
    count = await db.reset_scope("done")
    return {"reset": count}


# ── Thumbnail Management ──────────────────────────────────────────────────────

@router.post("/thumbnails/clear")
async def clear_thumbnails():
    thumb_dir = Path(settings.thumbnails_dir)
    count = 0
    if thumb_dir.exists():
        for f in thumb_dir.rglob("*.webp"):
            try:
                f.unlink()
                count += 1
            except OSError:
                pass
    return {"deleted": count}


# ── MRL Backfill ──────────────────────────────────────────────────────────────

@router.get("/mrl/status")
async def mrl_status(request: Request):
    db = request.app.state.db
    small_count, full_count, total = await asyncio.gather(
        db.count_small_embeddings(),
        db.count_with_embedding(),
        db.total_count(),
    )
    collection_dim_small = await db.get_collection_embed_dim_small()
    spooler = request.app.state.spooler
    backfill_job = next(
        (j for j in spooler.snapshot() if "MRL" in j.get("title", "") or "mrl" in j.get("id", "")),
        None,
    )
    return {
        "embed_dim": settings.embed_dim,
        "embed_dim_small": settings.embed_dim_small,
        "collection_dim_small": collection_dim_small,
        "total_images": total,
        "full_embeddings": full_count,
        "small_embeddings": small_count,
        "needs_backfill": full_count > 0 and small_count < full_count,
        "backfill": (
            {"running": backfill_job["state"] == "running", "progress": backfill_job["progress"]}
            if backfill_job else {"running": False, "done": 0, "error": None}
        ),
    }


@router.post("/mrl/backfill")
async def start_mrl_backfill(request: Request):
    from ..jobs.runners import run_mrl_backfill
    from ..spooler.models import JobLane
    spooler = request.app.state.spooler
    db = request.app.state.db
    job_id = spooler.submit(JobLane.EMBEDDING, "mrl_backfill", run_mrl_backfill, db=db)
    return {"status": "queued", "job_id": job_id}


# ── Color Palette Backfill ───────────────────────────────────────────────────

@router.get("/colors/status")
async def colors_status(request: Request):
    db = request.app.state.db
    total, with_color_vector, color_lab_count = await asyncio.gather(
        db.total_count(),
        db.count_with_color_vector(),
        db.count_with_color_lab(),
    )
    spooler = request.app.state.spooler
    backfill_job = next(
        (j for j in spooler.snapshot() if j.get("title") == "color_extract"),
        None,
    )
    if backfill_job:
        backfill_info = {"running": backfill_job["state"] == "running", "progress": backfill_job["progress"]}
    else:
        backfill_info = {"running": False, "done": 0, "total": 0, "color_vector_done": 0, "error": None}
    return {
        "total_images": total,
        "with_colors": with_color_vector,
        "with_color_vector": with_color_vector,
        "color_lab_pending": color_lab_count,
        "needs_backfill": with_color_vector < total,
        "needs_color_vector_backfill": with_color_vector < total or color_lab_count > 0,
        "backfill": backfill_info,
    }


@router.post("/colors/backfill")
async def start_color_backfill(request: Request):
    from ..jobs.runners import run_color_backfill
    from ..spooler.models import JobLane
    spooler = request.app.state.spooler
    db = request.app.state.db
    job_id = spooler.submit(JobLane.SYNC, "color_extract", run_color_backfill, db=db)
    return {"status": "queued", "job_id": job_id}


@router.post("/batch-category/backfill")
async def start_batch_category_backfill(request: Request):
    from ..jobs.runners import run_batch_category_backfill
    from ..spooler.models import JobLane
    spooler = request.app.state.spooler
    db = request.app.state.db
    job_id = spooler.submit(JobLane.SYNC, "batch_category_backfill", run_batch_category_backfill, db=db)
    return {"status": "queued", "job_id": job_id}


@router.post("/is-reference/backfill")
async def start_is_reference_backfill(request: Request):
    from ..jobs.runners import run_is_reference_backfill
    from ..spooler.models import JobLane
    spooler = request.app.state.spooler
    db = request.app.state.db
    job_id = spooler.submit(JobLane.SYNC, "is_reference_backfill", run_is_reference_backfill, db=db)
    return {"status": "queued", "job_id": job_id}


# ── Duplicate Detection ───────────────────────────────────────────────────────

@router.get("/duplicates")
async def find_duplicates(request: Request):
    from ..scanner.scanner import _collect_all_files, _sha256_file
    db = request.app.state.db
    loop = asyncio.get_event_loop()
    files = await loop.run_in_executor(None, _collect_all_files)

    sem = asyncio.Semaphore(8)
    sha256_to_paths: dict[str, list[Path]] = {}

    async def _hash(path: Path) -> None:
        async with sem:
            h = await loop.run_in_executor(None, _sha256_file, path)
            sha256_to_paths.setdefault(h, []).append(path)

    await asyncio.gather(*[_hash(f) for f in files])

    path_index = await db.find_path_mtime_index()
    sha256_to_registered = {v["sha256"]: k for k, v in path_index.items()}

    groups = []
    for sha256, paths in sha256_to_paths.items():
        if len(paths) < 2:
            continue
        registered_path = sha256_to_registered.get(sha256)
        str_paths = [str(p) for p in paths]
        primary = registered_path if registered_path in str_paths else str_paths[0]
        copies = [p for p in str_paths if p != primary]
        groups.append({
            "sha256": sha256,
            "registered_path": primary,
            "registered_name": Path(primary).name,
            "copies": [
                {"path": p, "name": Path(p).name, "size": Path(p).stat().st_size}
                for p in copies
            ],
        })

    groups.sort(key=lambda g: g["registered_name"])

    return {
        "total_files_on_disk": len(files),
        "total_registered": len(sha256_to_paths),
        "duplicate_groups": len(groups),
        "duplicate_extra_files": sum(len(g["copies"]) for g in groups),
        "groups": groups,
    }


# ── Scan ──────────────────────────────────────────────────────────────────────

@router.post("/scan/full")
async def full_rescan(request: Request):
    """Delete all image points from Qdrant so a full scan re-registers everything."""
    db = request.app.state.db
    count = await db.delete_all_images()
    return {"deleted": count}
