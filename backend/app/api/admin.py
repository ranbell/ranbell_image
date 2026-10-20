import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..config import settings
from ..ai import wd14 as wd14_mod
from ..runtime_config import LEGACY_KEYS, get_runtime_config, invalidate_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin")


# ── Config ────────────────────────────────────────────────────────────────────

class ConfigBody(BaseModel):
    embed_model: str | None = None
    vlm_model: str | None = None
    # Small/fast model for short structured calls. Empty → falls back to vlm_model.
    utility_model: str | None = None
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
    # External OpenAI-compatible server (Inspire / Refine)
    external_llm_url: str | None = None
    # Never sent back to the browser, so an empty value means "unchanged";
    # `external_llm_api_key_clear` removes a saved key.
    external_llm_api_key: str | None = None
    external_llm_api_key_clear: bool | None = None
    external_llm_model: str | None = None
    external_llm_vlm_model: str | None = None
    inspire_llm_backend: Literal["ollama", "openai"] | None = None
    refine_llm_backend: Literal["ollama", "openai"] | None = None
    # Muse's defaults (empty means the screen asks to choose)
    muse_model: str | None = None
    muse_workflow: str | None = None
    frozenset_classification: bool | None = None
    # GPU priority control
    auto_pause_on_generation: bool | None = None
    auto_pause_lanes: list[str] | None = None
    auto_alignment_evaluate: bool | None = None
    # Processing parallelism
    alignment_concurrency: Annotated[int, Field(ge=1, le=8)] | None = None
    pipeline_auto_continue: bool | None = None
    # Daily Oracle
    invoke_daily_oracle_enabled: bool | None = None
    invoke_daily_oracle_workflow: str | None = None
    invoke_daily_oracle_retain_days: Annotated[int, Field(ge=1, le=365)] | None = None
    invoke_daily_oracle_time: str | None = None
    invoke_daily_oracle_timezone: str | None = None
    invoke_daily_oracle_topic: str | None = None
    invoke_daily_oracle_min_free_gb: float | None = None
    backup_enabled: bool | None = None
    backup_time: str | None = None
    backup_timezone: str | None = None
    backup_retain_days: Annotated[int, Field(ge=1, le=90)] | None = None
    disk_caution_pct: Annotated[int, Field(ge=1, le=99)] | None = None
    disk_fault_pct: Annotated[int, Field(ge=1, le=99)] | None = None
    semantic_search_limit: Annotated[int, Field(ge=1, le=500)] | None = None
    muse_block_nsfw: bool | None = None


def _config_for_browser(cfg: dict) -> dict:
    out = dict(cfg)
    out["source_images_dir"] = str(settings.source_images_dir)
    out["generated_images_dir"] = str(settings.generated_images_dir)
    out["thumbnails_dir"] = str(settings.thumbnails_dir)
    # The key stays on the server; the screen only learns whether one is saved.
    out["external_llm_api_key_set"] = bool(out.get("external_llm_api_key"))
    out["external_llm_api_key"] = ""
    return out


@router.get("/config")
async def get_config(request: Request):
    db = request.app.state.db
    return _config_for_browser(await get_runtime_config(db))


async def _probe_embedding_width(ollama, model: str) -> int | None:
    """Embed one short string to find out how wide `model` actually is."""
    try:
        vec = await ollama.embed("dimension probe", model=model)
    except Exception:
        logger.warning("could not probe embedding width for %s", model, exc_info=True)
        return None
    return len(vec) if vec else None


