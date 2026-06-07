import asyncio
import logging
from fastapi import APIRouter, Request
from ..config import settings
from ..runtime_config import get_runtime_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.get("/health/detail")
async def detailed_health(request: Request):
    db = request.app.state.db
    ollama = request.app.state.ollama

    async def check_qdrant():
        try:
            count = await db.total_count()
            vector_count = await db.count_with_embedding()
            info = await db._qc.get_collection(collection_name="images")
            return {
                "ok": True,
                "doc_count": count,
                "vector_count": vector_count,
                "url": settings.qdrant_url,
            }
        except Exception as e:
            logger.error("Qdrant health check failed: %s", e)
            return {"ok": False, "error": "接続エラー", "url": settings.qdrant_url}

    async def check_ollama():
        try:
            cfg = await get_runtime_config(db)
            ok = await ollama.health()
            models = await ollama.list_models() if ok else []
            embed_model = cfg["embed_model"]
            vlm_model = cfg["vlm_model"]
            return {
                "ok": ok,
                "url": cfg["ollama_url"],
                "models": models,
                "embed_model": embed_model,
                "embed_model_available": any(
                    m == embed_model or m.startswith(embed_model.split(":")[0])
                    for m in models
                ),
                "vlm_model": vlm_model,
                "vlm_model_available": any(
                    m == vlm_model or m.startswith(vlm_model.split(":")[0])
                    for m in models
                ),
            }
        except Exception as e:
            logger.error("Ollama health check failed: %s", e)
            return {"ok": False, "error": "接続エラー", "url": settings.ollama_url, "models": []}

    async def check_comfy():
        try:
            c = request.app.state.comfy
            ok = await c.is_available()
            workflows = c.list_workflows() if ok else []
            return {
                "ok": ok,
                "url": settings.comfyui_url,
                "workflows_dir": settings.comfyui_workflows_dir,
                "workflows": workflows,
            }
        except Exception as e:
            logger.error("ComfyUI health check failed: %s", e)
            return {"ok": False, "error": "接続エラー", "url": settings.comfyui_url, "workflows": []}

    qdrant_res, ollama_res, comfy_res = await asyncio.gather(
        check_qdrant(), check_ollama(), check_comfy()
    )

    return {
        "backend": {"ok": True, "version": "0.4.0"},
        "qdrant": qdrant_res,
        "ollama": ollama_res,
        "comfyui": comfy_res,
    }


@router.get("/ollama/models")
async def ollama_models(request: Request):
    ollama = request.app.state.ollama
    try:
        models = await ollama.list_models()
        return {"models": models}
    except Exception:
        return {"models": []}
