import asyncio
import logging
from fastapi import APIRouter, Request
from ..config import settings
from ..runtime_config import get_runtime_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _model_available(model: str, models: list[str]) -> bool:
    if not model:
        return False
    prefix = model.split(":")[0]
    return any(m == model or m.startswith(prefix) for m in models)


@router.get("/health/detail")
async def detailed_health(request: Request):
    db = request.app.state.db
    llm = request.app.state.ollama

    async def check_qdrant():
        try:
            count = await db.total_count()
            vector_count = await db.count_with_embedding()
            info = await db._qc.get_collection(collection_name="images")
            _ = info
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
        """Ollama is always required for embeddings."""
        try:
            cfg = await get_runtime_config(db)
            runtime_url = cfg["ollama_url"]
            health_fn = getattr(llm, "health_ollama", None)
            list_fn = getattr(llm, "list_ollama_models", None)
            if health_fn:
                ok = await health_fn(runtime_url)
                models = await list_fn(runtime_url) if ok and list_fn else []
            else:
                ok = await llm.health(runtime_url)
                models = await llm.list_models(runtime_url) if ok else []
            embed_model = cfg["embed_model"]
            vlm_model = cfg["vlm_model"]
            return {
                "ok": ok,
                "url": runtime_url,
                "models": models,
                "embed_model": embed_model,
                "embed_model_available": _model_available(embed_model, models),
                "vlm_model": vlm_model,
                "vlm_model_available": _model_available(vlm_model, models),
            }
        except Exception as e:
            logger.error("Ollama health check failed: %s", e)
            return {"ok": False, "error": "接続エラー", "url": settings.ollama_url, "models": []}

    async def check_comfy():
        try:
            c = request.app.state.comfy
            ok = await c.is_available()
            workflows = c.list_workflows()  # reads local filesystem, independent of connection
            return {
                "ok": ok,
                "url": settings.comfyui_url,
                "workflows_dir": settings.comfyui_workflows_dir,
                "workflows": workflows,
            }
        except Exception as e:
            logger.error("ComfyUI health check failed: %s", e)
            c = getattr(request.app.state, "comfy", None)
            workflows = c.list_workflows() if c else []
            return {"ok": False, "error": "接続エラー", "url": settings.comfyui_url, "workflows": workflows}

    qdrant_res, ollama_res, comfy_res = await asyncio.gather(
        check_qdrant(), check_ollama(), check_comfy()
    )

    return {
        "backend": {"ok": True, "version": "0.3.1"},
        "qdrant": qdrant_res,
        "ollama": {**ollama_res, "provider": "ollama"},
        "comfyui": comfy_res,
    }


@router.get("/ollama/models")
async def ollama_models(request: Request):
    """Embedding-model list from Ollama (always).

    ``vision_models`` is the subset that accepts images, so the config UI can
    warn before a text-only model is picked for a job that sends references.
    """
    llm = request.app.state.ollama
    try:
        list_fn = getattr(llm, "list_ollama_models", llm.list_models)
        models = await list_fn()
    except Exception:
        return {"models": [], "vision_models": []}
    try:
        vision_fn = getattr(llm, "vision_ollama_models", None) or getattr(
            llm, "vision_models", None
        )
        vision = await vision_fn() if vision_fn else []
    except Exception:
        vision = []
    return {"models": models, "vision_models": vision}