@router.put("/config")
async def update_config(body: ConfigBody, request: Request):
    db = request.app.state.db
    existing = await db.get_config()
    updates = body.model_dump(exclude_none=True)
    if not updates.get("external_llm_api_key"):
        updates.pop("external_llm_api_key", None)
    if updates.pop("external_llm_api_key_clear", False):
        updates["external_llm_api_key"] = ""

    # Changing the embedding model is the one ordinary setting that can put the
    # collection and the vectors in it out of step. Check before saving, because
    # afterwards every embedding written is the wrong shape or the wrong space.
    warning = None
    new_model = updates.get("embed_model")
    if new_model and new_model != existing.get("embed_model"):
        width = await _probe_embedding_width(request.app.state.ollama, new_model)
        if width is not None and width != db.embed_dim:
            raise HTTPException(status_code=400, detail={
                "error": "embed_dim_mismatch",
                "model": new_model,
                "model_dim": width,
                "schema_dim": db.embed_dim,
                "message": (
                    f"'{new_model}' produces {width}-dimensional vectors, but the "
                    f"images collection holds {db.embed_dim}. Change the dimension "
                    f"first (Schema), which rebuilds the collection, then switch "
                    f"the model."
                ),
            })
        # Same width, different model: the old vectors are the right shape and
        # the wrong space. Nothing errors, searches just quietly get worse.
        warning = {
            "code": "reembed_required",
            "message": (
                f"The embedding model changed to '{new_model}'. Existing vectors "
                f"were produced by a different model, so search results will be "
                f"wrong until every image is re-embedded. Start a re-embed from "
                f"the AI screen — the app stays usable while it runs."
            ),
        }

    if "wd14_model_dir" in updates and updates["wd14_model_dir"] != existing.get("wd14_model_dir"):
        wd14_mod._session = None
        wd14_mod._tags_df = None
        wd14_mod._loaded_model_dir = None

    existing.update(updates)
    existing["_updated_at"] = time.time()
    # Settings that no longer exist are not written back, and are deleted by
    # name. The rest is merged, not replaced: other writers (UMAP, schema,
    # Invoke stats) put their own keys into the same document.
    legacy = [k for k in LEGACY_KEYS if existing.pop(k, None) is not None]
    await db.put_config(existing)
    if legacy:
        await db.delete_config_keys(legacy)
    invalidate_cache()

    # Hot-apply LLM connection settings without restart
    from ..ai.llm import LLM_RUNTIME_KEYS, apply_llm_runtime_config
    if LLM_RUNTIME_KEYS & updates.keys():
        apply_llm_runtime_config(request.app.state.ollama, await get_runtime_config(db))

    spooler = request.app.state.spooler

    # If pause settings changed, apply them immediately to the running spooler
    if "auto_pause_on_generation" in updates or "auto_pause_lanes" in updates or "eval_auto_pause" in updates:
        spooler.update_pause_settings(
            auto_pause_on_priority=existing.get("auto_pause_on_generation", True),
            auto_pause_target_lanes=existing.get("auto_pause_lanes", ["embed", "eval"]),
            eval_auto_pause=existing.get("eval_auto_pause", True),
        )

    # If disk thresholds changed, push to spooler immediately
    if "disk_caution_pct" in updates or "disk_fault_pct" in updates:
        spooler.set_disk_thresholds(
            existing.get("disk_caution_pct", 75),
            existing.get("disk_fault_pct", 90),
        )

    cfg = _config_for_browser(await get_runtime_config(db))
    if warning:
        cfg["warning"] = warning
    return cfg


class OpenAIModelsBody(BaseModel):
    base_url: str = ""
    # Empty → use the saved key.
    api_key: str = ""


@router.post("/openai/models")
async def external_llm_models(body: OpenAIModelsBody, request: Request):
    """Connection test for the external server: list its models.

    Takes the URL from the form, so it can be tried before saving.
    """
    url = body.base_url.strip()
    if not url:
        raise HTTPException(400, "base_url is required")
    cfg = await get_runtime_config(request.app.state.db)
    api_key = body.api_key or cfg.get("external_llm_api_key") or ""
    from ..ai.openai_compat import normalize_base_url
    external = request.app.state.ollama.external
    try:
        models = await external.list_models(url, api_key=api_key)
    except Exception as exc:
        return {"ok": False, "base_url": normalize_base_url(url), "models": [], "error": str(exc) or type(exc).__name__}
    return {"ok": True, "base_url": normalize_base_url(url), "models": models}


# ── Schema ────────────────────────────────────────────────────────────────────
#
# Vector width is recorded in Qdrant, not in the environment, and changing it
# rewrites every point in the collection. So it is not part of the ordinary
# settings save: it lives here, behind a typed confirmation, and runs as a job
# with progress rather than as a side effect of a process starting up.

