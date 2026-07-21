"""Chronicle Stage1+Stage2 pipeline (panel_1/2/3, think=True)."""
from __future__ import annotations

import asyncio
import logging
import random as _random
from typing import Any

from ..spooler.models import CancelToken, JobCancelled, JobLane, ProgressReporter
from . import db as story_db
from .authors import resolve_author_style
from .stage1_storyboard import (
    PANELS,
    apply_stage1_failure_handling,
    build_stage1_messages,
    build_stage1_user_input,
    candidate_from_stage1,
    character_profile_from_tags,
    custom_tags_from_body,
    parse_stage1_json,
    stage1_needs_retry,
)
from .stage2_enhance import enhance_all_panels

logger = logging.getLogger(__name__)

CANDIDATE_IDS = ("A", "B", "C")


async def run_chronicle_candidates_v2(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    body,
    db,
    ollama,
    token_queue: asyncio.Queue,
    story_id: str | None = None,
    temperature: float | None = None,
    bind_llm,
    models_fn,
    llm_options_fn,
    vlm_image_bytes_fn,
) -> None:
    """Phase 1: Stage1 storyboard × up to 3 candidates."""
    temp = body.temperature if temperature is None else temperature

    def _put(event: dict | None) -> None:
        token_queue.put_nowait(event)

    def _phase(code: str, progress: float, text: str) -> None:
        reporter.update(progress, text)
        _put({"type": "phase", "code": code, "progress": progress})

    _abort = asyncio.Event()
    cancel.on_cancel(_abort.set)

    try:
        from ..runtime_config import get_runtime_config

        cfg = await get_runtime_config(db)
        ollama = bind_llm(ollama, body)
        models = models_fn(body, cfg)
        options = llm_options_fn(body, temp, cfg, story=True)

        draft = None
        doc: dict = {}
        if story_id:
            draft = await story_db.get_story(db, story_id)
            if not draft:
                _put({"type": "error", "message": "Draft story not found"})
                return
        ctx: dict = dict((draft or {}).get("context") or {})

        if not ctx.get("character_profile"):
            has_base = bool((body.base_sha256 or "").strip())
            wd14_tags: list[str] = []
            style_hint = ""
            if has_base:
                _phase("loadingImage", 0.05, "Loading reference image...")
                doc = await db.get(body.base_sha256) or {}
                if not doc:
                    _put({"type": "error", "message": "Base image not found"})
                    return
                wd14_tags = list(doc.get("wd14_tags") or [])
                style_hint = str(doc.get("model_name") or "").strip()
                # Optional: keep raw tags for profile; no FIXED scene act
            elif not (body.user_topic or "").strip():
                _put({
                    "type": "error",
                    "message": "user_topic is required when no base image is provided",
                })
                return

            _phase("buildingProfile", 0.12, "Building character profile...")
            profile = character_profile_from_tags(
                getattr(body, "character_tags", "") or "",
                wd14_tags,
                rng=_random.Random((body.base_sha256 or body.user_topic or "x")[:32]),
            )
            author_style = await resolve_author_style(
                db,
                author_id=getattr(body, "author_id", "") or "",
                author_style=getattr(body, "author_style", "") or "",
            )
            ctx = {
                "character_profile": profile,
                "wd14_tags": wd14_tags,
                "style_hint": style_hint,
                "author_style": author_style,
                "include_happening": bool(getattr(body, "include_happening", False)),
                "custom_tags": custom_tags_from_body(body),
                "body": body.model_dump() if hasattr(body, "model_dump") else dict(body),
            }

        profile = ctx["character_profile"]
        author_style = ctx.get("author_style") or ""
        custom_tags = ctx.get("custom_tags") or custom_tags_from_body(body)
        include_happening = bool(ctx.get("include_happening"))
        style_hint = ctx.get("style_hint") or ""
        avoid: list[str] = []

        _phase("storyboarding", 0.25, "Writing storyboards...")
        candidates: list[dict] = []
        for i, cid in enumerate(CANDIDATE_IDS):
            cancel.raise_if_set()
            if _abort.is_set():
                raise JobCancelled()
            opts = dict(options)
            opts["temperature"] = min(
                1.2, float(opts.get("temperature") or temp) + 0.08 * i
            )
            user_input = build_stage1_user_input(
                theme=body.user_topic or "",
                character_profile=profile,
                include_happening=include_happening,
                author_style=author_style,
                custom_tags=custom_tags,
                avoid_repeats=avoid,
                style_hint=style_hint,
            )
            messages = build_stage1_messages(user_input)
            data = None
            for attempt in range(2):
                try_opts = dict(opts)
                if attempt == 1:
                    reason = stage1_needs_retry(
                        data,
                        include_happening=include_happening,
                        avoid_repeats=avoid,
                    )
                    if reason == "avoid_repeats":
                        try_opts["temperature"] = min(
                            1.3, float(try_opts.get("temperature") or 0.7) + 0.1
                        )
                raw = await ollama.chat_text(
                    "",
                    model=models["story"],
                    options=try_opts,
                    fmt=None,
                    think=True,
                    messages=messages,
                )
                cancel.raise_if_set()
                data = parse_stage1_json(raw)
                if data:
                    data = apply_stage1_failure_handling(
                        data,
                        character_profile=profile,
                        custom_tags=custom_tags,
                        include_happening=include_happening,
                    )
                reason = stage1_needs_retry(
                    data,
                    include_happening=include_happening,
                    avoid_repeats=avoid,
                )
                if not reason:
                    break
                logger.info(
                    "[chronicle] stage1 retry candidate=%s reason=%s", cid, reason
                )
            if not data:
                _put({
                    "type": "warning",
                    "message": f"Stage1 failed for candidate {cid}",
                })
                continue
            cand = candidate_from_stage1(data, candidate_id=cid)
            candidates.append(cand)
            cat = str(data.get("happening_category") or "").strip()
            if cat and cat != "該当なし" and cat not in avoid:
                avoid.append(cat)
            _phase(
                "storyboarding",
                0.25 + 0.2 * (i + 1) / 3,
                f"Storyboard {cid} ready...",
            )

        if not candidates:
            _put({"type": "error", "message": "Failed to generate storyboard candidates"})
            return

        if story_id:
            hist = list((draft or {}).get("respin_history") or [])
            hist.append({
                "kind": "candidates",
                "temperature": temp,
                "candidates": (draft or {}).get("candidates") or [],
            })
            await story_db.set_story_payload(db, story_id, {
                "candidates": candidates,
                "respin_history": hist,
                "context": ctx,
            })
        else:
            payload = story_db.new_story_payload(
                base_image_id=body.base_sha256 or "",
                workflow_name=body.workflow_name or "",
                group_id=body.group_id or "",
                user_topic=body.user_topic or "",
                locale=body.locale or "ja",
                status="draft",
                candidates=candidates,
                context=ctx,
                base_model_name=(doc or {}).get("model_name") or "",
                include_happening=include_happening,
                author_style=author_style,
            )
            story_id = await story_db.create_story(db, payload)

        _phase("selecting", 0.98, "Choose a story...")
        _put({"type": "candidates", "story_id": story_id, "candidates": candidates})
    except JobCancelled:
        raise
    except Exception as exc:
        logger.exception("[chronicle] candidates phase failed")
        _put({"type": "error", "message": str(exc)})
    finally:
        _put(None)


