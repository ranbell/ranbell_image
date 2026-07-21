"""Agent-facing Chronicle capability catalog (workflows, LLMs, defaults)."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Keep in sync with story.generator.TIME_SCALES keys.
TIME_SCALE_KEYS = (
    "minutes",
    "tens_of_minutes",
    "hours",
    "days",
    "months",
    "years",
    "decades",
)

GetRuntimeConfigFn = Callable[[Any], Awaitable[dict[str, Any]]]
ListAuthorsFn = Callable[[Any], Awaitable[list[dict[str, Any]]]]


def _model_available(model: str, models: list[str]) -> bool:
    if not model:
        return False
    prefix = model.split(":")[0]
    return any(m == model or m.startswith(prefix) for m in models)


async def build_chronicle_catalog(
    app,
    *,
    get_runtime_config_fn: GetRuntimeConfigFn | None = None,
    list_authors_fn: ListAuthorsFn | None = None,
    comfy_url: str = "",
) -> dict[str, Any]:
    """Aggregate workflows, LLM lists, authors, and suggested run defaults.

    Optional ``*_fn`` hooks are for unit tests; production leaves them unset.
    """
    db = app.state.db
    llm = app.state.ollama
    comfy = app.state.comfy

    cfg: dict[str, Any] = {}
    try:
        if get_runtime_config_fn is not None:
            cfg = await get_runtime_config_fn(db)
        else:
            from ..runtime_config import get_runtime_config
            cfg = await get_runtime_config(db)
    except Exception as exc:
        logger.warning("[chronicle_catalog] runtime_config failed: %s", exc)

    # ── Workflows (local filesystem; independent of Comfy online) ───────────
    workflows: list[str] = []
    comfy_ok = False
    resolved_comfy_url = comfy_url
    try:
        if not resolved_comfy_url:
            from ..config import settings
            resolved_comfy_url = str(settings.comfyui_url or "")
        if comfy is not None:
            workflows = list(comfy.list_workflows() or [])
            try:
                comfy_ok = bool(await comfy.is_available())
            except Exception:
                comfy_ok = False
    except Exception as exc:
        logger.warning("[chronicle_catalog] workflows failed: %s", exc)

    # ── Ollama models ───────────────────────────────────────────────────────
    ollama_models: list[str] = []
    ollama_ok = False
    ollama_url = str(cfg.get("ollama_url") or "")
    try:
        health_fn = getattr(llm, "health_ollama", None)
        list_fn = getattr(llm, "list_ollama_models", None) or getattr(llm, "list_models", None)
        if health_fn:
            ollama_ok = bool(await health_fn(ollama_url or None))
        else:
            ollama_ok = True
        if list_fn and ollama_ok:
            ollama_models = list(await list_fn(ollama_url or None) or [])
        elif list_fn:
            try:
                ollama_models = list(await list_fn(ollama_url or None) or [])
                ollama_ok = bool(ollama_models)
            except Exception:
                pass
    except Exception as exc:
        logger.warning("[chronicle_catalog] ollama models failed: %s", exc)

    # ── OpenAI-compatible models ────────────────────────────────────────────
    openai_models: list[str] = []
    openai_ok = False
    openai_url = str(cfg.get("openai_base_url") or "")
    try:
        health_fn = getattr(llm, "health_openai", None)
        list_fn = getattr(llm, "list_openai_models", None)
        if health_fn:
            openai_ok = bool(await health_fn(openai_url or None))
        if list_fn:
            try:
                openai_models = list(await list_fn(openai_url or None) or [])
                if openai_models:
                    openai_ok = True
            except Exception:
                pass
    except Exception as exc:
        logger.warning("[chronicle_catalog] openai models failed: %s", exc)

    # ── Authors ─────────────────────────────────────────────────────────────
    authors: list[dict] = []
    try:
        if list_authors_fn is not None:
            rows = await list_authors_fn(db)
        else:
            from . import authors as authors_db
            rows = await authors_db.list_authors(db)
        authors = [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "genre_tag": r.get("genre_tag") or "",
                "style_description": r.get("style_description") or "",
            }
            for r in (rows or [])
        ]
    except Exception as exc:
        logger.warning("[chronicle_catalog] authors failed: %s", exc)

    provider = str(cfg.get("llm_provider") or "ollama")
    admin_story = (cfg.get("story_model") or "").strip()
    admin_vlm = (cfg.get("vlm_model") or "").strip()
    admin_utility = (cfg.get("utility_model") or "").strip()
    # Chronicle panel does NOT fall back to Admin — agents must pass story_model.
    suggested_model = admin_story or admin_vlm or admin_utility
    if provider == "openai":
        suggested_model = (
            (cfg.get("openai_model") or "").strip()
            or suggested_model
            or (openai_models[0] if openai_models else "")
        )
        available = _model_available(suggested_model, openai_models)
        model_pool = openai_models
    else:
        if not suggested_model and ollama_models:
            suggested_model = ollama_models[0]
        available = _model_available(suggested_model, ollama_models)
        model_pool = ollama_models

    suggested_workflow = workflows[0] if workflows else ""

    return {
        "ok": True,
        "comfyui": {
            "ok": comfy_ok,
            "url": resolved_comfy_url,
            "workflows": workflows,
        },
        "llm": {
            "ollama": {
                "ok": ollama_ok,
                "url": ollama_url,
                "models": ollama_models,
            },
            "openai": {
                "ok": openai_ok,
                "url": openai_url,
                "models": openai_models,
                "default_model": (cfg.get("openai_model") or "").strip(),
            },
            "providers": ["ollama", "openai"],
        },
        "authors": authors,
        "time_scales": list(TIME_SCALE_KEYS),
        "locales": ["ja", "en"],
        "admin_defaults": {
            "llm_provider": provider,
            "vlm_model": admin_vlm,
            "story_model": admin_story,
            "utility_model": admin_utility,
            "embed_model": (cfg.get("embed_model") or "").strip(),
            "ollama_num_ctx": cfg.get("ollama_num_ctx"),
        },
        "suggested_run": {
            "llm_provider": provider if provider in ("ollama", "openai") else "ollama",
            "story_model": suggested_model,
            "story_model_available": available,
            "vlm_model": suggested_model,
            "workflow_name": suggested_workflow,
            "locale": "ja",
            "time_scale": "days",
            "candidate_id": "A",
            "temperature": 0.7,
            "num_ctx": int(cfg.get("ollama_num_ctx") or 32768),
            "wait_images": True,
            "export": True,
            "manual_mode": False,
            "use_ref_seed": True,
        },
        "notes": {
            "story_model_required": (
                "Chronicle does not fall back to Admin models. "
                "Pass story_model (or vlm_model) explicitly on every run."
            ),
            "workflow_required_for_images": (
                "workflow_name is required unless manual_mode=true "
                "(then call generate-images later)."
            ),
            "topic_or_base": "Provide user_topic and/or base_sha256.",
            "model_pool_for_provider": model_pool,
        },
        "endpoints": {
            "catalog": "GET /api/story/chronicle/catalog",
            "run": "POST /api/story/chronicle/run",
            "run_status": "GET /api/story/chronicle/run/{run_id}",
            "eval_bundle": "GET /api/story/{story_id}/eval-bundle",
            "export_eval": "POST /api/story/{story_id}/export-eval",
            "workflows_only": "GET /api/comfy/workflows",
            "ollama_models_only": "GET /api/ollama/models",
            "openai_models_only": "GET /api/llm/models",
            "authors": "GET /api/authors",
        },
    }