SCHEMA_JOB_TITLE = "schema_apply"
CONFIRM_PHRASE = "confirm"


class SchemaApplyBody(BaseModel):
    embed_dim: Annotated[int, Field(ge=1)]
    embed_dim_small: Annotated[int, Field(ge=1)]
    confirm: str = ""
    restart: bool = False


def _running_schema_job(spooler):
    return next(
        (j for j in spooler.snapshot()
         if j.get("title") == SCHEMA_JOB_TITLE
         and j.get("state") in ("running", "queued")),
        None,
    )


@router.get("/schema/status")
async def schema_status(request: Request):
    db = request.app.state.db
    doc = await db.get_config()
    recorded = doc.get("schema") or {}
    job = _running_schema_job(request.app.state.spooler)
    return {
        "recorded": {k: recorded.get(k) for k in
                     ("embed_dim", "embed_dim_small", "embed_model", "seeded_at", "seeded_from")},
        "collection": db.schema_state,
        "obsolete_env": db.obsolete_env,
        "total_images": await db.total_count(),
        "job": {"id": job["id"], "progress": job["progress"],
                "progress_text": job.get("progress_text")} if job else None,
        "confirm_phrase": CONFIRM_PHRASE,
    }


@router.post("/schema/apply")
async def schema_apply(body: SchemaApplyBody, request: Request):
    """Rebuild the images collection at a new vector width.

    The confirmation is checked here, not only in the browser: an endpoint that
    trusts the client to have asked is an endpoint that can be called without
    asking.
    """
    if body.confirm != CONFIRM_PHRASE:
        raise HTTPException(status_code=400, detail={
            "error": "confirm_required",
            "message": f'Type "{CONFIRM_PHRASE}" to confirm.',
        })

    spooler = request.app.state.spooler
    running = _running_schema_job(spooler)
    if running and not body.restart:
        raise HTTPException(status_code=409, detail={
            "error": "already_running",
            "job_id": running["id"],
            "progress": running["progress"],
            "message": (
                "A schema change is already running. Changing the dimension now "
                "throws away the work done so far and starts again. Continue?"
            ),
        })
    if running:
        spooler.cancel(running["id"])

    from ..jobs.runners import run_schema_apply
    from ..spooler.models import JobLane
    job_id = spooler.submit(
        JobLane.SYNC, SCHEMA_JOB_TITLE, run_schema_apply,
        meta={"embed_dim": body.embed_dim, "embed_dim_small": body.embed_dim_small},
        db=request.app.state.db,
        ollama=request.app.state.ollama,
        spooler=spooler,
        embed_dim=body.embed_dim,
        embed_dim_small=body.embed_dim_small,
    )
    return {"status": "queued", "job_id": job_id, "restarted": bool(running)}


# ── Backup ────────────────────────────────────────────────────────────────────
#
# There is deliberately no "run a backup now" endpoint. Backups happen on the
# schedule, and immediately before a schema change; an ad-hoc trigger is an
# invitation to treat "I took a backup" as a substitute for having one, and it
# is one more unguarded route into the data.


class BackupRestoreBody(BaseModel):
    collection: str
    snapshot: str
    confirm: str = ""


@router.get("/backup/status")
async def backup_status(request: Request):
    from ..backup.service import list_backups
    db = request.app.state.db
    cfg = await get_runtime_config(db)
    spooler = request.app.state.spooler
    job = next(
        (j for j in spooler.snapshot()
         if j.get("title") == "backup" and j.get("state") in ("running", "queued")),
        None,
    )
    listing = await list_backups(db, str(cfg.get("backup_dir") or "/mnt/backup"))
    return {
        **listing,
        "enabled": bool(cfg.get("backup_enabled", True)),
        "time": cfg.get("backup_time"),
        "timezone": cfg.get("backup_timezone"),
        "retain": cfg.get("backup_retain_days"),
        "dir": cfg.get("backup_dir"),
        "running": bool(job),
        "confirm_phrase": CONFIRM_PHRASE,
    }


