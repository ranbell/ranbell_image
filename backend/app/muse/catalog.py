"""Muse capability catalog: workflows, models, presets, and run defaults.

One call the panel makes on open, so every picker is populated and anything
missing is reported before a generation is spent finding out.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from .defaults import ALL_DEFAULTS

logger = logging.getLogger(__name__)

GetRuntimeConfigFn = Callable[[Any], Awaitable[dict[str, Any]]]


def _model_available(model: str, models: list[str]) -> bool:
    if not model:
        return False
    prefix = model.split(":")[0]
    return any(m == model or m.startswith(prefix) for m in models)


async def build_muse_catalog(
    app,
    *,
    get_runtime_config_fn: GetRuntimeConfigFn | None = None,
    comfy_url: str = "",
) -> dict[str, Any]:
    """Aggregate workflows, model lists, presets and suggested run defaults.

    The optional ``*_fn`` hook is for unit tests; production leaves it unset.
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

    # ── Workflows (local filesystem; independent of Comfy being online) ──────
    workflows: list[str] = []
    workflow_caps: list[dict[str, Any]] = []
    comfy_ok = False
    resolved_comfy_url = comfy_url
    try:
        if not resolved_comfy_url:
            from ..config import settings
            resolved_comfy_url = str(settings.comfyui_url or "")
        if comfy is not None:
            workflows = list(comfy.list_workflows() or [])
            for name in workflows:
                cap: dict[str, Any] = {
                    "name": name,
                    "has_openpose": False,
                    "can_inject_image": False,
                }
                try:
                    wf = comfy.load_workflow(name)
                    info = comfy.inspect_workflow(wf)
                    cap["has_openpose"] = bool(info.get("has_openpose"))
                    cap["can_inject_image"] = bool(info.get("can_inject_image"))
                except Exception:
                    pass
                workflow_caps.append(cap)
            try:
                comfy_ok = bool(await comfy.is_available())
            except Exception:
                comfy_ok = False
    except Exception as exc:
        logger.warning("[muse.catalog] workflows failed: %s", exc)

    # ── Models ──────────────────────────────────────────────────────────────
    # Three of the four stages hand the model an image. Ollama does not fail on
    # a text-only model given images — it drops them and answers from the text
    # alone, which reads as "the chain stopped improving" rather than as an
    # error. So the vision-capable subset is reported separately and is what the
    # suggestion comes from.
    models: list[str] = []
    vision: list[str] = []
    ollama_ok = False
    ollama_url = str(cfg.get("ollama_url") or "")
    try:
        health_fn = getattr(llm, "health_ollama", None) or getattr(llm, "health", None)
        if health_fn:
            ollama_ok = bool(await health_fn(ollama_url or None))
        list_fn = getattr(llm, "list_ollama_models", None) or getattr(llm, "list_models", None)
        if list_fn:
            models = list(await list_fn(ollama_url or None) or [])
            ollama_ok = ollama_ok or bool(models)
        # The gateway names it per provider; the bare client does not.
        vision_fn = (getattr(llm, "vision_ollama_models", None)
                     or getattr(llm, "vision_models", None))
        if vision_fn:
            vision = list(await vision_fn(ollama_url or None) or [])
    except Exception as exc:
        logger.warning("[muse.catalog] model list failed: %s", exc)

    character_count = 0
    try:
        from ..characters import presets as presets_db
        character_count = len(await presets_db.list_presets(db, limit=500))
    except Exception as exc:
        logger.warning("[muse.catalog] presets count failed: %s", exc)

    admin_vlm = (cfg.get("vlm_model") or "").strip()
    # **管理画面が決めた既定だけを「既定」と呼ぶ（2026-09-13）。**
    #
    # 総監督「使用する llm・画像モデルも空にして、実行前に選択するように。
    # 管理画面でデフォルト決めていたら、そのデフォルト値を使用して開始できるように」。
    # `suggested_*` は**一覧の先頭**を当てていたので、画面はそれを既定として使い、
    # 総監督が選ばないまま撮影が始まっていた。先頭当ては残す（外の呼び元が読む）が、
    # 画面が見るのは下の `admin_defaults` のほうにする。
    admin_model = (cfg.get("muse_model") or "").strip() or admin_vlm
    admin_workflow = (cfg.get("muse_workflow") or "").strip()
    if admin_workflow and workflows and admin_workflow not in workflows:
        admin_workflow = ""      # 消えたワークフローを既定にしない
    suggested_model = admin_model or (vision[0] if vision else (models[0] if models else ""))
    suggested_workflow = admin_workflow or (workflows[0] if workflows else "")

    return {
        "ok": True,
        "comfyui": {
            "ok": comfy_ok,
            "url": resolved_comfy_url,
            "workflows": workflows,
            "workflow_caps": workflow_caps,
        },
        "llm": {
            "ok": ollama_ok,
            "url": ollama_url,
            "models": models,
            "vision_models": vision,
        },
        "characters": {"count": character_count},
        "locales": ["ja", "en"],
        "admin_defaults": {
            # 画面はここだけを見る。**空なら選ばせる。**
            "muse_model": admin_model,
            "muse_workflow": admin_workflow,
            "vlm_model": admin_vlm,
            "ollama_num_ctx": cfg.get("ollama_num_ctx"),
            "wd14_model_dir": cfg.get("wd14_model_dir"),
        },
        "suggested_run": {
            "model": suggested_model,
            "model_is_vision": _model_available(suggested_model, vision),
            "workflow": suggested_workflow,
            "locale": "ja",
            **ALL_DEFAULTS,
        },
        "framings": [
            "auto", "full_body", "upper_body", "face_closeup", "from_behind",
        ],
        "notes": {
            "vision_model_required": (
                "Once a board exists, chat turns are shown it. A text-only "
                "model does not fail — Ollama silently drops the images — so "
                "pick one listed under llm.vision_models. inputs.model can "
                "stay a cheaper text model for turns before a board exists; "
                "inputs.vision_model covers turns after."
            ),
            "workflow_required": (
                "One workflow renders both the draft and the final shoot. "
                "They differ only in steps and cfg."
            ),
            "draft_steps": (
                "20 steps at cfg 4.0 is the validated draft. It is cheap in "
                "steps but full size, one frame — the crew argues over it in "
                "chat, it is not a thumbnail."
            ),
        },
        "endpoints": {
            "catalog": "GET /api/muse/catalog",
            "characters": "GET /api/characters",
            "sessions": "POST /api/muse/sessions",
            "session": "GET /api/muse/sessions/{session_id}",
            "patch_inputs": "PATCH /api/muse/sessions/{session_id}/inputs",
            "character": "POST /api/muse/sessions/{session_id}/character",
            "chat": "POST /api/muse/sessions/{session_id}/chat",
            "board": "POST /api/muse/sessions/{session_id}/board",
            "cancel_board": "POST /api/muse/sessions/{session_id}/board/cancel",
            "approve": "POST /api/muse/sessions/{session_id}/approve",
            "finish": "POST /api/muse/sessions/{session_id}/finish",
            "stream": "GET /api/muse/sessions/{session_id}/stream",
        },
    }
