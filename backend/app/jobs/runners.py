"""
Job runner functions.

Runner signature:
    async def run_X(reporter: ProgressReporter, cancel: CancelToken, *, <deps>) -> Any

- reporter.update(progress, text) reports progress from 0 to 1
- reporter.indeterminate() signals progress is active but indeterminate
- cancel.raise_if_set() performs cooperative cancellation checks
- cancel.on_cancel(handler) registers an external engine abort handler
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from pathlib import Path

from ..spooler.models import CancelToken, JobCancelled, ProgressReporter

_PRIORITY_ALIGNMENT = -10

logger = logging.getLogger(__name__)


# ── SYNC lane: scan jobs ───────────────────────────────────────────────────────

async def run_scan_heal(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama=None,
    spooler=None,
) -> None:
    from ..scanner.scanner import run_heal, scan_state
    reporter.indeterminate()
    task = asyncio.create_task(run_heal(db))
    cancel.on_cancel(task.cancel)

    while not task.done():
        if scan_state.total > 0:
            reporter.update(
                scan_state.processed / scan_state.total,
                f"{scan_state.processed}/{scan_state.total} files",
            )
        await asyncio.sleep(0.5)

    try:
        await task
    except asyncio.CancelledError:
        raise JobCancelled()

    # auto-start AI pipeline if new files were registered
    if spooler is not None and ollama is not None and scan_state.added > 0:
        from ..spooler.models import JobLane
        if spooler.is_lane_active(JobLane.EMBEDDING):
            spooler.submit(
                JobLane.EMBEDDING,
                "ai_pipeline_post_scan",
                run_pipeline,
                db=db,
                ollama=ollama,
                spooler=spooler,
            )
            logger.info("Auto-triggered pipeline after heal: %d new files", scan_state.added)


async def run_scan_full(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama=None,
    spooler=None,
) -> None:
    from ..scanner.scanner import run_scan, scan_state
    reporter.indeterminate()
    task = asyncio.create_task(run_scan(db))
    cancel.on_cancel(task.cancel)

    while not task.done():
        if scan_state.total > 0:
            reporter.update(
                scan_state.processed / scan_state.total,
                f"{scan_state.processed}/{scan_state.total} files",
            )
        await asyncio.sleep(0.5)

    try:
        await task
    except asyncio.CancelledError:
        raise JobCancelled()

    if spooler is not None and ollama is not None and scan_state.added > 0:
        from ..spooler.models import JobLane
        if spooler.is_lane_active(JobLane.EMBEDDING):
            spooler.submit(
                JobLane.EMBEDDING,
                "ai_pipeline_post_scan",
                run_pipeline,
                db=db,
                ollama=ollama,
                spooler=spooler,
            )
            logger.info("Auto-triggered pipeline after full scan: %d new files", scan_state.added)


async def run_scan_refresh_metadata(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
) -> None:
    from ..scanner.scanner import run_refresh_metadata, scan_state
    reporter.indeterminate()
    task = asyncio.create_task(run_refresh_metadata(db))
    cancel.on_cancel(task.cancel)

    while not task.done():
        if scan_state.total > 0:
            reporter.update(
                scan_state.processed / scan_state.total,
                f"{scan_state.processed}/{scan_state.total} files",
            )
        await asyncio.sleep(0.5)

    try:
        await task
    except asyncio.CancelledError:
        raise JobCancelled()


# ── SYNC lane: color extraction · UMAP ────────────────────────────────────────

async def run_color_backfill(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
) -> dict:
    from concurrent.futures import ThreadPoolExecutor

    from qdrant_client import models as qm

    from ..ai.color_extractor import extract_color_palette
    from ..db.qdrant_client import IMAGES_COLLECTION

    reporter.indeterminate()
    done = 0
    total = 0
    cancelled = False

    def _check_cancel() -> bool:
        return cancel._event.is_set()

    cancel.on_cancel(lambda: None)  # cancellation is polled via _check_cancel

    concurrency = 4
    sem = asyncio.Semaphore(concurrency)
    loop = asyncio.get_event_loop()
    executor = ThreadPoolExecutor(max_workers=concurrency)

    try:
        total = await db.total_count()
        offset = None

        while True:
            if _check_cancel():
                raise JobCancelled()

            points, next_offset = await db._qc.scroll(
                collection_name=IMAGES_COLLECTION,
                scroll_filter=qm.Filter(
                    should=[
                        # avg_saturation が absent（未処理）または < 0（失敗済み）
                        qm.IsEmptyCondition(is_empty=qm.PayloadField(key="avg_saturation")),
                        qm.FieldCondition(key="avg_saturation", range=qm.Range(lt=0.0)),
                        # color_lab payload が残っている（color_vector への移行待ち）
                        qm.Filter(must_not=[
                            qm.IsEmptyCondition(is_empty=qm.PayloadField(key="color_lab"))
                        ]),
                    ]
                ),
                limit=200,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(include=["sha256", "path", "color_lab"]),
                with_vectors=False,
            )

            fast_items: list[tuple[str, list[float]]] = []
            slow_items: list[tuple[str, Path]] = []
            missing_sha256s: list[str] = []
            for p in points:
                sha256 = p.payload.get("sha256")
                if not sha256:
                    continue
                existing_color_lab = p.payload.get("color_lab")
                if existing_color_lab:
                    fast_items.append((sha256, existing_color_lab))
                else:
                    fp = Path(p.payload.get("path") or "")
                    if fp.exists():
                        slow_items.append((sha256, fp))
                    else:
                        logger.warning("color backfill: file not found for %s at %s — marking failed", sha256, fp)
                        missing_sha256s.append(sha256)

            if missing_sha256s and not _check_cancel():
                for sha256 in missing_sha256s:
                    await db.set_payload(sha256, {"avg_saturation": -1.0})
                done += len(missing_sha256s)

            if fast_items and not _check_cancel():
                if db.has_color_vector:
                    await db.set_color_vectors_batch(fast_items)
                await db.delete_payload_keys_batch([s for s, _ in fast_items], ["color_lab"])
                # avg_saturation が未設定のまま残ると analyzer が pending と誤検知するため、
                # color_lab から Lab chroma を求めて proxy avg_saturation をセットする。
                async def _set_proxy_sat(sha256: str, lab: list) -> None:
                    if len(lab) >= 3:
                        chroma = math.sqrt(lab[1] ** 2 + lab[2] ** 2)
                        avg_sat = round(min(chroma / 128.0, 1.0), 3)
                    else:
                        avg_sat = 0.0
                    await db.set_payload(sha256, {"avg_saturation": avg_sat})
                await asyncio.gather(*[_set_proxy_sat(s, lab) for s, lab in fast_items])
                done += len(fast_items)

            async def _process_slow(sha256: str, fp: Path) -> None:
                nonlocal done
                if _check_cancel():
                    return
                async with sem:
                    try:
                        color_data = await loop.run_in_executor(executor, extract_color_palette, fp)
                        if color_data:
                            color_lab = color_data.pop("color_lab", None)
                            await db.set_payload(sha256, color_data)
                            if color_lab and db.has_color_vector:
                                await db.set_color_vector(sha256, color_lab)
                        else:
                            logger.warning("color_extractor returned empty for %s — marking failed", sha256)
                            await db.set_payload(sha256, {"avg_saturation": -1.0})
                    except Exception as e:
                        logger.warning("Color extraction failed for %s: %s", sha256, e)
                        try:
                            await db.set_payload(sha256, {"avg_saturation": -1.0})
                        except Exception:
                            pass
                    finally:
                        done += 1

            if slow_items and not _check_cancel():
                await asyncio.gather(
                    *[_process_slow(s, fp) for s, fp in slow_items],
                    return_exceptions=True,
                )

            if total > 0:
                reporter.update(done / max(total, 1), f"{done}/{total} items")

            if next_offset is None:
                break
            offset = next_offset

    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    recovered = await db.recover_missing_color_vectors()
    if recovered:
        logger.info("color backfill: recovered %d color_vectors from palette_hex heuristic", recovered)

    return {"done": done, "total": total, "recovered": recovered}


async def run_analyze_umap(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
) -> None:
    from ..ai.umap_reducer import analyzer_umap_state, run_umap_analysis

    def _cancel_fn() -> bool:
        return cancel._event.is_set()

    reporter.indeterminate()
    task = asyncio.create_task(run_umap_analysis(db, _cancel_fn))
    cancel.on_cancel(task.cancel)

    while not task.done():
        st = analyzer_umap_state
        if st.get("total", 0) > 0 and st.get("done", 0) > 0:
            reporter.update(
                st["done"] / st["total"],
                f"{st.get('phase', '')} {st['done']}/{st['total']}",
            )
        else:
            reporter.indeterminate()
        await asyncio.sleep(0.5)

    try:
        await task
    except asyncio.CancelledError:
        raise JobCancelled()


# ── EMBEDDING lane: AI pipeline · MRL backfill ────────────────────────────────

async def run_pipeline(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama,
    sha256s: list[str] | None = None,
    spooler=None,
) -> dict:
    from ..ai.pipeline import pipeline_state, run_ai_pipeline

    def _on_cancel() -> None:
        pipeline_state.cancelled = True

    cancel.on_cancel(_on_cancel)
    reporter.indeterminate()

    task = asyncio.create_task(
        run_ai_pipeline(db, ollama, sha256s, pause_checkpoint=cancel.pause_checkpoint)
    )
    cancel.on_cancel(task.cancel)

    while not task.done():
        total = pipeline_state.total
        processed = pipeline_state.processed
        if total > 0:
            reporter.update(processed / total, f"{processed}/{total} processed")
        else:
            reporter.indeterminate()
        await asyncio.sleep(0.5)

    try:
        await task
    except asyncio.CancelledError:
        raise JobCancelled()

    if cancel._event.is_set():
        raise JobCancelled()

    result = {
        "processed": pipeline_state.processed,
        "errors": pipeline_state.errors,
    }

    # auto-alignment: submit evaluation job after pipeline completes
    if spooler is not None and pipeline_state.processed > 0:
        from ..runtime_config import get_runtime_config
        from ..spooler.models import JobLane
        cfg = await get_runtime_config(db)
        if cfg.get("auto_alignment_evaluate", False):
            if spooler.is_lane_active(JobLane.EVALUATION):
                _already_queued = any(
                    j["lane"] == "eval" and j["state"] == "queued"
                    for j in spooler.snapshot()
                )
                if not _already_queued:
                    spooler.submit(
                        JobLane.EVALUATION,
                        "alignment_auto",
                        run_alignment_evaluate,
                        db=db,
                        ollama=ollama,
                        sha256s=None,
                        spooler=spooler,
                        priority=_PRIORITY_ALIGNMENT,
                    )
                    logger.info("Auto-alignment submitted after pipeline (%d processed)", pipeline_state.processed)
            else:
                logger.info("Auto-alignment skipped: EVALUATION lane is paused")

    # auto-continue: if the batch was full, re-submit for the remaining items
    if spooler is not None and not cancel._event.is_set():
        from ..runtime_config import get_runtime_config
        from ..spooler.models import JobLane
        cfg2 = await get_runtime_config(db)
        if cfg2.get("pipeline_auto_continue", True):
            batch_size = int(cfg2.get("pipeline_batch_size", 5000))
            if pipeline_state.total >= batch_size and spooler.is_lane_active(JobLane.EMBEDDING):
                spooler.submit(
                    JobLane.EMBEDDING,
                    "ai_pipeline_continue",
                    run_pipeline,
                    db=db,
                    ollama=ollama,
                    spooler=spooler,
                )
                logger.info("Auto-continue pipeline submitted (batch_size=%d reached)", batch_size)

    return result


async def run_mrl_backfill(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
) -> dict:
    reporter.indeterminate()
    count = await db.backfill_small_embeddings()
    return {"done": count}


# ── EVALUATION lane: alignment evaluation ─────────────────────────────────────

async def run_alignment_evaluate(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama,
    sha256s: list[str] | None = None,
    spooler=None,
) -> dict:
    from ..alignment.evaluator import AlignmentEvaluator
    from ..spooler.models import JobLane

    evaluator = AlignmentEvaluator(db, ollama)

    def _cancel_fn() -> bool:
        return cancel._event.is_set()

    def _on_progress(done: int, total: int) -> None:
        if total > 0:
            reporter.update(done / total, f"{done}/{total} images")
        else:
            reporter.indeterminate()

    from ..runtime_config import get_runtime_config
    cfg = await get_runtime_config(db)
    concurrency = int(cfg.get("alignment_concurrency", 1))

    results = await evaluator.evaluate_batch(
        sha256s,
        cancel_fn=_cancel_fn,
        on_progress=_on_progress,
        concurrency=concurrency,
        pause_checkpoint=cancel.pause_checkpoint,
    )

    if cancel._event.is_set():
        raise JobCancelled()

    errors = sum(1 for r in results if r.status == "error")

    # self-continuation: re-submit to cover images added concurrently by the pipeline
    # manual single-image jobs (sha256s specified) are not re-submitted
    if (
        spooler is not None
        and sha256s is None
        and len(results) > 0
        and spooler.is_lane_active(JobLane.EVALUATION)
    ):
        _already_queued = any(
            j["lane"] == "eval" and j["state"] == "queued"
            for j in spooler.snapshot()
        )
        if not _already_queued:
            spooler.submit(
                JobLane.EVALUATION,
                "alignment_auto",
                run_alignment_evaluate,
                db=db,
                ollama=ollama,
                sha256s=None,
                spooler=spooler,
                priority=_PRIORITY_ALIGNMENT,
            )
            logger.info("Auto-alignment re-submitted for next batch")

    return {"done": len(results), "errors": errors}


async def run_tag_taxonomy(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    ollama,
    tags: list[str],
    model: str | None = None,
) -> dict:
    import json

    from ..ai.tag_analyzer import TAG_CATEGORIES, split_chunks

    chunks = split_chunks(tags)
    total = len(tags)
    done = 0
    taxonomy: dict[str, str] = {}

    for chunk in chunks:
        cancel.raise_if_set()
        if not chunk:
            continue
        tag_list = ", ".join(f'"{t}"' for t in chunk)
        prompt = (
            f"You are a tag classifier for anime/illustration images.\n"
            f"Classify each tag into exactly one of these categories: "
            f"{', '.join(TAG_CATEGORIES)}.\n"
            f"Respond with ONLY a JSON object in this exact format:\n"
            f'{{\"tags\": {{\"tag_name\": \"category\", ...}}}}\n\n'
            f"Tags to classify: {tag_list}"
        )
        try:
            resp = await ollama.generate_text(prompt, model=model)
            start = resp.find("{")
            end = resp.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = json.loads(resp[start:end])
                taxonomy.update(parsed.get("tags", {}))
        except Exception as e:
            logger.warning("Tag taxonomy chunk failed: %s", e)
        done += len(chunk)
        reporter.update(done / max(total, 1), f"{done}/{total} tags")

    return {"taxonomy": taxonomy}


# ── SYNC lane: batch_category backfill ────────────────────────────────────────

async def run_batch_category_backfill(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
) -> dict:
    reporter.indeterminate()
    count = await db.backfill_batch_category()
    return {"done": count}


async def run_is_reference_backfill(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
) -> dict:
    reporter.indeterminate()
    count = await db.backfill_is_reference()
    return {"done": count}


# ── GENERATION lane: ComfyUI generation ───────────────────────────────────────

async def run_generation(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    comfy,
    db,
    workflow_name: str,
    positive: str,
    negative: str = "",
    positive_node_id: str = "",
    negative_node_id: str = "",
    batch_count: int = 1,
    creation_meta: dict | None = None,
    seed: int | None = None,
) -> dict:
    import asyncio as _asyncio
    import random as _random

    from ..api.ai import _save_and_register_comfy_image
    from ..creation.schema import CreationRecord, InspireContext, SourceImageRef

    reporter.indeterminate()

    # generate a random uint64 seed when none is specified
    if seed is None:
        seed = _random.randint(0, (1 << 64) - 1)

    async def _write_creation_record(sha256: str) -> None:
        if not creation_meta:
            return
        try:
            shas = creation_meta.get("sha256s", [])
            weights = creation_meta.get("weights", [])
            padded_weights = list(weights) + [0.0] * max(0, len(shas) - len(weights))
            source_images = [
                SourceImageRef(sha256=s, weight=w)
                for s, w in zip(shas, padded_weights)
            ]
            inspire_raw = creation_meta.get("inspire_context")
            record = CreationRecord(
                method="direct" if creation_meta.get("direct_prompt") else "refine",
                instruction=creation_meta.get("instruction", ""),
                prompt_style=creation_meta.get("prompt_style", ""),
                temperature=creation_meta.get("temperature"),
                num_ctx=creation_meta.get("num_ctx"),
                workflow_name=creation_meta.get("workflow_name", ""),
                batch_count=creation_meta.get("batch_count", 1),
                positive_prompt_generated=creation_meta.get("positive_prompt", ""),
                negative_prompt_generated=creation_meta.get("negative_prompt", ""),
                direct_prompt=bool(creation_meta.get("direct_prompt")),
                source_images=source_images,
                inspire_context=InspireContext(**inspire_raw) if inspire_raw else None,
                seed=creation_meta.get("seed"),
            )
            await db.set_payload(sha256, {"creation_record": record.model_dump()})
        except Exception as exc:
            logger.warning("creation_record write failed for %s: %s", sha256, exc)

    # load and patch workflow
    wf = comfy.load_workflow(workflow_name)
    patched = comfy.patch_workflow(
        wf, positive, negative, positive_node_id, negative_node_id, batch_count, seed=seed
    )

    # submit to ComfyUI
    prompt_id = await comfy.queue_prompt(patched)
    reporter.update(0.0, "Waiting in ComfyUI queue...")

    # cancel handler: delete from queue if not yet started, interrupt if running
    queued = True

    async def _cancel_comfy() -> None:
        if queued:
            try:
                await comfy.delete_from_queue(prompt_id)
            except Exception as exc:
                logger.warning("ComfyUI queue delete failed: %s", exc)
        try:
            await comfy.interrupt()
        except Exception as exc:
            logger.warning("ComfyUI interrupt failed: %s", exc)

    cancel.on_cancel(lambda: _asyncio.create_task(_cancel_comfy()))

    saved_sha256s: list[str] = []
    saved_filenames: set[str] = set()

    async for event in comfy.stream_progress(prompt_id):
        cancel.raise_if_set()
        queued = False

        if event["type"] == "comfy_progress":
            v = event.get("value", 0)
            m = event.get("max", 1)
            reporter.update(v / max(m, 1), f"Step {v}/{m}")

        elif event["type"] == "comfy_output":
            for img_ref in event.get("images", []):
                cancel.raise_if_set()
                try:
                    img_bytes = await comfy.fetch_image(
                        img_ref["filename"],
                        img_ref.get("subfolder", ""),
                        img_ref.get("type", "output"),
                    )
                    sha256 = await _save_and_register_comfy_image(
                        img_bytes, img_ref["filename"], db
                    )
                    if sha256:
                        saved_sha256s.append(sha256)
                        saved_filenames.add(img_ref["filename"])
                        await _write_creation_record(sha256)
                except Exception as exc:
                    logger.error("ComfyUI image save error: %s", exc)

    # fill in images missed by WebSocket from /history
    history_images = await comfy.fetch_history(prompt_id)
    for img_ref in history_images:
        if img_ref.get("filename") in saved_filenames:
            continue
        try:
            img_bytes = await comfy.fetch_image(
                img_ref["filename"],
                img_ref.get("subfolder", ""),
                img_ref.get("type", "output"),
            )
            sha256 = await _save_and_register_comfy_image(
                img_bytes, img_ref["filename"], db
            )
            if sha256:
                saved_sha256s.append(sha256)
                await _write_creation_record(sha256)
        except Exception as exc:
            logger.error("ComfyUI history image save error: %s", exc)

    reporter.update(1.0, f"{len(saved_sha256s)} images generated")
    return {"sha256s": saved_sha256s, "prompt_id": prompt_id}


# ── PROMPT lane: VLM prompt refinement ────────────────────────────────────────

async def run_inversion(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    body_dict: dict,
    db,
    ollama,
    event_queue: asyncio.Queue,
) -> None:
    """PROMPT lane runner — streams inversion events into event_queue."""
    import json as _json
    from ..api.inspire import InversionRequest, _inversion_stream
    from ..runtime_config import get_runtime_config

    body = InversionRequest(**body_dict)
    cfg = await get_runtime_config(db)
    reporter.indeterminate()

    _abort = asyncio.Event()
    cancel.on_cancel(_abort.set)

    STAGE_PROGRESS = {0: 0.05, 1: 0.20, 2: 0.45, 3: 0.60, 4: 0.75, 5: 0.90}

    try:
        async for sse_str in _inversion_stream(body, db, ollama, cfg):
            if _abort.is_set():
                raise JobCancelled()
            await event_queue.put(sse_str)
            try:
                evt = _json.loads(sse_str.removeprefix("data: ").strip())
                if evt.get("type") == "stage":
                    p = STAGE_PROGRESS.get(evt.get("stage"), None)
                    if p is not None:
                        reporter.update(p, evt.get("label", ""))
                elif evt.get("type") == "done":
                    reporter.update(1.0, "Done")
            except Exception:
                pass
    except JobCancelled:
        await event_queue.put('data: {"type":"cancelled"}\n\n')
        raise
    except Exception as exc:
        await event_queue.put(f'data: {{"type":"error","message":{str(exc)!r}}}\n\n')
        raise
    finally:
        await event_queue.put(None)


async def run_brainstorm(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    body_dict: dict,
    db,
    ollama,
    event_queue: asyncio.Queue,
) -> None:
    """PROMPT lane runner — streams brainstorm events into event_queue."""
    from ..api.inspire import BrainstormRequest, _brainstorm_stream
    from ..runtime_config import get_runtime_config

    body = BrainstormRequest(**body_dict)
    cfg = await get_runtime_config(db)
    reporter.indeterminate()

    _abort = asyncio.Event()
    cancel.on_cancel(_abort.set)

    try:
        async for sse_str in _brainstorm_stream(body.sha256s, body.extra_tags, db, ollama, cfg, lang=body.lang):
            if _abort.is_set():
                raise JobCancelled()
            await event_queue.put(sse_str)
    except JobCancelled:
        await event_queue.put('data: {"type":"cancelled"}\n\n')
        raise
    except Exception as exc:
        await event_queue.put(f'data: {{"type":"error","message":{str(exc)!r}}}\n\n')
        raise
    finally:
        reporter.update(1.0, "Done")
        await event_queue.put(None)


async def run_refine_prompt(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    body_dict: dict,
    db,
    ollama,
    spooler,
    comfy,
    token_queue: asyncio.Queue,
) -> None:
    """PROMPT lane runner — generates a prompt via VLM and streams events into token_queue.
    Always puts a None sentinel at the end, whether done, cancelled, or errored.
    """
    # lazy import — avoids circular import with api.ai
    from ..api.ai import (
        RefineRequest,
        _WD14_MUST_INCLUDE_THRESHOLD,
        _resolve_weights,
        _build_vlm_prompt,
        _clean_markdown,
        _strip_stray_negative,
        _parse_positive_negative,
        _check_natural_prose,
        _remove_forced_tags,
        _translate_instruction,
        _translate_and_classify,
        _extract_literal_directives,
        _inject_literal_directives,
        _parse_detailed_output,
    )
    from ..ai.tile_image import create_tile_image
    from ..runtime_config import get_runtime_config
    from ..spooler.models import JobLane

    body = RefineRequest(**body_dict)

    def _put(event: dict | None) -> None:
        token_queue.put_nowait(event)

    # event for cancel signal (can be set synchronously from on_cancel handler)
    _abort = asyncio.Event()
    cancel.on_cancel(_abort.set)

    # look up source image seed (only when auto_submit and use_ref_seed)
    seed_for_gen: int | None = None
    if body.use_ref_seed and body.auto_submit and body.sha256s:
        doc = await db.get(body.sha256s[0])
        if doc:
            seed_for_gen = (doc.get("model_info") or {}).get("seed")

    # ── direct_prompt bypass ──────────────────────────────────────────────────
    if body.direct_prompt is not None:
        positive = body.direct_prompt.strip()
        if not positive:
            _put({"type": "error", "message": "direct_prompt is empty"})
            _put(None)
            return
        negative = (body.direct_negative_prompt or "").strip()
        _put({"type": "done", "positive": positive, "negative": negative,
               "auto_submit": body.auto_submit, "prose_missing": False})
        if body.auto_submit and body.workflow_name:
            try:
                gen_job_id = _submit_gen_direct(spooler, comfy, db, body, positive, negative, seed=seed_for_gen)
                _put({"type": "comfy_job_id", "job_id": gen_job_id})
            except Exception as exc:
                _put({"type": "error", "message": f"Generation job error: {exc}"})
        _put(None)
        return

    # 1. load images
    reporter.indeterminate()
    context_parts: list[str] = []
    image_bytes_list: list[bytes] = []
    weights = _resolve_weights(body.sha256s[:6], body.weights)
    loaded_indices: list[int] = []

    for idx, sha256 in enumerate(body.sha256s[:6]):
        cancel.raise_if_set()
        doc = await db.get(sha256)
        if not doc:
            continue
        lines: list[str] = []
        prompt_txt = doc.get("positive_prompt", "")
        if prompt_txt:
            lines.append(f"Prompt: {prompt_txt}")
        wd14 = doc.get("wd14_tags", [])
        wd14_scores = doc.get("wd14_tags_scores", [])
        if wd14_scores and len(wd14_scores) == len(wd14):
            scored_pairs = list(zip(wd14, wd14_scores))
            must_tags = [t for t, s in scored_pairs if s >= _WD14_MUST_INCLUDE_THRESHOLD]
            if must_tags:
                lines.append(f"Must include these tags verbatim: {', '.join(must_tags)}")
            remaining = [t for t, s in scored_pairs if s < _WD14_MUST_INCLUDE_THRESHOLD]
            if remaining:
                lines.append(f"Reference tags: {', '.join(remaining[:30])}")
        elif wd14:
            lines.append(f"Auto-tags: {', '.join(wd14[:40])}")
        if lines:
            context_parts.append("\n".join(lines))
            loaded_indices.append(idx)
        fp = Path(doc.get("path", ""))
        if fp.exists():
            image_bytes_list.append(fp.read_bytes())

    if not context_parts and not image_bytes_list:
        _put({"type": "error", "message": "No valid images found"})
        _put(None)
        return

    # 2. tile image
    tile_bytes = create_tile_image(image_bytes_list) if image_bytes_list else b""
    images_for_vlm = [tile_bytes] if tile_bytes else []

    # 3. build VLM prompt
    cfg = await get_runtime_config(db)
    options = {"temperature": body.temperature, "num_ctx": body.num_ctx}

    annotated_parts: list[str] = []
    for part_idx, (ctx, img_idx) in enumerate(zip(context_parts, loaded_indices)):
        pct = round(weights[img_idx] * 100)
        annotated_parts.append(f"[Image {part_idx + 1} — influence weight: {pct}%]\n{ctx}")
    context = "\n\n---\n\n".join(annotated_parts)

    # 3a. instruction pre-processing: translate → separate literals from NL
    literals: list[dict] = []
    nl_instruction = body.instruction
    if body.instruction and body.instruction_mode != "none":
        if body.instruction_mode == "basic":
            instr_en = await _translate_instruction(
                body.instruction, ollama, model=cfg["vlm_model"]
            )
            nl_instruction, literals = _extract_literal_directives(instr_en)
        elif body.instruction_mode == "enhanced":
            _, nl_instruction, literals = await _translate_and_classify(
                body.instruction, ollama, model=cfg["vlm_model"]
            )

    vlm_prompt = _build_vlm_prompt(
        context,
        nl_instruction,
        body.prompt_style,
        body.negative_prompt,
        instruction_framing=(body.instruction_mode != "none"),
    )

    # 4. Ollama stream → token_queue
    accumulated_tokens: list[str] = []
    reporter.update(0.05, "VLM generating...")
    try:
        async for event in ollama.generate_vlm_stream(
            vlm_prompt, images_for_vlm, model=cfg["vlm_model"], options=options
        ):
            if _abort.is_set():
                raise JobCancelled()
            _put(event)
            if event["type"] == "token":
                accumulated_tokens.append(event["text"])
    except JobCancelled:
        _put({"type": "cancelled"})
        _put(None)
        return
    except Exception as exc:
        logger.error("Ollama stream error in run_refine_prompt: %s", exc)
        _put({"type": "error", "message": str(exc)})
        _put(None)
        return

    # 5. parse text
    raw_text = "".join(accumulated_tokens)

    if body.prompt_style == "detailed":
        # Parse 8-section format BEFORE _clean_markdown strips ** bold markers
        if body.negative_prompt:
            # Extract 8 sections from full raw text — do NOT split on POSITIVE: first,
            # as that would discard everything before the POSITIVE: label.
            parsed = _parse_detailed_output(raw_text)
            if parsed:
                positive = _clean_markdown(parsed)
            else:
                # Fallback: no 8-section structure found — use POSITIVE: block directly
                positive_raw, _ = _parse_positive_negative(raw_text)
                positive = _clean_markdown(positive_raw)
            neg_m = re.search(r"NEGATIVE:\s*(.*?)$", raw_text, re.S | re.I)
            negative = _clean_markdown(neg_m.group(1).strip()) if neg_m else ""
        else:
            raw_stripped = _strip_stray_negative(raw_text)
            parsed = _parse_detailed_output(raw_stripped)
            positive = _clean_markdown(parsed if parsed else raw_stripped)
            negative = ""
    elif body.negative_prompt:
        positive_raw, negative_raw = _parse_positive_negative(raw_text)
        positive = _clean_markdown(positive_raw)
        negative = _clean_markdown(negative_raw)
    else:
        positive = _clean_markdown(_strip_stray_negative(raw_text))
        negative = ""

    removal_tags = {t.lower().replace(' ', '_') for t in cfg.get("prompt_removal_tags", [])}
    positive, removed_tags = _remove_forced_tags(
        positive,
        removal_tags,
        all_lines=(body.prompt_style in ("detailed", "danbooru")),
    )

    # 5b. inject literal directives (text overlays etc.) bypassing VLM
    if literals:
        positive = _inject_literal_directives(positive, literals)

    prose_missing = body.prompt_style == "natural" and not _check_natural_prose(positive)

    _put({
        "type": "done",
        "positive": positive,
        "negative": negative,
        "auto_submit": body.auto_submit,
        "prose_missing": prose_missing,
        "removed_tags": removed_tags,
        "injected_literals": literals,
    })

    # 6. auto_submit: queue a ComfyUI generation job
    if body.auto_submit and body.workflow_name:
        try:
            gen_job_id = _submit_gen_direct(spooler, comfy, db, body, positive, negative, seed=seed_for_gen)
            _put({"type": "comfy_job_id", "job_id": gen_job_id})
        except Exception as exc:
            _put({"type": "error", "message": f"Generation job error: {exc}"})

    reporter.update(1.0, "Done")
    _put(None)


def _submit_gen_direct(spooler, comfy, db, body, positive: str, negative: str, seed: int | None = None) -> str:
    """Submit a ComfyUI job to the GENERATION lane and return its job_id (no request object needed)."""
    from ..spooler.models import JobLane
    return spooler.submit(
        JobLane.GENERATION,
        f"comfy_generate ({body.workflow_name})",
        run_generation,
        meta={
            "sha256s": body.sha256s[:6],
            "positive_preview": positive[:300],
            "negative_preview": (negative or "")[:200],
            "workflow_name": body.workflow_name,
            "batch_count": body.batch_count,
            "positive_prompt": positive,
            "negative_prompt": negative or "",
            "instruction": body.instruction,
            "prompt_style": body.prompt_style,
            "weights": body.weights,
            "direct_prompt": body.direct_prompt,
            "direct_negative_prompt": body.direct_negative_prompt or "",
            "temperature": body.temperature,
            "num_ctx": body.num_ctx,
            "inspire_context": body.inspire_context,
            "seed": seed,
        },
        comfy=comfy,
        db=db,
        workflow_name=body.workflow_name,
        positive=positive,
        negative=negative,
        positive_node_id=body.positive_node_id,
        negative_node_id=body.negative_node_id,
        batch_count=body.batch_count,
        seed=seed,
        creation_meta={
            "sha256s": body.sha256s[:6],
            "weights": body.weights,
            "instruction": body.instruction,
            "prompt_style": body.prompt_style,
            "temperature": body.temperature,
            "num_ctx": body.num_ctx,
            "workflow_name": body.workflow_name,
            "batch_count": body.batch_count,
            "positive_prompt": positive,
            "negative_prompt": negative or "",
            "direct_prompt": bool(body.direct_prompt),
            "inspire_context": body.inspire_context,
            "seed": seed,
        },
    )