@router.post("/backup/restore")
async def backup_restore(body: BackupRestoreBody, request: Request):
    """Recover one collection from a snapshot. Replaces what is there now."""
    if body.confirm != CONFIRM_PHRASE:
        raise HTTPException(status_code=400, detail={
            "error": "confirm_required",
            "message": f'Type "{CONFIRM_PHRASE}" to confirm.',
        })
    from ..backup.service import _physical
    db = request.app.state.db
    # Snapshots are filed under the physical collection name even when they were
    # taken through an alias, so the path has to be resolved before it is built.
    physical = await _physical(db, body.collection)
    location = f"file:///qdrant/snapshots/{physical}/{body.snapshot}"
    try:
        await db._qc.recover_snapshot(
            collection_name=physical, location=location,
        )
    except Exception as e:
        logger.warning("snapshot restore failed", exc_info=True)
        raise HTTPException(status_code=500, detail={
            "error": "restore_failed", "message": str(e),
        }) from e
    return {"status": "restored", "collection": body.collection,
            "snapshot": body.snapshot}


@router.post("/backup/import-lineage")
async def backup_import_lineage(request: Request):
    """Fill in provenance and ratings the ledger has and the database lacks.

    Additive only — an existing value is never replaced — so this is safe to run
    at any time, and in particular after a heal scan has re-registered images
    that had been dropped.
    """
    from ..backup.service import import_lineage
    db = request.app.state.db
    cfg = await get_runtime_config(db)
    result = await import_lineage(db, str(cfg.get("backup_dir") or "/mnt/backup"))
    return {"status": "ok", **result}


# ── Stats ─────────────────────────────────────────────────────────────────────

def _thumbnail_usage(thumb_dir: Path) -> tuple[int, int]:
    """(count, bytes) of thumbnails. Blocking — a walk of the whole volume, kept
    off the event loop so opening the admin screen does not stall every request."""
    count = size = 0
    stack = [str(thumb_dir)] if thumb_dir.exists() else []
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.name.endswith(".webp"):
                            count += 1
                            size += e.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return count, size


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

    thumb_count, thumb_size = await asyncio.get_running_loop().run_in_executor(
        None, _thumbnail_usage, Path(settings.thumbnails_dir),
    )

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


# ── Character chemistry vectors ──────────────────────────────────────────────

@router.get("/character-compat/status")
async def character_compat_status(request: Request):
    from ..characters.compat import compat_status
    status = await compat_status(request.app.state.db)
    spooler = request.app.state.spooler
    backfill_job = next(
        (j for j in spooler.snapshot() if j.get("title") == "character_compat_backfill"),
        None,
    )
    status["backfill"] = (
        {
            "running": backfill_job["state"] == "running",
            "progress": backfill_job["progress"],
            "progress_text": backfill_job["progress_text"],
        }
        if backfill_job else {"running": False, "progress": 0.0, "progress_text": None}
    )
    return status


@router.get("/character-compat/matrix")
async def character_compat_matrix(request: Request):
    """Every pairwise chemistry score at once, for the viewer on the Characters screen."""
    from ..characters.compat import compat_matrix
    return await compat_matrix(request.app.state.db)


@router.post("/diaries/backfill-photos")
async def backfill_diary_photos(request: Request):
    """Reattach every final photo of a shoot to the diary page it belongs to.

    Pages written before the session kept its finished takes carry only the
    take the showrunner stopped on. The photos themselves know which shoot they
    came from, so nothing was lost — it just had to be asked for. Runs inline:
    it is a payload rewrite over a few dozen pages, not a render.
    """
    from ..characters import presets as presets_db
    return await presets_db.backfill_diary_photos(request.app.state.db)


@router.post("/character-compat/backfill")
async def start_character_compat_backfill(request: Request):
    """Embed every character still missing appearance/personality vectors."""
    from ..characters.compat import run_character_compat_backfill
    from ..spooler.models import JobLane
    spooler = request.app.state.spooler
    job_id = spooler.submit(
        JobLane.EMBEDDING, "character_compat_backfill", run_character_compat_backfill,
        db=request.app.state.db, ollama=request.app.state.ollama,
    )
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


