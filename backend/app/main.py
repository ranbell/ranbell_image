from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings

from .db.qdrant_client import QdrantDBClient
from .ai.ollama import OllamaClient
from .ai.comfy import ComfyUIClient
from .core.runtime_cache import RuntimeConfigCache
from . import runtime_config as _runtime_config
from .scanner.watcher import ImageDirectoryWatcher
from .spooler.spooler import JobSpooler
from .spooler.resources import build_resources
from .api.images import router as images_router
from .api.info import router as info_router
from .api.scan import router as scan_router
from .api.ai import router as ai_router
from .api.health import router as health_router
from .api.admin import router as admin_router
from .api.comfy import router as comfy_router
from .api.inspire import router as inspire_router
from .api.jobs import router as jobs_router
from .api.analyzer import router as analyzer_router
from .api.alignment import router as alignment_router


def _check_generated_dir(warnings: list[str]) -> None:
    """Abort startup if generated_images_dir is missing or not writable."""
    import os, tempfile
    from .config import settings
    gen_dir = settings.generated_images_dir
    if not gen_dir.exists():
        raise RuntimeError(
            f"generated_images_dir '{gen_dir}' does not exist. "
            "Mount a writable host directory to this path in docker-compose.override.yml and restart."
        )
    if not os.path.ismount(gen_dir):
        warnings.append(
            f"generated_images_dir '{gen_dir}' is not a mount point — "
            "data will be lost on container restart. "
            "Mount a host directory to this path in docker-compose.override.yml."
        )
    try:
        with tempfile.NamedTemporaryFile(dir=gen_dir, delete=True):
            pass
    except Exception as e:
        raise RuntimeError(
            f"generated_images_dir '{gen_dir}' is not writable: {e}. "
            "Mount it as a read-write volume and restart."
        ) from e


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    startup_warnings: list[str] = []
    _check_generated_dir(startup_warnings)
    if not settings.source_images_dir.exists():
        startup_warnings.append(
            f"source_images_dir '{settings.source_images_dir}' does not exist — no images will be found. "
            "Mount a source directory in docker-compose.override.yml."
        )
    app.state.startup_warnings = startup_warnings

    db = QdrantDBClient()
    await db.start()

    ollama = OllamaClient()
    comfy = ComfyUIClient()

    from .config import settings as _settings
    resources, lane_resource = build_resources(_settings)
    spooler = JobSpooler(resources=resources, lane_resource=lane_resource)

    app.state.db = db
    app.state.ollama = ollama
    app.state.comfy = comfy
    app.state.spooler = spooler

    runtime_config_cache = RuntimeConfigCache(ttl_seconds=30.0)
    app.state.runtime_config_cache = runtime_config_cache
    _runtime_config.set_cache(runtime_config_cache)
    app.state.ready = True
    app.state.refine_token_queues: dict[str, asyncio.Queue] = {}
    app.state.inspire_event_queues: dict[str, asyncio.Queue] = {}

    asyncio.ensure_future(db.backfill_model_name())

    await spooler.start()

    # On startup: apply pause settings saved in the DB to the spooler
    from .runtime_config import _defaults as _rc_defaults
    _saved_cfg = await db.get_config()
    spooler.update_pause_settings(
        auto_pause_on_priority=_saved_cfg.get(
            "auto_pause_on_generation", _rc_defaults["auto_pause_on_generation"]
        ),
        auto_pause_target_lanes=_saved_cfg.get(
            "auto_pause_lanes", _rc_defaults["auto_pause_lanes"]
        ),
    )

    watcher = ImageDirectoryWatcher(
        db, ollama, spooler,
        debounce_seconds=_settings.watch_debounce_seconds,
        auto_ai_pipeline=_settings.auto_ai_pipeline,
    )
    watcher.start(_settings.source_images_dir, _settings.generated_images_dir)
    app.state.watcher = watcher

    yield

    watcher.stop()
    await spooler.stop()
    await comfy.close()
    await db.close()
    await ollama.close()


app = FastAPI(title="Ranbell Image", version="0.5.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def verify_api_token(request: Request, call_next):
    # Exempt probes and the public token endpoint
    if request.url.path in {"/api/health", "/api/token"}:
        return await call_next(request)
    if request.url.path.startswith("/api"):
        token = (
            request.headers.get("X-API-Token")
            or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            or request.query_params.get("token", "")
            or request.cookies.get("api_token", "")
        )
        if token != settings.api_token:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)

app.include_router(images_router)
app.include_router(info_router)
app.include_router(scan_router)
app.include_router(ai_router)
app.include_router(health_router)
app.include_router(admin_router)
app.include_router(comfy_router)
app.include_router(inspire_router)
app.include_router(jobs_router)
app.include_router(analyzer_router)
app.include_router(alignment_router)


@app.get("/api/token")
async def get_token():
    return {"token": settings.api_token}


@app.get("/api/health")
async def health(request: Request):
    ready = getattr(request.app.state, "ready", False)
    result: dict = {"status": "ok" if ready else "starting", "ready": ready}
    result["warnings"] = getattr(request.app.state, "startup_warnings", [])
    if ready:
        spooler = request.app.state.spooler
        running_jobs = [
            j for j in spooler.snapshot()
            if j["state"] in ("running", "cancelling")
        ]
        result["jobs"] = {
            "running_count": len(running_jobs),
            "running": [{
                "id": j["id"],
                "lane": j["lane"],
                "title": j["title"],
                "progress": j["progress"],
            } for j in running_jobs],
        }
    return result
