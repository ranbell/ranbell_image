"""Muse capability catalog: workflows, LLMs, presets, and run defaults.

One call the panel makes on open, so the UI can populate every picker and warn
about anything missing before the user spends a generation on it.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from .defaults import ALL_DEFAULTS

logger = logging.getLogger(__name__)

GetRuntimeConfigFn = Callable[[Any], Awaitable[dict[str, Any]]]
ListAuthorsFn = Callable[[Any], Awaitable[list[dict[str, Any]]]]


def _model_available(model: str, models: list[str]) -> bool:
    if not model:
        return False
    prefix = model.split(":")[0]
    return any(m == model or m.startswith(prefix) for m in models)


async def build_muse_catalog(
    app,
    *,
    get_runtime_config_fn: GetRuntimeConfigFn | None = None,
    list_authors_fn: ListAuthorsFn | None = None,
    comfy_url: str = "",
) -> dict[str, Any]:
    """Aggregate workflows, LLM lists, presets, and suggested run defaults.

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
        logger.warning("[muse.catalog] runtime_config failed: %s", exc)

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
        logger.warning("[muse.catalog] workflows failed: %s", exc)

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
        if list_fn:
            try:
                ollama_models = list(await list_fn(ollama_url or None) or [])
                ollama_ok = ollama_ok or bool(ollama_models)
            except Exception:
                pass
    except Exception as exc:
        logger.warning("[muse.catalog] ollama models failed: %s", exc)

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
        logger.warning("[muse.catalog] openai models failed: %s", exc)

    # ── Authors (optional flavour text for the scene description) ───────────
    authors: list[dict] = []
    try:
        if list_authors_fn is not None:
            rows = await list_authors_fn(db)
        else:
            from ..authors import authors as authors_db
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
        logger.warning("[muse.catalog] authors failed: %s", exc)

    # ── The tag engine's own prerequisite ───────────────────────────────────
    # Every vocab_bank retrieval returns [] when wd14_vocab is empty, which
    # would look like "the LLM gave bad tags" rather than "nothing was imported".
    vocab_count = 0
    try:
        vocab_count = int(await db.count_wd14_vocab())
    except Exception as exc:
        logger.warning("[muse.catalog] wd14_vocab count failed: %s", exc)

    character_count = 0
    try:
        from ..characters import presets as presets_db
        character_count = len(await presets_db.list_presets(db, limit=500))
    except Exception as exc:
        logger.warning("[muse.catalog] presets count failed: %s", exc)

    provider = str(cfg.get("llm_provider") or "ollama")
    admin_vlm = (cfg.get("vlm_model") or "").strip()
    admin_utility = (cfg.get("utility_model") or "").strip()
    # Muse's LLM calls are small (a theme split, a two-sentence description), so
    # the light utility model is the right default; VLM is only the fallback.
    suggested_model = admin_utility or admin_vlm
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
            "ollama": {"ok": ollama_ok, "url": ollama_url, "models": ollama_models},
            "openai": {
                "ok": openai_ok,
                "url": openai_url,
                "models": openai_models,
                "default_model": (cfg.get("openai_model") or "").strip(),
            },
            "providers": ["ollama", "openai"],
        },
        "authors": authors,
        "wd14_vocab": {"count": vocab_count, "imported": vocab_count > 0},
        "characters": {"count": character_count},
        "locales": ["ja", "en"],
        "admin_defaults": {
            "llm_provider": provider,
            "vlm_model": admin_vlm,
            "utility_model": admin_utility,
            "embed_model": (cfg.get("embed_model") or "").strip(),
            "ollama_num_ctx": cfg.get("ollama_num_ctx"),
            "wd14_threshold": cfg.get("wd14_threshold"),
        },
        "suggested_run": {
            "llm_provider": provider if provider in ("ollama", "openai") else "ollama",
            "light_model": suggested_model,
            "light_model_available": available,
            "board_workflow": suggested_workflow,
            "final_workflow": suggested_workflow,
            "locale": "ja",
            **ALL_DEFAULTS,
        },
        "notes": {
            "wd14_vocab_required": (
                "Tag retrieval reads the wd14_vocab collection. When it is empty "
                "every step returns no tags — run POST /api/admin/invoke/import-wd14-vocab."
            ),
            "workflow_required_for_images": (
                "board_workflow and final_workflow are required for image jobs. "
                "Point board_workflow at a cheap/fast model when you have one."
            ),
            "board_steps": (
                "steps=2 only works on a step-distilled model (Turbo/Lightning). "
                "On an ordinary model use 12-20 or the board is unreadable."
            ),
            "model_pool_for_provider": model_pool,
        },
        "endpoints": {
            "catalog": "GET /api/muse/catalog",
            "characters": "GET /api/characters",
            "sessions": "POST /api/muse/sessions",
            "session": "GET /api/muse/sessions/{session_id}",
            "patch_inputs": "PATCH /api/muse/sessions/{session_id}/inputs",
            "split": "POST /api/muse/sessions/{session_id}/split",
            "tags": "POST /api/muse/sessions/{session_id}/tags",
            "board": "POST /api/muse/sessions/{session_id}/board",
            "harvest": "POST /api/muse/sessions/{session_id}/harvest",
            "merge": "POST /api/muse/sessions/{session_id}/merge",
            "brainstorm": "POST /api/muse/sessions/{session_id}/brainstorm",
            "render": "POST /api/muse/sessions/{session_id}/render",
            "stream": "GET /api/muse/sessions/{session_id}/stream",
            "workflows_only": "GET /api/comfy/workflows",
            "ollama_models_only": "GET /api/ollama/models",
            "authors": "GET /api/authors",
        },
    }