@router.post("/model-name/backfill")
async def start_model_name_backfill(request: Request):
    from ..jobs.runners import run_model_name_backfill
    from ..spooler.models import JobLane
    spooler = request.app.state.spooler
    db = request.app.state.db
    job_id = spooler.submit(JobLane.SYNC, "model_name_backfill", run_model_name_backfill, db=db)
    return {"status": "queued", "job_id": job_id}


# ── Duplicate Detection ───────────────────────────────────────────────────────

@router.get("/duplicates")
async def find_duplicates(request: Request):
    """Files with identical bytes at more than one path.

    The scanner records every copy on the image's point (`dup_paths`), so this
    reads Qdrant instead of hashing the whole library again.
    """
    db = request.app.state.db
    groups = []
    for doc in await db.scroll_dup_groups():
        primary = doc.get("path") or ""
        groups.append({
            "sha256": doc.get("sha256", ""),
            "registered_path": primary,
            "registered_name": Path(primary).name,
            "copies": [
                {"path": d["path"], "name": Path(d["path"]).name, "size": d.get("size") or 0}
                for d in doc.get("dup_paths") or [] if d.get("path")
            ],
        })
    groups.sort(key=lambda g: g["registered_name"])

    dirs, _ = await db.load_scan_dirs()
    total_registered = await db.total_count()
    return {
        "total_files_on_disk": sum(int(e.get("n_images") or 0) for e in dirs.values()),
        "total_registered": total_registered,
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


# ── Path migration ────────────────────────────────────────────────────────────

class MigratePathsBody(BaseModel):
    old_prefix: str
    new_prefix: str


@router.post("/migrate-paths")
async def migrate_paths(body: MigratePathsBody, request: Request):
    """Rewrite stored image paths when the mount-point or base directory changes.

    Replaces old_prefix with new_prefix in every image's stored path and
    recomputes is_reference.  Run this BEFORE triggering a heal scan after
    changing source_images_dir / generated_images_dir mounts.
    """
    if not body.old_prefix or not body.new_prefix:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="old_prefix and new_prefix must not be empty")
    db = request.app.state.db
    updated = await db.migrate_path_prefix(body.old_prefix, body.new_prefix)
    return {"updated": updated}


# ── Cleanup ───────────────────────────────────────────────────────────────────

@router.post("/cleanup/orphan-alignments")
async def cleanup_orphan_alignments(request: Request):
    """Remove alignment records for images that no longer exist in the database."""
    db = request.app.state.db
    removed = await db.cleanup_orphan_alignments()
    return {"removed": removed}


# ── Invoke Vocab (WD14 → Qdrant) ─────────────────────────────────────────────

@router.get("/invoke/vocab-status")
async def invoke_vocab_status(request: Request):
    db = request.app.state.db
    count = await db.count_wd14_vocab()
    return {"imported": count > 0, "tag_count": count}


@router.delete("/invoke/daily-oracle")
async def delete_daily_oracle(request: Request, date: str | None = None):
    """Delete daily oracle images for a given date (defaults to today in configured TZ)."""
    from ..api.invoke import _oracle_date_str
    from ..runtime_config import get_runtime_config
    db = request.app.state.db
    cfg = await get_runtime_config(db)
    target_date = date or _oracle_date_str(cfg)
    deleted = await db.delete_daily_oracle(target_date)
    return {"deleted": deleted, "date": target_date}


@router.post("/invoke/import-wd14-vocab")
async def invoke_import_wd14_vocab(request: Request):
    """Parse selected_tags.csv from wd14_model_dir, embed with Ollama, upsert to Qdrant."""
    from ..jobs.runners import run_import_wd14_vocab
    from ..spooler.models import JobLane
    spooler = request.app.state.spooler
    db = request.app.state.db
    ollama = request.app.state.ollama
    job_id = spooler.submit(
        JobLane.SYNC,
        "import_wd14_vocab",
        run_import_wd14_vocab,
        db=db,
        ollama=ollama,
    )
    return {"status": "queued", "job_id": job_id}