async def run_chronicle_expand_v2(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    story_id: str,
    candidate_id: str,
    temperature: float,
    db,
    ollama,
    spooler,
    comfy,
    token_queue: asyncio.Queue,
    bind_llm,
    models_fn,
    llm_options_fn,
    image_generate_fn,
) -> None:
    """Phase 2: Stage2 enhance × 3 panels, then generate all three images."""
    from ..runtime_config import get_runtime_config
    from ..story.api import ChronicleRequest

    def _put(event: dict | None) -> None:
        token_queue.put_nowait(event)

    def _phase(code: str, progress: float, text: str) -> None:
        reporter.update(progress, text)
        _put({"type": "phase", "code": code, "progress": progress})

    try:
        draft = await story_db.get_story(db, story_id)
        if not draft:
            _put({"type": "error", "message": "Draft story not found"})
            return

        ctx = draft.get("context") or {}
        body_dict = ctx.get("body") or {}
        try:
            body = ChronicleRequest(**{
                **body_dict,
                "base_sha256": draft.get("base_image_id") or body_dict.get("base_sha256") or "",
                "workflow_name": draft.get("workflow_name") or body_dict.get("workflow_name") or "",
                "group_id": draft.get("group_id") or body_dict.get("group_id") or "",
                "locale": draft.get("locale") or body_dict.get("locale") or "ja",
                "user_topic": draft.get("user_topic") or body_dict.get("user_topic") or "",
            })
        except Exception:
            body = ChronicleRequest(
                base_sha256=draft.get("base_image_id") or "",
                workflow_name=draft.get("workflow_name") or "",
                group_id=draft.get("group_id") or "",
                locale=draft.get("locale") or "ja",
                user_topic=draft.get("user_topic") or "",
            )

        selected = None
        for c in draft.get("candidates") or []:
            if c.get("id") == candidate_id:
                selected = c
                break
        if not selected or not selected.get("stage1"):
            _put({"type": "error", "message": f"Candidate {candidate_id} not found"})
            return

        stage1 = selected["stage1"]
        profile = ctx.get("character_profile") or {}
        custom_tags = ctx.get("custom_tags") or {}
        stage1 = apply_stage1_failure_handling(
            stage1,
            character_profile=profile,
            custom_tags=custom_tags,
            include_happening=bool(
                stage1.get("include_happening")
                or ctx.get("include_happening")
            ),
        )

        cfg = await get_runtime_config(db)
        ollama = bind_llm(ollama, body)
        models = models_fn(body, cfg)
        options = llm_options_fn(body, temperature, cfg, story=True)
        # Stage2: slightly cooler but still creative
        options = dict(options)
        options["temperature"] = min(float(options.get("temperature") or 0.7), 0.85)
        options["num_ctx"] = max(16384, int(options.get("num_ctx") or 32768))

        if draft.get("status") == "final":
            hist = list(draft.get("respin_history") or [])
            hist.append({
                "kind": "expand",
                "temperature": temperature,
                "title": draft.get("title"),
                "axes": draft.get("axes"),
            })
            await story_db.set_story_payload(db, story_id, {"respin_history": hist})

        _phase("enhancingPrompts", 0.3, "Enhancing panel prompts...")
        enhanced = await enhance_all_panels(
            ollama,
            stage1=stage1,
            custom_tags=custom_tags,
            model=models["story"],
            options=options,
            locale=body.locale or "ja",
        )
        cancel.raise_if_set()

        prompts: dict[str, dict] = {}
        for key in PANELS:
            e = enhanced.get(key) or {}
            prompts[key] = {
                "positive": e.get("positive") or "",
                "negative": e.get("negative") or "",
                "visual_script": e.get("visual_script") or "",
            }
            _put({
                "type": "axis_prompt",
                "axis": key,
                "positive": prompts[key]["positive"],
                "negative": prompts[key]["negative"],
                "visual_script": prompts[key]["visual_script"],
            })
            if not prompts[key]["positive"]:
                _put({"type": "error", "message": f"Prompt build failed for {key}"})
                return

        title = str(stage1.get("title") or selected.get("title") or "")
        overall = str(stage1.get("core_conflict") or selected.get("summary") or "")
        panels = stage1.get("panels") or []

        seed = _random.randint(0, (1 << 64) - 1)
        axes_payload: dict[str, Any] = {}
        for i, key in enumerate(PANELS):
            p = panels[i] if i < len(panels) else {}
            pr = prompts[key]
            nar_ja = str((p or {}).get("narrative_ja") or "")
            nar_en = str((p or {}).get("narrative_en") or "")
            axes_payload[key] = {
                "story": nar_en or nar_ja,
                "story_ja": nar_ja or nar_en,
                "prompt_positive": pr["positive"],
                "prompt_negative": pr["negative"],
                "visual_script": pr["visual_script"],
                "image_id": None,
                "camera": (p or {}).get("camera") or "",
                "character_state_diff": (p or {}).get("character_state_diff") or "",
            }

        _phase("savingStory", 0.75, "Saving story...")
        embedding = None
        try:
            embedding = await ollama.embed(
                " ".join([
                    title, overall,
                    *(axes_payload[a]["story"] for a in PANELS),
                ])[:4000]
            )
        except Exception as exc:
            logger.warning("[chronicle] story embed failed: %s", exc)

        await story_db.set_story_payload(db, story_id, {
            "status": "final",
            "selected_candidate": candidate_id,
            "workflow_name": body.workflow_name,
            "title": title,
            "title_ja": title,
            "overall_story": overall,
            "overall_story_ja": overall,
            "axes": axes_payload,
            "stage1": stage1,
            "include_happening": bool(stage1.get("include_happening")),
            "happening_category": stage1.get("happening_category") or "",
            "author_style": ctx.get("author_style") or "",
            "context": ctx,
        })
        if embedding:
            try:
                await story_db.set_story_embedding(db, story_id, embedding)
            except Exception as exc:
                logger.warning("[chronicle] set embedding failed: %s", exc)

        _put({"type": "story_saved", "story_id": story_id})

        image_jobs: list[dict] = []
        if not body.manual_mode and body.workflow_name:
            for key in PANELS:
                cancel.raise_if_set()
                gen_job_id = spooler.submit(
                    JobLane.GENERATION,
                    "chronicle_image",
                    image_generate_fn,
                    meta={"group_id": body.group_id, "story_id": story_id, "axis": key},
                    db=db,
                    comfy=comfy,
                    story_id=story_id,
                    axis=key,
                    workflow_name=body.workflow_name,
                    positive=prompts[key]["positive"],
                    negative=prompts[key]["negative"],
                    seed=seed,
                )
                image_jobs.append({"axis": key, "job_id": gen_job_id})
            _put({"type": "image_jobs", "jobs": image_jobs})
        elif not body.workflow_name:
            _put({
                "type": "warning",
                "message": "No workflow selected — image generation skipped.",
            })

        _put({
            "type": "done",
            "story_id": story_id,
            "group_id": body.group_id,
            "seed": seed,
            "manual_mode": body.manual_mode,
            "title": title,
            "title_ja": title,
            "overall": overall,
            "overall_ja": overall,
            "axes": axes_payload,
        })
    except JobCancelled:
        _put({"type": "error", "message": "Chronicle expand was cancelled"})
        raise
    except Exception as exc:
        logger.exception("[chronicle] expand pipeline failed")
        _put({"type": "error", "message": str(exc)})
    finally:
        _put(None)
