"""Muse capability catalog: workflows, models, presets, and run defaults.

One call the panel makes on open, so every picker is populated and anything
missing is reported before a generation is spent finding out.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from . import family
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
                    # Which model family it belongs to, so the panel can say so
                    # before the first frame. The graph is already being read
                    # here for the pose caps — the marker costs no extra read.
                    "family": family.family_from_name(name) or family.DEFAULT_FAMILY,
                }
                try:
                    wf = comfy.load_workflow(name)
                    info = comfy.inspect_workflow(wf)
                    cap["has_openpose"] = bool(info.get("has_openpose"))
                    cap["can_inject_image"] = bool(info.get("can_inject_image"))
                    cap["family"] = family.resolve_family(name, wf)
                    # What the graph renders at on its own — the panel shows this
                    # where a family leaves the canvas to the workflow, so the
                    # size is visible without opening ComfyUI.
                    cap["canvas"] = info.get("canvas")
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
    # **Only what the admin screen decided is called a default (2026-09-13).**
    #
    # The Showrunner: "empty the llm and image model too, so they are chosen before
    # running. If a default is set in the admin screen, it should be possible to
    # start from that default." `suggested_*` was picking **the first of the list**,
    # so the screen used that as the default and shoots began without the Showrunner
    # choosing. The first-of-list pick stays (outside callers read it), and the
    # screen now reads `admin_defaults` below instead.
    admin_model = (cfg.get("muse_model") or "").strip() or admin_vlm
    admin_workflow = (cfg.get("muse_workflow") or "").strip()
    if admin_workflow and workflows and admin_workflow not in workflows:
        admin_workflow = ""      # a workflow that has gone is not a default
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
            # The screen reads only this. **Empty means it asks to choose.**
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
        # What each family asks for, so the panel prints the numbers from the
        # table rather than hardcoding them. `cfg: null` means "the workflow's
        # own" — that family's graphs keep whatever cfg they were saved with.
        "image_families": {
            name: {
                "label": row["label"],
                "draft_steps": row["draft_steps"],
                "final_steps": row["final_steps"],
                "draft_cfg": row["draft_cfg"],
                "final_cfg": row["final_cfg"],
                "negative": row["negative"],
                # "muse" = the session's canvas is written into the graph;
                # "workflow" = the graph keeps the resolution it was saved at.
                "canvas": row["canvas"],
            }
            for name, row in family.FAMILIES.items()
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
            "approve": "POST /api/muse/sessions/{session_id}/approve",
            "finish": "POST /api/muse/sessions/{session_id}/finish",
            "stream": "GET /api/muse/sessions/{session_id}/stream",
        },
    }
