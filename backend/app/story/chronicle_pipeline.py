"""Chronicle Stage1+Stage2 pipeline (panel_1/2/3, think=True)."""
from __future__ import annotations

import asyncio
import json
import logging
import random as _random
import time
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


def _opts_snapshot(options: dict, *, think: bool = True) -> str:
    payload = {**dict(options or {}), "think": think, "num_predict": -1}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


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
    """Phase 1: Stage1 storyboard × up to 3 candidates (serial, fixed options)."""
    temp = body.temperature if temperature is None else temperature

    def _put(event: dict | None) -> None:
        token_queue.put_nowait(event)

    def _log(msg: str) -> None:
        logger.info("%s", msg)
        _put({"type": "token", "text": msg.rstrip() + "\n"})

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
        # Fixed for the whole Phase1 job — never mutate per candidate/attempt
        # (Ollama reloads the model when options differ even slightly).
        options = dict(llm_options_fn(body, temp, cfg, story=True))

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
                _log(f"[chronicle] loading base image sha={body.base_sha256[:12]}...")
                doc = await db.get(body.base_sha256) or {}
                if not doc:
                    _put({"type": "error", "message": "Base image not found"})
                    return
                wd14_tags = list(doc.get("wd14_tags") or [])
                style_hint = str(doc.get("model_name") or "").strip()
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
                "llm_options": dict(options),
                "story_model": models.get("story") or "",
            }
        else:
            # Keep options identical across respin of candidates when possible
            if ctx.get("llm_options"):
                options = dict(ctx["llm_options"])
            ctx["llm_options"] = dict(options)
            ctx["story_model"] = models.get("story") or ctx.get("story_model") or ""

        profile = ctx["character_profile"]
        author_style = ctx.get("author_style") or ""
        custom_tags = ctx.get("custom_tags") or custom_tags_from_body(body)
        include_happening = bool(ctx.get("include_happening"))
        style_hint = ctx.get("style_hint") or ""
        avoid: list[str] = []
        story_model = models.get("story") or ""

        _phase("storyboarding", 0.25, "Writing storyboards...")
        _log(f"[chronicle] model={story_model}")
        _log(f"[chronicle] options={_opts_snapshot(options, think=True)}")
        _log(
            f"[chronicle] Phase1 START candidates={len(CANDIDATE_IDS)} "
            f"serial=true include_happening={include_happening}"
        )

        candidates: list[dict] = []
        phase1_t0 = time.perf_counter()
        for i, cid in enumerate(CANDIDATE_IDS):
            cancel.raise_if_set()
            if _abort.is_set():
                raise JobCancelled()
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
                _log(
                    f"[chronicle] Stage1 candidate={cid} attempt={attempt + 1}/2 START"
                )
                t0 = time.perf_counter()
                raw = await ollama.chat_text(
                    "",
                    model=story_model,
                    options=options,
                    fmt=None,
                    think=True,
                    messages=messages,
                )
                cancel.raise_if_set()
                wall = time.perf_counter() - t0
                data = parse_stage1_json(raw)
                parsed_ok = data is not None
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
                _log(
                    f"[chronicle] Stage1 candidate={cid} attempt={attempt + 1}/2 END "
                    f"wall={wall:.3f}s parsed={'ok' if parsed_ok else 'FAIL'} "
                    f"out_chars={len(raw or '')}"
                    + (f" retry_reason={reason}" if reason else "")
                )
                if not reason:
                    break
                _log(
                    f"[chronicle] Stage1 candidate={cid} RETRY reason={reason} "
                    f"(options unchanged)"
                )
                logger.info(
                    "[chronicle] stage1 retry candidate=%s reason=%s", cid, reason
                )
            if not data:
                _put({
                    "type": "warning",
                    "message": f"Stage1 failed for candidate {cid}",
                })
                _log(f"[chronicle] Stage1 candidate={cid} FAILED — skipped")
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
            _log(
                f"[chronicle] Stage1 candidate={cid} OK title={cand.get('title')!r}"
            )

        _log(
            f"[chronicle] Phase1 DONE candidates={len(candidates)} "
            f"total_wall={time.perf_counter() - phase1_t0:.3f}s"
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


def _seed_from_base_doc(doc: dict | None) -> int | None:
    if not doc:
        return None
    cr = doc.get("creation_record") or {}
    if isinstance(cr, dict) and cr.get("seed") is not None:
        try:
            return int(cr["seed"])
        except (TypeError, ValueError):
            pass
    mi = doc.get("model_info") or {}
    if isinstance(mi, dict) and mi.get("seed") is not None:
        try:
            return int(mi["seed"])
        except (TypeError, ValueError):
            pass
    return None


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
    """Phase 2: Stage2 enhance × 3 panels (serial), then generate all three images."""
    from ..runtime_config import get_runtime_config
    from ..story.api import ChronicleRequest

    def _put(event: dict | None) -> None:
        token_queue.put_nowait(event)

    def _log(msg: str) -> None:
        logger.info("%s", msg)
        _put({"type": "token", "text": msg.rstrip() + "\n"})

    def _phase(code: str, progress: float, text: str) -> None:
        reporter.update(progress, text)
        _put({"type": "phase", "code": code, "progress": progress})

    try:
        draft = await story_db.get_story(db, story_id)
        if not draft:
            _put({"type": "error", "message": "Draft story not found"})
            return

        ctx = dict(draft.get("context") or {})
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
        # Prefer Phase1-stored options so Ollama does not reload between phases.
        if isinstance(ctx.get("llm_options"), dict) and ctx["llm_options"]:
            options = dict(ctx["llm_options"])
            _log("[chronicle] Stage2 using Phase1-stored llm_options (no mutation)")
        else:
            options = dict(llm_options_fn(body, temperature, cfg, story=True))
            ctx["llm_options"] = dict(options)
        story_model = (
            ctx.get("story_model")
            or models.get("story")
            or ""
        )

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
        _log(f"[chronicle] model={story_model}")
        _log(f"[chronicle] options={_opts_snapshot(options, think=True)}")
        _log("[chronicle] Stage2 START panels=3 serial=true")
        enhanced = await enhance_all_panels(
            ollama,
            stage1=stage1,
            custom_tags=custom_tags,
            model=story_model,
            options=options,
            locale=body.locale or "ja",
            log=_log,
        )
        cancel.raise_if_set()
        _log("[chronicle] Stage2 DONE")

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

        base_sha = (body.base_sha256 or draft.get("base_image_id") or "").strip()
        use_ref_seed = bool(getattr(body, "use_ref_seed", False)) and bool(base_sha)
        seed = _random.randint(0, (1 << 64) - 1)
        if use_ref_seed and base_sha:
            base_doc = await db.get(base_sha) or {}
            ref = _seed_from_base_doc(base_doc)
            if ref is not None:
                seed = ref
                _log(f"[chronicle] use_ref_seed=ON seed={seed} from base={base_sha[:12]}")
            else:
                _log(
                    f"[chronicle] use_ref_seed=ON but no seed on base={base_sha[:12]} "
                    f"— using random seed={seed}"
                )
        else:
            _log(f"[chronicle] image seed={seed} (random)")

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
            _log(
                f"[chronicle] queue image jobs workflow={body.workflow_name} "
                f"base_sha={base_sha[:12] if base_sha else '(none)'}"
            )
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
                    base_sha256=base_sha,
                )
                image_jobs.append({"axis": key, "job_id": gen_job_id})
                _log(f"[chronicle] queued {key} job_id={gen_job_id}")
            _put({"type": "image_jobs", "jobs": image_jobs})
        elif not body.workflow_name:
            _put({
                "type": "warning",
                "message": "No workflow selected — image generation skipped.",
            })
            _log("[chronicle] image generation skipped (no workflow)")

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
