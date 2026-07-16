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
            return {
                "ok": ok,
                "url": runtime_url,
                "models": models,
                "embed_model": embed_model,
                "embed_model_available": _model_available(embed_model, models),
            }
        except Exception as e:
            logger.error("Ollama health check failed: %s", e)
            return {"ok": False, "error": "接続エラー", "url": settings.ollama_url, "models": []}

    async def check_llm():
        """Active text/VLM provider (Ollama or OpenAI-compatible)."""
        try:
            cfg = await get_runtime_config(db)
            provider = cfg.get("llm_provider") or "ollama"
            vlm_model = cfg["vlm_model"]
            if provider == "openai":
                url = cfg.get("openai_base_url") or settings.openai_base_url
                health_fn = getattr(llm, "health_openai", llm.health)
                list_fn = getattr(llm, "list_openai_models", llm.list_models)
                ok = await health_fn(url)
                models = await list_fn(url) if ok else []
            else:
                url = cfg["ollama_url"]
                health_fn = getattr(llm, "health_ollama", llm.health)
                list_fn = getattr(llm, "list_ollama_models", llm.list_models)
                ok = await health_fn(url)
                models = await list_fn(url) if ok else []
            # llama-server (Bonsai) often accepts any model id for the one loaded GGUF.
            if provider == "openai":
                vlm_ok = ok and (
                    not models
                    or _model_available(vlm_model, models)
                    or bool(vlm_model)
                )
            else:
                vlm_ok = _model_available(vlm_model, models)
            return {
                "ok": ok,
                "provider": provider,
                "url": url,
                "models": models,
                "vlm_model": vlm_model,
                "vlm_model_available": bool(vlm_ok),
            }
        except Exception as e:
            logger.error("LLM health check failed: %s", e)
            return {
                "ok": False,
                "error": "接続エラー",
                "provider": settings.llm_provider,
                "url": settings.openai_base_url,
                "models": [],
            }

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

    qdrant_res, ollama_res, llm_res, comfy_res = await asyncio.gather(
        check_qdrant(), check_ollama(), check_llm(), check_comfy()
    )

    # Backward-compatible ollama block: merge embed + active VLM status
    ollama_merged = {
        **ollama_res,
        "provider": llm_res.get("provider", "ollama"),
        "vlm_model": llm_res.get("vlm_model"),
        "vlm_model_available": llm_res.get("vlm_model_available"),
        "llm_url": llm_res.get("url"),
        "llm_ok": llm_res.get("ok"),
        "llm_models": llm_res.get("models", []),
    }

    return {
        "backend": {"ok": True, "version": "0.3.1"},
        "qdrant": qdrant_res,
        "ollama": ollama_merged,
        "llm": llm_res,
        "comfyui": comfy_res,
    }


@router.get("/ollama/models")
async def ollama_models(request: Request):
    """Embedding-model list from Ollama (always)."""
    llm = request.app.state.ollama
    try:
        list_fn = getattr(llm, "list_ollama_models", llm.list_models)
        models = await list_fn()
        return {"models": models}
    except Exception:
        return {"models": []}


@router.get("/llm/models")
async def llm_models(request: Request):
    """Text/VLM models for the active provider (Ollama or OpenAI-compat)."""
    llm = request.app.state.ollama
    db = request.app.state.db
    try:
        cfg = await get_runtime_config(db)
        provider = cfg.get("llm_provider") or "ollama"
        if provider == "openai":
            list_fn = getattr(llm, "list_openai_models", llm.list_models)
            models = await list_fn(cfg.get("openai_base_url"))
        else:
            list_fn = getattr(llm, "list_ollama_models", llm.list_models)
            models = await list_fn(cfg.get("ollama_url"))
        return {"provider": provider, "models": models}
    except Exception:
        return {"provider": "ollama", "models": []}
