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
import json
import logging
import math
import re
from pathlib import Path

from ..spooler.models import CancelToken, JobCancelled, ProgressReporter

_PRIORITY_ALIGNMENT = -10

logger = logging.getLogger(__name__)

# Person/subject count tags used as safety-net anchors in refine output.
_PERSON_COUNT_TAGS = frozenset({
    "1girl", "1boy", "solo", "2girls", "2boys", "3girls", "3boys",
    "multiple_girls", "multiple_boys", "6+girls", "6+boys",
    "1other", "2others", "multiple_others",
})


def _ensure_subject_anchor(tags_positive: str, raw_docs: list, wd14_scores_key: str = "wd14_tags_scores") -> str:
    """If Pass 1 output lacks a subject count tag, prepend the highest-confidence one from WD14."""
    tag_set = {t.strip().lower() for t in tags_positive.split(",") if t.strip()}
    if tag_set & _PERSON_COUNT_TAGS:
        return tags_positive
    best_tag, best_score = "", 0.0
    for doc, _idx in raw_docs:
        wd14 = doc.get("wd14_tags", [])
        scores = doc.get(wd14_scores_key) or []
        for tag, score in zip(wd14, scores):
            if tag in _PERSON_COUNT_TAGS and score > best_score:
                best_tag, best_score = tag, score
    if best_tag and best_score >= 0.40:
        return f"{best_tag}, {tags_positive}"
    return tags_positive


# Quality meta-tags that must never appear in invoke positive/negative prompts.
# Spirits must not generate them, but we strip as a safety net.
_INVOKE_QUALITY_METATAG_BLOCKLIST = frozenset({
    "masterpiece", "best_quality", "highres", "ultra_detailed", "4k", "8k", "hd", "uhd",
    "worst_quality", "low_quality", "bad_anatomy", "extra_limbs", "extra_fingers",
    "missing_fingers", "missing_arms", "mutated_hands", "jpeg_artifacts", "blurry",
    "out_of_focus", "watermark", "signature", "text", "username", "error",
})


def _strip_quality_metatags(tags: str) -> str:
    parts = [t.strip() for t in tags.split(",") if t.strip()]
    return ", ".join(t for t in parts if t.lower() not in _INVOKE_QUALITY_METATAG_BLOCKLIST)


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

    # auto-start AI pipeline if new files were registered (CPU tagging stage
    # first; it chains the embed stage on the EMBEDDING lane)
    if spooler is not None and ollama is not None and scan_state.added > 0:
        from ..spooler.models import JobLane
        if spooler.is_lane_active(JobLane.TAGGING):
            spooler.submit(
                JobLane.TAGGING,
                "ai_tagging_post_scan",
                run_pipeline_tagging,
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
    from ..runtime_config import get_runtime_config
    cfg = await get_runtime_config(db)
    reporter.indeterminate()
    task = asyncio.create_task(run_scan(db, concurrency=int(cfg.get("scan_concurrency", 8))))
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
        if spooler.is_lane_active(JobLane.TAGGING):
            spooler.submit(
                JobLane.TAGGING,
                "ai_tagging_post_scan",
                run_pipeline_tagging,
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

async def run_pipeline_tagging(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama=None,
    sha256s: list[str] | None = None,
    spooler=None,
) -> dict:
    """TAGGING lane (CPU only, never auto-paused). WD14 + colors for pending docs,
    then chains the embed stage on the EMBEDDING lane."""
    from ..ai.pipeline import tagging_state, run_ai_pipeline
    from ..spooler.models import JobLane

    def _on_cancel() -> None:
        tagging_state.cancelled = True

    cancel.on_cancel(_on_cancel)
    reporter.indeterminate()

    task = asyncio.create_task(
        run_ai_pipeline(db, ollama, sha256s, pause_checkpoint=cancel.pause_checkpoint, stage="tagging")
    )
    cancel.on_cancel(task.cancel)

    while not task.done():
        total = tagging_state.total
        processed = tagging_state.processed
        if total > 0:
            reporter.update(processed / total, f"{processed}/{total} tagged")
        else:
            reporter.indeterminate()
        await asyncio.sleep(0.5)

    try:
        await task
    except asyncio.CancelledError:
        raise JobCancelled()

    if cancel._event.is_set():
        raise JobCancelled()

    # Chain the embed stage. Submit even while EMBEDDING is auto-paused — the job
    # waits at the pause gate and runs when generation finishes (that overlap is
    # the whole point of the tagging stage). Dedup against an existing queued job.
    if spooler is not None and ollama is not None:
        _already_queued = any(
            j["lane"] == "embed" and j["state"] == "queued" and j["title"].startswith("ai_pipeline")
            for j in spooler.snapshot()
        )
        if not _already_queued:
            spooler.submit(
                JobLane.EMBEDDING,
                "ai_pipeline",
                run_pipeline,
                db=db,
                ollama=ollama,
                sha256s=sha256s,
                spooler=spooler,
            )

    return {"processed": tagging_state.processed, "errors": tagging_state.errors}


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

    # stage="embed" reuses tags written by the tagging stage and falls back to
    # the full per-doc path when tags are missing — behaviorally equivalent to
    # the old full pipeline for untagged docs.
    task = asyncio.create_task(
        run_ai_pipeline(db, ollama, sha256s, pause_checkpoint=cancel.pause_checkpoint, stage="embed")
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

    # auto-continue: pipeline now processes all pending items in one run via Queue,
    # so re-submission is no longer needed.

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


async def run_expand_theme(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    body_dict: dict,
    db,
    ollama,
    event_queue: asyncio.Queue,
) -> None:
    """PROMPT lane runner — expands a theme into 4 section tags via VLM, puts done event on event_queue."""
    from ..api.inspire import (
        ExpandThemeRequest, _sse, _normalize_section,
        _EXPAND_THEME_PROMPT, _parse_json_from_llm, create_tile_image,
    )
    from ..runtime_config import get_runtime_config

    body = ExpandThemeRequest(**body_dict)
    cfg = await get_runtime_config(db)
    reporter.indeterminate()

    image_bytes_list: list[bytes] = []
    for sha256 in body.sha256s[:4]:
        doc = await db.get(sha256)
        if doc:
            fp = Path(doc.get("path", ""))
            if fp.exists():
                image_bytes_list.append(fp.read_bytes())
    tile_bytes = create_tile_image(image_bytes_list) if image_bytes_list else None

    safe_theme = body.theme.replace("{", "{{").replace("}", "}}")
    prompt = _EXPAND_THEME_PROMPT.format(theme=safe_theme)

    try:
        if tile_bytes:
            raw = await ollama.generate_vlm(prompt, [tile_bytes], model=cfg["vlm_model"])
        else:
            raw = await ollama.generate_text(prompt, model=cfg["vlm_model"])
        data = _parse_json_from_llm(raw) or {}
    except Exception as exc:
        await event_queue.put(_sse({"type": "error", "message": str(exc)}))
        await event_queue.put(None)
        raise

    await event_queue.put(_sse({
        "type": "done",
        "character":  _normalize_section(data.get("character", "")),
        "background": _normalize_section(data.get("background", "")),
        "props":      _normalize_section(data.get("props", "")),
        "action":     _normalize_section(data.get("action", "")),
        "mood":       _normalize_section(data.get("mood", "")),
        "camera":     _normalize_section(data.get("camera", "")),
    }))
    reporter.update(1.0, "Done")
    await event_queue.put(None)


async def _find_conflict_tags(
    instruction_en: str,
    source_tags: list[str],
    db,
    ollama,
    model: str,
) -> set[str]:
    """Return the subset of source_tags that contradict the given instruction.

    Uses semantic search to find instruction-aligned tags for context, then asks
    the LLM (text-only, no image) to identify conflicting source tags.
    Falls back to empty set on any error.
    """
    try:
        instr_vec = await ollama.embed(instruction_en)
    except Exception as exc:
        logger.warning("_find_conflict_tags embed failed: %s", exc)
        return set()

    desired_names: list[str] = []
    try:
        desired_hits = await db.search_wd14_vocab(instr_vec, limit=40)
        desired_names = [h["name"] for h in desired_hits]
    except Exception as exc:
        logger.warning("_find_conflict_tags vocab search failed: %s", exc)

    prompt = (
        f'Instruction: "{instruction_en}"\n'
        f'Semantically aligned tags for this instruction: {", ".join(desired_names[:20])}\n'
        f'Source tags: {", ".join(source_tags[:80])}\n\n'
        'Which source tags DIRECTLY CONTRADICT the instruction?\n'
        'Rules: Only list tags that conflict (e.g. wrong hair color, wrong style). '
        'Tags unrelated to the instruction must NOT be listed.\n'
        'Return ONLY valid JSON: {"conflict": ["tag1", "tag2"]}'
    )
    try:
        resp = await ollama.generate_text(prompt, model=model, fmt="json")
        data = json.loads(resp)
        return set(data.get("conflict", []))
    except Exception as exc:
        logger.warning("_find_conflict_tags LLM call failed: %s", exc)
        return set()


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
        _build_natural_tags_prompt,
        _build_natural_prose_prompt,
        _build_natural_visual_script_prompt,
        _build_weighted_wd14_context,
        _clean_markdown,
        _strip_stray_negative,
        _parse_positive_negative,
        _parse_visual_script_sections,
        _strip_visual_script_markers,
        _build_all_must,
        _inject_wd14_must_tags,
        _correct_prose_wd14_conflicts,
        _enforce_wd14_on_cat_tags,
        _check_natural_prose,
        _remove_forced_tags,
        _translate_instruction,
        _translate_and_classify,
        _extract_literal_texts,
        _append_literal_texts,
        _parse_detailed_output,
        _sample_mutation_tags,
        _REFINE_CAT_FIELDS,
    )
    from ..api.inspire import _parse_json_from_llm, _split_tags
    from ..ai.tile_image import create_tile_image
    from ..runtime_config import get_runtime_config
    from ..spooler.models import JobLane

    body = RefineRequest(**body_dict)

    def _put(event: dict | None) -> None:
        token_queue.put_nowait(event)

    def _phase(code: str, progress: float, text: str) -> None:
        reporter.update(progress, text)
        _put({"type": "phase", "code": code, "progress": progress})

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

    # 1a. load images (doc metadata only — context built after conflict detection)
    _phase("loadingImages", 0.02, "Loading reference images...")
    image_bytes_list: list[bytes] = []
    weights = _resolve_weights(body.sha256s[:6], body.weights)
    raw_docs: list[tuple[dict, int]] = []  # (doc, original_idx)

    for idx, sha256 in enumerate(body.sha256s[:6]):
        cancel.raise_if_set()
        doc = await db.get(sha256)
        if not doc:
            continue
        raw_docs.append((doc, idx))
        fp = Path(doc.get("path", ""))
        if fp.exists():
            image_bytes_list.append(fp.read_bytes())

    if not raw_docs and not image_bytes_list:
        _put({"type": "error", "message": "No valid images found"})
        _put(None)
        return

    # 2. tile image
    tile_bytes = create_tile_image(image_bytes_list) if image_bytes_list else b""
    images_for_vlm = [tile_bytes] if tile_bytes else []

    # 3. build VLM prompt
    cfg = await get_runtime_config(db)
    options = {"temperature": body.temperature, "num_ctx": body.num_ctx}

    # 3a. instruction pre-processing: separate literals from NL
    # LLM/VLM understands Japanese directly — translation is unnecessary overhead.
    # basic: regex-only literal extraction (instant); enhanced: LLM classify only.
    literal_texts: list[str] = []
    nl_instruction = body.instruction
    if body.instruction and body.instruction_mode != "none":
        # Strip text-render commands from instruction so VLM never sees them;
        # they are appended as text "X", text_on_image at the end of post-processing.
        literal_texts, nl_instruction = _extract_literal_texts(body.instruction)
        if body.instruction_mode == "enhanced" and nl_instruction:
            _phase("translatingInstruction", 0.05, "Analyzing instruction...")
            _, nl_instruction, _ = await _translate_and_classify(
                nl_instruction, ollama, model=cfg["vlm_model"]
            )

    # 3b. conflict tag suppression: identify WD14 tags that contradict user instruction
    conflict_tags: set[str] = set()
    if body.suppress_conflict_tags and nl_instruction:
        all_source_tags = [t for doc, _ in raw_docs for t in doc.get("wd14_tags", [])]
        if all_source_tags:
            _phase("analyzingConflicts", 0.08, "Analyzing tag conflicts...")
            conflict_tags = await _find_conflict_tags(
                nl_instruction, all_source_tags, db, ollama, cfg["vlm_model"]
            )

    # 1b. build context with common/unique WD14 tag decomposition.
    # Transmute: divergence loosens the shared-trait lock and injects mutation tags.
    divergence = max(0.0, min(1.0, body.divergence))
    effective_common_ratio = body.wd14_common_ratio * (1.0 - divergence * 0.7)

    _phase("buildingPrompt", 0.10, "Building prompt context...")
    context, wd14_analysis = _build_weighted_wd14_context(
        raw_docs,
        weights,
        conflict_tags,
        common_ratio=effective_common_ratio,
        unique_count=body.wd14_unique_count,
        roles=body.roles or None,
    )

    # Emotional register shift: state the references' dominant emotion and the target
    if body.emotion_shift:
        from ..ai.emotion_tagger import EMOTION_DIMENSIONS
        if body.emotion_shift in EMOTION_DIMENSIONS:
            sums = {d: 0.0 for d in EMOTION_DIMENSIONS}
            scored_docs = 0
            for doc, _ in raw_docs:
                if doc.get(f"emotion_{EMOTION_DIMENSIONS[0]}") is None:
                    continue
                scored_docs += 1
                for d in EMOTION_DIMENSIONS:
                    sums[d] += float(doc.get(f"emotion_{d}") or 0.0)
            current_line = ""
            if scored_docs:
                dom = max(sums, key=sums.get)
                current_line = f"Current dominant register: {dom} ({sums[dom] / scored_docs:.2f}).\n"
            context += (
                "\n\n---\n\n[EMOTIONAL REGISTER SHIFT]\n"
                f"{current_line}"
                f"Rewrite the mood, lighting, color, and atmosphere toward: {body.emotion_shift}. "
                "Keep the subject, pose, and composition of the references intact."
            )

    mutation_tags: list[str] = []
    if divergence > 0:
        _phase("mutatingTags", 0.14, "Sampling mutation tags...")
        mutation_tags = await _sample_mutation_tags(db, ollama, wd14_analysis, divergence)
        if mutation_tags:
            pct = round(divergence * 100)
            context += (
                f"\n\n---\n\n[MUTATION TAGS — divergence {pct}%]\n"
                f"{', '.join(mutation_tags)}\n"
                f"Replace roughly {pct}% of the style / scene / lighting elements of the "
                "references with these mutation tags. Keep the subject count, pose, and "
                "character identity from the references intact."
            )

    instruction_framing = body.instruction_mode != "none"

    async def _stream_vlm(
        prompt: str,
        phase_start: float = 0.0,
        phase_end: float = 1.0,
        expected_tokens: int = 200,
        phase_text: str = "",
    ) -> str:
        """Run one VLM call (with images), forwarding tokens to token_queue."""
        tokens: list[str] = []
        async for event in ollama.generate_vlm_stream(
            prompt, images_for_vlm, model=cfg["vlm_model"], options=options
        ):
            if _abort.is_set():
                raise JobCancelled()
            _put(event)
            if event["type"] == "token":
                tokens.append(event["text"])
                n = len(tokens)
                if n % 8 == 0:
                    frac = min(n / expected_tokens, 0.97)
                    reporter.update(phase_start + (phase_end - phase_start) * frac, phase_text)
        return "".join(tokens)

    async def _stream_text(
        prompt: str,
        phase_start: float = 0.0,
        phase_end: float = 1.0,
        expected_tokens: int = 200,
        phase_text: str = "",
        options_override: dict | None = None,
    ) -> str:
        """Run a text-only LLM call (no images), forwarding tokens to token_queue."""
        tokens: list[str] = []
        async for event in ollama.generate_text_stream(
            prompt, model=cfg["vlm_model"], options=options_override or options
        ):
            if _abort.is_set():
                raise JobCancelled()
            _put(event)
            if event["type"] == "token":
                tokens.append(event["text"])
                n = len(tokens)
                if n % 8 == 0:
                    frac = min(n / expected_tokens, 0.97)
                    reporter.update(phase_start + (phase_end - phase_start) * frac, phase_text)
        return "".join(tokens)

    # 4. Ollama call(s) → token_queue
    context_story = ""
    cat_tags: dict[str, list[str]] = {f: [] for f in _REFINE_CAT_FIELDS}
    _all_must = _build_all_must(wd14_analysis)

    # Variation fan-out (natural style only): prose pass runs N times on a temperature ladder
    _FANOUT_TEMPS = (0.5, 0.8, 1.1)
    variation_count = max(1, min(3, body.variation_count)) if body.prompt_style == "natural" else 1
    fanout_stories: list[tuple[str, float]] = []  # (story, temperature) for extra variants

    try:
        if body.prompt_style == "natural":
            # Pass 1: tags only — a small VLM handles one focused task reliably.
            tags_prompt = _build_natural_tags_prompt(
                context, nl_instruction, body.negative_prompt,
                instruction_framing=instruction_framing,
            )
            _phase("writingTags", 0.20, "Writing tags...")
            tags_raw = await _stream_vlm(tags_prompt, 0.20, 0.55, 80, "Writing tags...")
            if body.negative_prompt:
                tags_positive, negative = _parse_positive_negative(tags_raw)
            else:
                tags_positive, negative = _strip_stray_negative(tags_raw), ""
            tags_positive = _clean_markdown(tags_positive)
            negative = _clean_markdown(negative)
            tags_positive = _ensure_subject_anchor(tags_positive, raw_docs)
            # Inject WD14 must_unique directly into tag line ("2回" reinforcement).
            # Skipped at high divergence — re-anchoring all reference tags would undo the mutation.
            if divergence <= 0.5:
                tags_positive = _inject_wd14_must_tags(tags_positive, wd14_analysis)

            # Pass 2: Visual Script — prose with inline danbooru tags + per-category labeled sections.
            _put({"type": "token", "text": "\n\n"})
            vs_prompt = _build_natural_visual_script_prompt(
                context, nl_instruction, tags_positive,
                instruction_framing=instruction_framing,
            )
            _phase("writingDescription", 0.55, "Writing Visual Script...")
            _pass2_end = 0.90 if variation_count == 1 else 0.70
            _main_options = (
                {**options, "temperature": _FANOUT_TEMPS[0]} if variation_count > 1 else None
            )
            vs_raw = await _stream_text(
                vs_prompt, 0.55, _pass2_end, 500, "Writing Visual Script...",
                options_override=_main_options,
            )

            # Parse visual script: split prose from labeled tag sections
            context_story, vs_cat_tags = _parse_visual_script_sections(vs_raw)
            context_story = _strip_visual_script_markers(context_story)

            # Post-processing: correct WD14 conflicts in prose and category tags
            if _all_must:
                context_story = _correct_prose_wd14_conflicts(context_story, _all_must)
            for _f in _REFINE_CAT_FIELDS:
                cat_tags[_f] = vs_cat_tags.get(_f, [])
            if _all_must:
                cat_tags = _enforce_wd14_on_cat_tags(cat_tags, _all_must)

            prose_missing = len(context_story.split()) < 30
            positive = f"{tags_positive}\n\n{context_story}"

            # Extra fan-out variants: same tags, hotter prose interpretations
            for _vi, _vt in enumerate(_FANOUT_TEMPS[1:variation_count]):
                _put({"type": "token", "text": "\n\n---\n\n"})
                _v_start = 0.70 + 0.10 * _vi
                _phase("writingDescription", _v_start, f"Variant prose (temp {_vt})...")
                _v_raw = await _stream_text(
                    vs_prompt, _v_start, _v_start + 0.10, 500,
                    f"Variant prose (temp {_vt})...",
                    options_override={**options, "temperature": _vt},
                )
                _v_story, _ = _parse_visual_script_sections(_v_raw)
                _v_story = _strip_visual_script_markers(_v_story)
                if _all_must:
                    _v_story = _correct_prose_wd14_conflicts(_v_story, _all_must)
                fanout_stories.append((f"{tags_positive}\n\n{_v_story}", _vt))
        else:
            vlm_prompt = _build_vlm_prompt(
                context, nl_instruction, body.prompt_style, body.negative_prompt,
                instruction_framing=instruction_framing,
            )
            _phase("generatingPrompt", 0.20, "VLM generating...")
            raw_text = await _stream_vlm(vlm_prompt, 0.20, 0.90, 150, "VLM generating...")
            prose_missing = False

            if body.prompt_style == "detailed":
                # Split off trailing per-category JSON block before parsing 8 sections
                _cb = raw_text.rfind("```json")
                if _cb >= 0:
                    _sections_text = raw_text[:_cb].strip()
                    _cat_json = _parse_json_from_llm(raw_text[_cb:])
                    for _f in _REFINE_CAT_FIELDS:
                        _v = _cat_json.get(_f, "")
                        cat_tags[_f] = _split_tags(_v) if _v else []
                else:
                    _sections_text = raw_text

                # Parse 8-section format BEFORE _clean_markdown strips ** bold markers
                if body.negative_prompt:
                    # Extract 8 sections from full raw text — do NOT split on POSITIVE: first,
                    # as that would discard everything before the POSITIVE: label.
                    parsed = _parse_detailed_output(_sections_text)
                    if parsed:
                        positive = _clean_markdown(parsed)
                    else:
                        # Fallback: no 8-section structure found — use POSITIVE: block directly
                        positive_raw, _ = _parse_positive_negative(_sections_text)
                        positive = _clean_markdown(positive_raw)
                    neg_m = re.search(r"NEGATIVE:\s*(.*?)$", _sections_text, re.S | re.I)
                    negative = _clean_markdown(neg_m.group(1).strip()) if neg_m else ""
                else:
                    _raw_stripped = _strip_stray_negative(_sections_text)
                    parsed = _parse_detailed_output(_raw_stripped)
                    positive = _clean_markdown(parsed if parsed else _raw_stripped)
                    negative = ""
            elif body.negative_prompt:
                positive_raw, negative_raw = _parse_positive_negative(raw_text)
                positive = _clean_markdown(positive_raw)
                negative = _clean_markdown(negative_raw)
            else:
                positive = _clean_markdown(_strip_stray_negative(raw_text))
                negative = ""
            # WD14 post-processing for danbooru/detailed (mirrors natural branch)
            if body.prompt_style == "danbooru" and divergence <= 0.5:
                positive = _inject_wd14_must_tags(positive, wd14_analysis)
            if _all_must:
                if body.prompt_style == "detailed":
                    positive = _correct_prose_wd14_conflicts(positive, _all_must)
                cat_tags = _enforce_wd14_on_cat_tags(cat_tags, _all_must)
    except JobCancelled:
        _put({"type": "cancelled"})
        _put(None)
        return
    except Exception as exc:
        logger.error("Ollama stream error in run_refine_prompt: %s", exc)
        _put({"type": "error", "message": str(exc)})
        _put(None)
        return

    # 5. post-process: forced-tag removal + literal directive injection
    _phase("parsingOutput", 0.90, "Parsing output...")
    removal_tags = {t.lower().replace(' ', '_') for t in cfg.get("prompt_removal_tags", [])}
    positive, removed_tags = _remove_forced_tags(
        positive,
        removal_tags,
        all_lines=(body.prompt_style in ("detailed", "danbooru")),
    )

    # 5b. append literal text render tags (text "X") to end of positive prompt
    if literal_texts:
        positive = _append_literal_texts(positive, literal_texts)

    # 5c. process fan-out variants with the same post-processing as the main prompt
    variants: list[dict] = []
    for _v_pos, _vt in fanout_stories:
        _v_pos, _ = _remove_forced_tags(_v_pos, removal_tags, all_lines=False)
        if literal_texts:
            _v_pos = _append_literal_texts(_v_pos, literal_texts)
        variants.append({"positive": _v_pos, "temperature": _vt})

    _put({
        "type": "done",
        "positive": positive,
        "negative": negative,
        "auto_submit": body.auto_submit,
        "prose_missing": prose_missing,
        "removed_tags": removed_tags,
        "injected_literals": [{"text": t} for t in literal_texts],
        "context_story": context_story,
        "wd14_analysis": wd14_analysis,
        "divergence": divergence,
        "mutation_tags": mutation_tags,
        "variants": variants,
        **cat_tags,
    })

    # 6. auto_submit: queue a ComfyUI generation job (one per fan-out variant)
    if body.auto_submit and body.workflow_name:
        try:
            _phase("queuingGeneration", 0.97, "Queuing generation job...")
            gen_job_id = _submit_gen_direct(spooler, comfy, db, body, positive, negative, seed=seed_for_gen)
            _put({"type": "comfy_job_id", "job_id": gen_job_id})
            for v in variants:
                _submit_gen_direct(spooler, comfy, db, body, v["positive"], negative, seed=seed_for_gen)
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


# ── Invoke lane runners ────────────────────────────────────────────────────────

async def run_invoke_axis_decompose(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama,
    spooler,
    session_id: str,
    user_intent: str,
    emoji_codes: list,
    mood_sliders: dict,
    color_hex: list,
    person_gender: str = "",
    person_count: str = "",
    camera_shot: str = "",
    camera_angle: str = "",
    pro_topic: str = "",
    pro_sections: dict | None = None,
    pro_prompt: str = "",
    session_manager,
    resonance_mode: bool = False,
    frontier_mode: bool = False,
    emotion: str = "",
) -> dict:
    """PROMPT lane. Decompose user intent into structured axes."""
    from ..invoke.axis_decomposer import decompose_axes

    reporter.indeterminate()
    cancel.raise_if_set()

    from ..invoke.vocab_bank import get_character_danbooru_hints
    from ..invoke.axis_decomposer import _EMOJI_MEANINGS

    person_present = bool(person_gender or person_count)
    pro_prompt_spec: dict | None = None

    if pro_prompt:
        # pro_prompt 指定時: まずビジュアル仕様に展開し、そのスローガンを使用
        from ..invoke.vocab_bank import expand_pro_prompt, get_topic_tags
        pro_prompt_spec = await expand_pro_prompt(pro_prompt, pro_topic, pro_sections, ollama)
        effective_slogan = pro_prompt_spec["slogan"]
        # topic_tags は引き続き取得（テーマ整合の WD14 補完用）
        anchor_text = pro_topic or pro_prompt
        topic_tags = await get_topic_tags(db, ollama, anchor_text, pro_sections) if anchor_text else []
    elif pro_topic:
        # pro_topic のみ: 従来通り topic_tags + slogan を合成
        from ..invoke.vocab_bank import get_topic_tags, synthesize_slogan
        topic_tags = await get_topic_tags(db, ollama, pro_topic, pro_sections)
        effective_slogan = await synthesize_slogan(pro_topic, pro_sections, topic_tags, ollama)
    else:
        topic_tags = []
        effective_slogan = user_intent  # Light mode: determine_slogan が通常通り実行

    hint_query = effective_slogan or " ".join(
        _EMOJI_MEANINGS.get(e, e) for e in (emoji_codes or [])
    )
    try:
        character_hints = await get_character_danbooru_hints(
            db, ollama, slogan=hint_query, person_present=person_present
        )
    except Exception as _e:
        logger.debug("[invoke] character_hints failed: %s", _e)
        character_hints = {}

    # Echoes of Resonance: blend starred-image taste hints into character_hints
    if resonance_mode:
        from ..invoke.vocab_bank import compute_resonance_hints
        try:
            resonance = await compute_resonance_hints(db)
            for cat, tags in resonance.items():
                seen = set(character_hints.get(cat, []))
                character_hints[cat] = character_hints.get(cat, []) + [
                    t for t in tags if t not in seen
                ]
            logger.debug("[invoke] resonance hints merged: %s", {k: len(v) for k, v in resonance.items()})
        except Exception as _re:
            logger.warning("[invoke] resonance_hints failed: %s", _re)
    # Frontier: blend never-seen vocabulary far from the taste centroid (exclusive with resonance)
    elif frontier_mode:
        from ..invoke.vocab_bank import compute_frontier_hints
        try:
            frontier = await compute_frontier_hints(db)
            for cat, tags in frontier.items():
                seen = set(character_hints.get(cat, []))
                character_hints[cat] = character_hints.get(cat, []) + [
                    t for t in tags if t not in seen
                ]
            logger.debug("[invoke] frontier hints merged: %s", {k: len(v) for k, v in frontier.items()})
        except Exception as _fe:
            logger.warning("[invoke] frontier_hints failed: %s", _fe)

    # Emotion register: bias the mood axis toward the chosen emotional dimension
    emotion_hint: str | None = None
    if emotion:
        from ..invoke.vocab_bank import get_emotion_hints
        try:
            em_tags = await get_emotion_hints(db, ollama, emotion)
            if em_tags:
                seen = set(character_hints.get("mood", []))
                character_hints["mood"] = character_hints.get("mood", []) + [
                    t for t in em_tags if t not in seen
                ]
            emotion_hint = (
                f"Target emotional register: {emotion}. "
                "Infuse the mood and lighting axes with this feeling."
            )
        except Exception as _ee:
            logger.warning("[invoke] emotion_hints failed: %s", _ee)

    axes = await decompose_axes(
        ollama,
        user_intent=effective_slogan,
        emoji_codes=emoji_codes,
        mood_sliders=mood_sliders,
        color_hex=color_hex,
        context_hint=emotion_hint,
        person_gender=person_gender,
        person_count=person_count,
        camera_shot=camera_shot,
        camera_angle=camera_angle,
        character_hints=character_hints,
        pro_sections=pro_sections or {},
        pro_prompt_spec=pro_prompt_spec,
    )
    # スピリットが元の NL テキストを参照できるよう _user_intent を元お題に上書き
    axes['_user_intent'] = pro_topic or pro_prompt or user_intent
    if topic_tags:
        axes['_topic_tags'] = topic_tags
    if pro_prompt_spec:
        axes['_story_directive']  = pro_prompt_spec.get("story_directive", "")
        axes['_supplement_tags']  = pro_prompt_spec.get("supplement_tags", [])
    if pro_prompt:
        axes['_pro_prompt_raw'] = pro_prompt  # ベースタグを生値で保存（スピリットに verbatim 渡し）

    # スピリット別シーン多様性のため N バリアントを生成（Light モードは slogan をトピックとして使用。
    # 先頭バリアントはベースシーンに近いため faithful は概ね元のシーンを保つ）
    variant_topic = pro_topic or pro_prompt or axes.get('_slogan') or user_intent
    if variant_topic:
        from ..invoke.axis_decomposer import generate_scene_variants
        _session = session_manager.get_session(session_id)
        enabled_count = len(_session.enabled_spirits) if _session else 5
        # scene_anchor があれば pro_topic に付加してより具体的なベースシーンを渡す
        if pro_prompt_spec and pro_prompt_spec.get("scene_anchor"):
            variant_topic = f"{variant_topic}\n{pro_prompt_spec['scene_anchor']}"
        scene_variants = await generate_scene_variants(ollama, axes, variant_topic, n=enabled_count)
        axes['_scene_variants'] = scene_variants

    reporter.update(1.0, "Axes ready")
    await session_manager.on_axis_done(session_id, axes)
    return {"axes": axes}


async def run_invoke_spirit_compose(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    session_id: str,
    spirit_name: str,
    axes: dict,
    vocab_hints: dict,
    axis_tag_hints: list | None = None,
    locale: str = "en",
    rebel_inversion: bool = True,
    avoid_tags: list | None = None,
    respin_boost: float = 0.0,
    session_manager,
) -> dict:
    """PROMPT lane. Generate prompt for one Spirit via Ollama."""
    import json as _json
    import re as _re

    from ..invoke.spirit_loader import load_spirit

    reporter.indeterminate()
    cancel.raise_if_set()

    spirit = load_spirit(spirit_name)
    sys_prompt = spirit["system_prompt"]

    # Localize monologue: replace the English-only schema placeholder with a Japanese instruction.
    # A single phrase swap ("in English" → "in Japanese") is too subtle for the LLM to follow
    # reliably, so we replace the full placeholder and add an explicit reminder in the user msg.
    if locale == "ja":
        sys_prompt = _re.sub(
            r'"internal_monologue": "<[^>]*in English[^>]*>"',
            '"internal_monologue": "<このスピリットの内なる声を日本語で1行>"',
            sys_prompt,
        )

    # Build user message
    style_str = ", ".join(axes.get("style", []))
    user_msg_parts: list[str] = []
    # Front-load the respin "don't repeat this" instruction so the LLM actually
    # notices it — buried at the tail of a long prompt it barely lands.
    if avoid_tags:
        user_msg_parts.append(
            "⚠️ RESPIN — DO NOT REPRODUCE THE PREVIOUS ATTEMPT ⚠️\n"
            f"Previously used tags: [{', '.join(avoid_tags)}]\n"
            "Pick a genuinely different scene interpretation, pose, lighting, "
            "palette, and supporting tag set. Keep the character identity and "
            "the locked axes intact."
        )
    user_msg_parts.extend([
        f"slogan: {axes.get('_slogan', '')}",
        f"user_intent: {axes.get('_user_intent', '')}",
        f"axes:",
        f"  subject: {axes.get('subject', '')}",
        f"  character_detail: {axes.get('character_detail', '')}",
        f"  action: {axes.get('action', '')}",
        f"  scene: {axes.get('scene', '')}",
        f"  mood: {axes.get('mood', '')}",
        f"  lighting: {axes.get('lighting', '')}",
        f"  composition: {axes.get('composition', '')}",
        f"  style: [{style_str}]",
        f"  palette: {axes.get('palette', '')}",
        f"  accessories: {axes.get('accessories', '')}",
    ])
    if spirit.get("needs_vocab_hint"):
        stranger_tags = ", ".join(vocab_hints.get("stranger", []))
        lunatic_tags = ", ".join(vocab_hints.get("lunatic", []))
        if spirit_name == "stranger" and stranger_tags:
            user_msg_parts.append(f"guest_tags: [{stranger_tags}]")
        elif spirit_name == "lunatic" and lunatic_tags:
            user_msg_parts.append(f"wild_tags: [{lunatic_tags}]")

    if axis_tag_hints:
        user_msg_parts.append(
            f"SUGGESTED DANBOORU TAGS (semantically close to the axes — "
            f"use these as Danbooru vocabulary hints; include only those consistent with the scene axes, "
            f"skip any that would over-anchor a specific location): [{', '.join(axis_tag_hints)}]"
        )
    # BASE TAGS: ユーザー指定の Danbooru タグ — verbatim で全て含める
    if axes.get("_pro_prompt_raw"):
        user_msg_parts.append(
            "BASE TAGS (the user's own Danbooru tags — include ALL of these verbatim, unchanged, "
            "as the foundation of your danbooru_tags output. Do NOT omit, rename, or substitute any): "
            f"[{axes['_pro_prompt_raw']}]"
        )
    # STORY DIRECTIVE: お題 × pro_prompt から生成したナラティブ指令
    if axes.get("_story_directive"):
        user_msg_parts.append(
            f"STORY DIRECTIVE (narrative context from topic × user prompt — add tags that develop "
            f"this story ON TOP of the BASE TAGS): {axes['_story_directive']}"
        )
    # SUPPLEMENT TAGS: story 分析から提案された追加タグ
    supplement = axes.get("_supplement_tags", [])
    if supplement:
        user_msg_parts.append(
            f"SUGGESTED SUPPLEMENT TAGS (from story analysis — incorporate fitting ones): "
            f"[{', '.join(supplement)}]"
        )
    user_msg_parts.append(
        "Your danbooru_tags MUST cover all axes: subject+action, scene+environment, "
        "mood+atmosphere, lighting, palette, and style."
    )
    user_msg_parts.append(
        "MINIMUM TAG COUNT: danbooru_tags MUST contain at least 50 comma-separated tags "
        "(BASE TAGS verbatim + story/atmosphere/action/scene additions)."
    )
    if locale == "ja":
        user_msg_parts.append(
            'IMPORTANT: Write the "internal_monologue" value in Japanese (日本語で書くこと). This is required.'
        )

    if spirit_name == "rebel" and not rebel_inversion:
        user_msg_parts.append(
            "[INVERSION OVERRIDE]: Express Counter's contrarian perspective WITHOUT inverting any axis. "
            "Find an unexpected but beautiful angle on the theme that produces a coherent, high-quality image. "
            "Set inverted_axis to null."
        )

    full_prompt = f"{sys_prompt}\n\n---\n\n" + "\n".join(user_msg_parts)

    session = session_manager.get_session(session_id)
    ollama = session.ollama if session else None
    if not ollama:
        await session_manager.on_spirit_error(session_id, spirit_name, "Session expired")
        return {}

    # Spirit-native temperature × session heat (+ respin boost) drives sampling divergence
    heat = getattr(session, "heat", 1.0) or 1.0
    temperature = float(spirit.get("temperature", 0.8)) * heat + respin_boost
    temperature = max(0.1, min(1.6, temperature))

    # Fresh Ollama seed every compose so identical prompt text still samples
    # a different trajectory — critical for making respins visibly different.
    import random as _random
    seed = _random.randint(1, (1 << 31) - 1)

    logger.debug("[invoke] spirit_compose start: %s temp=%.2f seed=%d", spirit_name, temperature, seed)
    try:
        raw = await ollama.generate_text(
            full_prompt, fmt="json",
            options={"temperature": temperature, "seed": seed},
        )
    except Exception as e:
        logger.warning("[invoke] spirit_compose ollama failed (%s): %s", spirit_name, e)
        await session_manager.on_spirit_error(session_id, spirit_name, f"LLM error: {e}")
        return {}

    raw = _re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = _re.sub(r"\s*```$", "", raw.strip())

    try:
        result = _json.loads(raw)
    except Exception as e:
        # Malformed JSON used to be papered over with a subject-only fallback,
        # which produced a silently anemic prompt. Surface it instead so the
        # user can respin (same UX path as a content_policy block).
        logger.warning("[invoke] spirit_compose JSON parse failed (%s): %s | raw=%r", spirit_name, e, raw[:200])
        await session_manager.on_spirit_error(
            session_id, spirit_name, "LLM returned malformed JSON — please retry."
        )
        return {}

    # ── BM25 normalize Spirit danbooru_tags against Danbooru vocabulary ──────
    try:
        from ..ai.wd14 import normalize_tag_string
        raw_tags = (result.get("danbooru_tags") or "")
        if raw_tags:
            result["danbooru_tags"] = normalize_tag_string(raw_tags)
    except Exception:
        pass  # BM25 index not ready yet — skip silently

    # ── Content safety check on LLM output (VLM-delegated) ───────────────────
    from ..invoke.content_guard import check_spirit_output, BLOCK_MESSAGE
    if await check_spirit_output(result, ollama):
        logger.warning("[invoke] content_guard blocked spirit output: %s", spirit_name)
        await session_manager.on_spirit_error(session_id, spirit_name, BLOCK_MESSAGE)
        return {}

    # ── Adequacy guard: catch severely short / empty prompts and surface them
    # the same way content_policy does, so the user sees the Retry button
    # instead of a silently-degraded rendered image.
    nl_len = len((result.get("natural_language") or "").strip())
    tag_count = sum(1 for t in (result.get("danbooru_tags") or "").split(",") if t.strip())
    if nl_len < 30 and tag_count < 15:
        logger.warning(
            "[invoke] spirit produced degenerate prompt: %s (nl=%d, tags=%d)",
            spirit_name, nl_len, tag_count,
        )
        await session_manager.on_spirit_error(
            session_id, spirit_name, "Prompt generation failed — please retry."
        )
        return {}

    logger.debug("[invoke] spirit_compose done: %s → nl=%r", spirit_name, str(result.get("natural_language", ""))[:60])
    reporter.update(1.0, f"{spirit_name} composed")

    cancel.raise_if_set()
    await session_manager.on_spirit_composed(session_id, spirit_name, result)
    return result


async def _save_and_register_invoke_image(img_bytes: bytes, original_name: str, db) -> str | None:
    """Save an invoke-generated image to generated_images_dir/invoke/ (watcher skips auto-pipeline there)."""
    import hashlib as _hl
    from datetime import datetime as _dt
    from pathlib import Path as _Path

    from ..config import settings as _settings
    from ..scanner.scanner import register_image as _register_image

    sha256 = _hl.sha256(img_bytes).hexdigest()
    gen_dir = _settings.generated_images_dir / "invoke"
    gen_dir.mkdir(parents=True, exist_ok=True)

    suffix = _Path(original_name).suffix or ".png"
    ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    filename = f"invoke_{ts}_{sha256[:8]}{suffix}"
    path = gen_dir / filename

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, path.write_bytes, img_bytes)

    try:
        await _register_image(path, db)
        logger.debug("[invoke] image registered: %s", filename)
        return sha256
    except Exception as exc:
        logger.error("[invoke] register_image failed: %s", exc)
        return None


async def run_invoke_image_generate(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    session_id: str,
    spirit_name: str,
    prompt_result: dict,
    workflow_name: str,
    seed: int | None,
    session_manager,
) -> dict:
    """GEN lane. Generate image for one Spirit via ComfyUI."""
    import random as _random

    reporter.indeterminate()

    session = session_manager.get_session(session_id)
    if not session:
        await session_manager.on_spirit_error(session_id, spirit_name, "Session expired")
        return {}
    comfy = session.comfy
    db = session.db

    if not workflow_name:
        logger.warning("[invoke] image_generate: no workflow configured for %s", spirit_name)
        await session_manager.on_spirit_error(session_id, spirit_name, "No workflow configured")
        return {}

    if seed is None:
        seed = _random.randint(0, (1 << 64) - 1)

    prompt_mode = getattr(session, "prompt_mode", "danbooru+natural")
    person_tags = getattr(session, "person_tags", "")

    nl      = (prompt_result.get("natural_language") or "").strip()
    db_tags = _strip_quality_metatags((prompt_result.get("danbooru_tags") or "").strip())

    # Treat very short natural_language as absent (Spirit fallback produces subject string only)
    nl_usable = nl if len(nl) >= 30 else ""

    if prompt_mode == "natural":
        body_str = nl_usable or db_tags
    elif prompt_mode == "danbooru":
        body_str = db_tags
    else:  # danbooru+natural (default)
        body_str = (nl_usable + "\n" + db_tags).strip() if nl_usable else db_tags

    positive = (person_tags + "\n" + body_str).strip() if person_tags else body_str
    spirit_negative = (prompt_result.get("negative_supplement") or "").strip()
    pro_negative = getattr(session, "pro_negative", "").strip()
    negative = ", ".join(filter(None, [pro_negative, spirit_negative]))

    logger.debug("[invoke] image_generate start: %s seed=%d wf=%s", spirit_name, seed, workflow_name)

    try:
        wf = comfy.load_workflow(workflow_name)
        patched = comfy.patch_workflow(wf, positive.strip(), negative.strip(), "", "", 1, seed=seed)
        prompt_id = await comfy.queue_prompt(patched)
    except Exception as e:
        logger.warning("[invoke] image_generate ComfyUI setup failed (%s): %s", spirit_name, e)
        await session_manager.on_spirit_error(session_id, spirit_name, f"ComfyUI setup error: {e}")
        return {}

    reporter.update(0.0, "Waiting in ComfyUI queue...")

    queued = True

    async def _cancel_comfy() -> None:
        if queued:
            try:
                await comfy.delete_from_queue(prompt_id)
            except Exception:
                pass
        try:
            await comfy.interrupt()
        except Exception:
            pass

    cancel.on_cancel(lambda: asyncio.create_task(_cancel_comfy()))

    sha256: str | None = None

    try:
        async for event in comfy.stream_progress(prompt_id):
            cancel.raise_if_set()
            queued = False

            if event["type"] == "comfy_progress":
                v = event.get("value", 0)
                m = event.get("max", 1)
                reporter.update(v / max(m, 1), f"Step {v}/{m}")
                await session_manager.on_spirit_progress(session_id, spirit_name, v, m)

            elif event["type"] == "comfy_output":
                for img_ref in event.get("images", []):
                    cancel.raise_if_set()
                    try:
                        img_bytes = await comfy.fetch_image(
                            img_ref["filename"],
                            img_ref.get("subfolder", ""),
                            img_ref.get("type", "output"),
                        )
                        saved = await _save_and_register_invoke_image(img_bytes, img_ref["filename"], db)
                        if saved and not sha256:
                            sha256 = saved
                    except Exception as exc:
                        logger.error("[invoke] image fetch/save error (%s): %s", spirit_name, exc)
    except Exception as e:
        logger.warning("[invoke] image_generate stream failed (%s): %s", spirit_name, e)
        if not sha256:
            await session_manager.on_spirit_error(session_id, spirit_name, f"Generation failed: {e}")
            return {}

    if sha256:
        logger.debug("[invoke] image_generate done: %s sha256=%s", spirit_name, sha256[:12])
        reporter.update(1.0, f"{spirit_name} image ready")
        await session_manager.on_image_done(session_id, spirit_name, sha256)
    else:
        await session_manager.on_spirit_error(session_id, spirit_name, "Image generation produced no output")

    return {"sha256": sha256}


async def run_invoke_session_finalize(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    session_id: str,
    spirit_sha256s: dict,
    db,
    ollama,
    session_manager,
) -> dict:
    """EMBEDDING lane. 全 invoke 生成画像に AI pipeline を一括適用後、各 spirit の alignment を submit。"""
    from ..ai.pipeline import run_ai_pipeline
    from ..spooler.models import JobLane

    reporter.indeterminate()
    sha256s = list(spirit_sha256s.values())

    # run_ai_pipeline はべき等のため直接呼び出す（処理済み画像はスキップされる）
    try:
        task = asyncio.create_task(
            run_ai_pipeline(db, ollama, sha256s, pause_checkpoint=cancel.pause_checkpoint)
        )
        cancel.on_cancel(task.cancel)
        await task
    except asyncio.CancelledError:
        raise JobCancelled()
    except Exception as exc:
        logger.warning("[invoke] session_finalize pipeline failed: %s", exc)

    reporter.update(0.85, "pipeline done, scoring novelty")

    # Surprise スコア: 最近傍ライブラリ画像との埋め込み距離（セッション兄弟は除外）。
    # VLM 不要の純ベクトル演算 — lunatic/stranger の逸脱を可視化する。
    session = session_manager.get_session(session_id)
    sibling_set = set(spirit_sha256s.values())
    for spirit_name, sha256 in spirit_sha256s.items():
        cancel.raise_if_set()
        novelty: float | None = None
        try:
            similar = await db.search_similar(sha256, n_results=8)
            top_sim = next(
                (d["_score"] for d in similar if d.get("sha256") not in sibling_set),
                None,
            )
            if top_sim is not None:
                novelty = round(max(0.0, min(1.0, 1.0 - float(top_sim))) * 100, 1)
        except Exception as exc:
            logger.debug("[invoke] novelty score failed for %s: %s", sha256[:12], exc)
        if novelty is None:
            continue
        if session and (spirit := session.spirits.get(spirit_name)):
            spirit.novelty_score = novelty
        try:
            await db.set_payload(sha256, {"genesis.novelty_at_genesis": novelty})
        except Exception as exc:
            logger.debug("[invoke] novelty payload write failed for %s: %s", sha256[:12], exc)

    reporter.update(0.9, "novelty done, submitting alignment")

    # Pipeline 完了後、各 spirit の alignment を EVALUATION ランに submit
    if session:
        for spirit_name, sha256 in spirit_sha256s.items():
            spirit = session.spirits.get(spirit_name)
            if spirit:
                spirit.status = "scoring"
            job_id = session.spooler.submit(
                JobLane.EVALUATION,
                f"invoke.align/{spirit_name[:3]}",
                run_invoke_alignment_score,
                meta={"session_id": session_id, "spirit": spirit_name},
                sha256=sha256,
                session_id=session_id,
                spirit_name=spirit_name,
                session_manager=session_manager,
                db=db,
                ollama=ollama,
            )
            if spirit:
                spirit.job_ids.append(job_id)

    reporter.update(1.0, "finalize done")
    return {"processed": len(sha256s)}


async def run_invoke_alignment_score(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    sha256: str,
    session_id: str,
    spirit_name: str,
    session_manager,
    db,
    ollama,
) -> dict:
    """EVAL lane. Alignment-score one Invoke-generated image, then mark spirit done."""
    from ..alignment.evaluator import AlignmentEvaluator

    reporter.indeterminate()
    cancel.raise_if_set()

    evaluator = AlignmentEvaluator(db, ollama)
    score: float | None = None
    try:
        result = await evaluator.evaluate_one(sha256)
        score = result.score if result.status == "done" else None
    except Exception as e:
        logger.warning("invoke alignment failed for %s: %s", sha256, e)

    reporter.update(1.0, f"score={score:.2f}" if score is not None else "scored")
    await session_manager.on_spirit_done(session_id, spirit_name, score)
    return {"score": score}


async def run_invoke_respin(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    session_id: str,
    spirit_name: str,
    session_manager,
) -> dict:
    """PROMPT lane. Compute vocab/axis hints then compose a single spirit for respin."""
    import asyncio as _asyncio
    from ..invoke.vocab_bank import get_vocab_hints, get_axis_semantic_tags

    reporter.indeterminate()
    cancel.raise_if_set()

    session = session_manager.get_session(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found or expired")

    axis_tags: list[str] = []
    for v in (session.axes or {}).values():
        if isinstance(v, list):
            axis_tags.extend(v)
        elif isinstance(v, str) and v:
            axis_tags.extend(v.replace(",", " ").split())

    _vh, _ah = await _asyncio.gather(
        get_vocab_hints(session.db, session.ollama, axis_tags, wildness=session.wildness),
        get_axis_semantic_tags(session.db, session.ollama, session.axes or {}),
        return_exceptions=True,
    )
    vocab_hints = _vh if not isinstance(_vh, Exception) else {"stranger": [], "lunatic": []}
    axis_tag_hints = _ah if not isinstance(_ah, Exception) else []

    spirit_vocab = vocab_hints if spirit_name in ("stranger", "lunatic") else {"stranger": [], "lunatic": []}

    # Respin memory: steer away from previous attempts instead of re-rolling the same dice
    avoid_tags: list[str] = []
    spirit_state = session.spirits.get(spirit_name)
    history = spirit_state.history if spirit_state else []
    if history:
        prev_wild: set[str] = set()
        seen: set[str] = set()
        for prev in history:
            prev_wild.update(prev.get("wild_tags_used") or [])
            for f in ("background_tags", "object_tags", "lighting_tags", "pose_tags"):
                for t in (prev.get(f) or "").split(","):
                    t = t.strip()
                    if t and t.lower() not in seen:
                        avoid_tags.append(t)
                        seen.add(t.lower())
        avoid_tags = avoid_tags[-15:]  # most recent attempts matter most
        # Draw fresh vocabulary: drop guest/wild tags already tried
        if prev_wild:
            spirit_vocab = {
                k: [t for t in v if t not in prev_wild]
                for k, v in spirit_vocab.items()
            }

    reporter.update(0.25, f"Hints ready — composing {spirit_name}")
    cancel.raise_if_set()

    # Stepped respin temperature ramp. The previous +0.1 * len(history) was
    # too gentle to visibly reroll on the first respin — this jumps to +0.25
    # immediately so the user sees a genuinely different sampling trajectory.
    n = len(history)
    if n == 0:
        respin_boost = 0.0
    elif n == 1:
        respin_boost = 0.25
    elif n == 2:
        respin_boost = 0.40
    else:
        respin_boost = 0.55

    return await run_invoke_spirit_compose(
        reporter,
        cancel,
        session_id=session_id,
        spirit_name=spirit_name,
        axes=session.axes or {},
        vocab_hints=spirit_vocab,
        axis_tag_hints=axis_tag_hints,
        locale=session.locale,
        rebel_inversion=session.rebel_inversion if spirit_name == "rebel" else True,
        avoid_tags=avoid_tags,
        respin_boost=respin_boost,
        session_manager=session_manager,
    )


async def run_invoke_enhance_prompt(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama,
    text: str,
    tag_count: int = 25,
    event_queue: asyncio.Queue,
) -> None:
    """PROMPT lane. Embed text, find semantic WD14 tags, refine via LLM. Puts done event on event_queue."""
    import json as _json
    import re as _re
    from ..invoke.vocab_bank import _is_species_tag

    reporter.update(0.1, "Embedding text...")
    cancel.raise_if_set()

    vec = await ollama.embed(text)

    reporter.update(0.4, "Searching vocab...")
    cancel.raise_if_set()

    hits = await db.search_wd14_vocab(vec, min_freq=0.005, max_freq=1.0, limit=tag_count * 2)
    candidate_names = [h["name"] for h in hits if not _is_species_tag(h["name"])]

    reporter.update(0.6, "Refining tags...")
    cancel.raise_if_set()

    system_prompt = (
        "You are an expert Danbooru image-tag curator. "
        "The user provides a scene description (possibly in Japanese). "
        "You receive a candidate tag list sourced by semantic search. "
        "Your job: select the most fitting tags, add obvious missing ones (e.g. 1girl), "
        "and write a polished English visual description (1-2 sentences). "
        "Output ONLY valid JSON, no markdown fences:\n"
        '{"tags": "tag1, tag2, ...", "natural_language": "..."}'
    )
    user_msg = (
        f"User description: {text}\n\n"
        f"Candidate tags: {', '.join(candidate_names)}\n\n"
        f"Select {tag_count} tags and write the natural_language description."
    )
    full_prompt = f"{system_prompt}\n\n{user_msg}"

    try:
        raw = await ollama.generate_text(full_prompt, fmt="json")
        raw = _re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=_re.MULTILINE)
        raw = _re.sub(r"\s*```$", "", raw.strip(), flags=_re.MULTILINE)
        result = _json.loads(raw)
    except Exception as e:
        logger.warning("enhance_prompt LLM parse failed: %s — returning raw hits", e)
        result = {
            "tags": ", ".join(candidate_names[:tag_count]),
            "natural_language": text,
        }

    raw_tags = [t.strip() for t in result.get("tags", "").split(",")]
    result["tags"] = ", ".join(t for t in raw_tags if t and not _is_species_tag(t))

    result_dict = {
        "type":             "done",
        "tags":             result.get("tags", ""),
        "natural_language": result.get("natural_language", ""),
        "vocab_hits":       [h for h in hits[:tag_count] if not _is_species_tag(h["name"])],
    }
    reporter.update(1.0, "Done")
    await event_queue.put(f"data: {json.dumps(result_dict)}\n\n")
    await event_queue.put(None)


async def run_invoke_oracle_compose(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama,
    topic: str = "",
    roulette: bool = False,
    daily_oracle_date: str = "",
) -> dict:
    """PROMPT lane. Draw the oracle's daily theme and decompose axes.

    All Ollama (GPU) work of the daily oracle lives here so the SYNC lane
    stays CPU/I-O only."""
    from ..invoke.axis_decomposer import decompose_axes
    from ..invoke.vocab_bank import get_recent_adopted_tags

    reporter.indeterminate()
    cancel.raise_if_set()

    # Roulette: draw today's theme instead of repeating a static topic.
    # Alternates "comfort day" (recent taste) and "frontier day" (unexplored vocabulary).
    if not topic and roulette:
        from datetime import date as _date
        from ..invoke.vocab_bank import compute_frontier_hints, synthesize_slogan

        try:
            day = _date.fromisoformat(daily_oracle_date)
        except ValueError:
            day = _date.today()
        season = ("winter", "winter", "spring", "spring", "spring", "summer",
                  "summer", "summer", "autumn", "autumn", "autumn", "winter")[day.month - 1]

        drawn_tags: list[str] = []
        if day.timetuple().tm_yday % 2 == 0:
            mode = "a comforting scene close to the user's recent taste"
            try:
                recent = await get_recent_adopted_tags(db, days=14)
                drawn_tags = [t for t, _ in sorted(recent.items(), key=lambda x: -x[1])[:4]]
            except Exception as e:
                logger.warning("oracle roulette comfort tags failed: %s", e)
        else:
            mode = "an unexplored frontier scene unlike anything in the user's library"
            try:
                fh = await compute_frontier_hints(db, n_tags=8)
                drawn_tags = (fh.get("mood", []) + fh.get("scene", []) + fh.get("character", []))[:3]
            except Exception as e:
                logger.warning("oracle roulette frontier tags failed: %s", e)

        topic = await synthesize_slogan(f"A {season} day — {mode}", None, drawn_tags, ollama)
        logger.info("[invoke] oracle roulette topic: %r (tags=%s)", topic[:80], drawn_tags)

    context_hint = None
    if not topic:
        try:
            recent = await get_recent_adopted_tags(db, days=7)
            if recent:
                top = sorted(recent.items(), key=lambda x: -x[1])[:5]
                top_tags = ", ".join(t for t, _ in top)
                context_hint = (
                    f"The user has recently gravitated toward: {top_tags}. "
                    f"Today, offer a striking counterpoint to this established pattern — "
                    f"something they have not seen before."
                )
        except Exception as e:
            logger.warning("daily oracle context hint failed: %s", e)

    cancel.raise_if_set()
    axes = await decompose_axes(ollama, user_intent=topic, context_hint=context_hint)
    reporter.update(1.0, "Oracle axes ready")
    return {"axes": axes, "topic": topic}


async def run_invoke_daily_oracle(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama,
    comfy,
    spooler,
    session_manager,
    daily_oracle_date: str,
    workflow_name: str = "",
    topic: str = "",
    roulette: bool = False,
) -> dict:
    """SYNC lane (low priority). Generate today's 5 oracle images.

    LLM work (theme + axis decomposition) is delegated to a PROMPT lane job so
    this SYNC job stays CPU/I-O only; cross-lane waiting cannot deadlock (the
    PROMPT worker is independent of SYNC)."""
    from ..invoke.session_manager import SPIRIT_ORDER
    from ..spooler.models import JobLane

    reporter.indeterminate()
    cancel.raise_if_set()

    if not workflow_name:
        reporter.update(1.0, "Skipped: no oracle workflow configured")
        return {"skipped": True, "reason": "no workflow"}

    compose_job_id = spooler.submit(
        JobLane.PROMPT,
        "invoke.oracle_compose",
        run_invoke_oracle_compose,
        meta={"daily_oracle_date": daily_oracle_date},
        db=db,
        ollama=ollama,
        topic=topic,
        roulette=roulette,
        daily_oracle_date=daily_oracle_date,
    )
    cancel.on_cancel(lambda: asyncio.create_task(spooler.cancel(compose_job_id)))
    reporter.update(0.05, "Composing oracle axes (PROMPT lane)...")
    compose_result = await spooler.wait(compose_job_id)

    axes = compose_result["axes"]
    axes["_daily_oracle_date"] = daily_oracle_date

    reporter.update(0.1, "Axes ready — launching oracle spirits")
    cancel.raise_if_set()

    session = session_manager.create_session(
        user_intent="[daily oracle]",
        input_mode="daily_oracle",
        workflow_name=workflow_name,
        enabled_spirits=SPIRIT_ORDER,
        db=db,
        ollama=ollama,
        comfy=comfy,
        spooler=spooler,
    )

    await session_manager.on_axis_done(session.session_id, axes)
    reporter.update(0.15, f"Oracle session {session.session_id} launched — awaiting spirits")

    # Wait until the session reaches a terminal state (complete / all-error / cancelled)
    waiter = asyncio.create_task(session.completion.wait())
    cancel.on_cancel(waiter.cancel)
    try:
        await waiter
    except asyncio.CancelledError:
        raise JobCancelled()

    reporter.update(1.0, "Daily oracle complete")
    return {"session_id": session.session_id, "axes": axes}


_LINEAGE_MUTABLE_AXES = ("scene", "mood", "lighting", "palette", "composition", "accessories", "action")


async def run_invoke_lineage(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama,
    session_id: str,
    parent_axes: list[dict],
    mode: str,               # 'evolve' | 'breed'
    mutation: float = 0.3,
    session_manager,
) -> dict:
    """PROMPT lane. Synthesize axes from parent genesis snapshots, then launch spirits.

    evolve — single parent: jitter a `mutation` fraction of the mutable axes by swapping in
             semantic-neighbor tags from the wd14 vocab bank.
    breed  — two parents: merge their axes via a small JSON-in/JSON-out VLM task
             (random per-axis pick as fallback).
    """
    import json as _json
    import random as _random
    import re as _re

    from ..invoke.vocab_bank import _is_species_tag

    reporter.indeterminate()
    cancel.raise_if_set()

    session = session_manager.get_session(session_id)
    if not session:
        raise ValueError(f"Session {session_id} not found or expired")

    axes: dict = dict(parent_axes[0])

    if mode == "breed" and len(parent_axes) >= 2:
        axes_a, axes_b = parent_axes[0], parent_axes[1]
        keys = ("subject", "character_detail", "action", "scene", "mood",
                "lighting", "composition", "style", "palette", "accessories")

        def _axis_str(a: dict, k: str) -> str:
            v = a.get(k, "")
            return ", ".join(v) if isinstance(v, list) else str(v or "")

        prompt = "\n".join([
            "You are merging two image concepts into one child concept.",
            "For each axis, choose the value from A, from B, or write a short fusion of both.",
            "Keep every value concise and Danbooru-compatible. The child must be a coherent single scene.",
            "",
            "AXES A: " + _json.dumps({k: _axis_str(axes_a, k) for k in keys}, ensure_ascii=False),
            "AXES B: " + _json.dumps({k: _axis_str(axes_b, k) for k in keys}, ensure_ascii=False),
            "",
            "Output ONLY valid JSON with exactly these keys, no markdown fences:",
            _json.dumps({k: "<value>" for k in keys}),
        ])
        merged: dict = {}
        try:
            raw = await ollama.generate_text(prompt, fmt="json")
            raw = _re.sub(r"^```(?:json)?\s*", "", raw.strip())
            raw = _re.sub(r"\s*```$", "", raw.strip())
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                merged = {k: str(parsed.get(k, "")).strip() for k in keys}
        except Exception as e:
            logger.warning("[invoke] breed merge VLM failed, falling back to random pick: %s", e)
        if not merged or not any(merged.values()):
            merged = {k: _axis_str(_random.choice((axes_a, axes_b)), k) for k in keys}

        axes = merged
        # style axis is a list downstream
        axes["style"] = [s.strip() for s in str(axes.get("style", "")).split(",") if s.strip()]

    elif mode == "evolve":
        cancel.raise_if_set()
        n_mut = max(1, round(max(0.0, min(1.0, mutation)) * len(_LINEAGE_MUTABLE_AXES)))
        chosen = _random.sample(_LINEAGE_MUTABLE_AXES, n_mut)
        for axis in chosen:
            val = axes.get(axis) or ""
            text = ", ".join(val) if isinstance(val, list) else str(val)
            if not text.strip():
                continue
            try:
                vec = await ollama.embed(text)
                hits = await db.search_wd14_vocab(vec, min_freq=0.01, max_freq=0.8, category=0, limit=30)
                # Skip the nearest hits — they are near-synonyms that barely mutate anything
                pool = [h["name"] for h in hits[10:] if not _is_species_tag(h["name"])]
                if pool:
                    axes[axis] = ", ".join(_random.sample(pool, min(2, len(pool))))
            except Exception as e:
                logger.warning("[invoke] evolve mutation failed for axis %s: %s", axis, e)
        logger.debug("[invoke] evolve mutated axes: %s", chosen)

    axes["_slogan"] = session.user_intent
    axes["_user_intent"] = session.user_intent

    reporter.update(1.0, f"{mode} axes ready")
    await session_manager.on_axis_done(session_id, axes)
    return {"axes": axes, "mode": mode}


# ── SYNC lane: WD14 vocab import ───────────────────────────────────────────────

async def run_import_wd14_vocab(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama,
) -> dict:
    """Parse selected_tags.csv from the WD14 model directory, embed each tag with Ollama,
    and upsert into the wd14_vocab Qdrant collection for semantic search.
    """
    import csv
    from ..config import settings
    from ..invoke.vocab_bank import invalidate_vocab_cache

    csv_path = Path(settings.wd14_model_dir) / "selected_tags.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"selected_tags.csv not found at {csv_path}")

    reporter.indeterminate()

    # Read CSV — category 0 = General tags only
    rows: list[dict] = []
    max_count = 1
    with open(csv_path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if int(row["category"]) != 0:
                continue
            count = int(row["count"])
            rows.append({"id": int(row["tag_id"]), "name": row["name"], "count": count})
            if count > max_count:
                max_count = count

    total = len(rows)
    logger.info("[import_wd14_vocab] %d general tags to embed", total)

    # Embed in batches of 256
    BATCH = 256
    done = 0
    points: list[dict] = []

    for i in range(0, total, BATCH):
        cancel.raise_if_set()
        batch = rows[i:i + BATCH]
        names = [r["name"] for r in batch]
        try:
            vectors = await ollama.embed_batch(names)
        except Exception as e:
            logger.warning("[import_wd14_vocab] embed_batch failed at offset %d: %s", i, e)
            # Retry individually to avoid dropping the whole batch
            vectors = []
            for name in names:
                try:
                    vectors.append(await ollama.embed(name))
                except Exception:
                    vectors.append([0.0] * len(vectors[0]) if vectors else [0.0])

        for row, vec in zip(batch, vectors):
            points.append({
                "id":        row["id"],
                "vector":    vec,
                "name":      row["name"],
                "frequency": round(row["count"] / max_count, 6),
                "category":  0,
                "count":     row["count"],
            })

        done += len(batch)
        reporter.update(done / total, f"埋め込み中 {done}/{total}")

    reporter.update(0.95, "Qdrantに登録中...")
    await db.upsert_wd14_vocab(points)

    invalidate_vocab_cache()

    reporter.update(1.0, f"完了: {len(points)} タグを登録")
    logger.info("[import_wd14_vocab] done: %d tags", len(points))
    return {"imported": len(points)}


async def run_emotion_tag(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    ollama,
    sha256s: list[str] | None = None,
) -> dict:
    """EMBEDDING lane. Assign 12 emotion dimension scores to images using Ollama LLM.

    When sha256s is None, processes all images that lack emotion scores (no emotion_loneliness
    payload field). Results are stored as flat keys: emotion_loneliness, emotion_nostalgia, ...
    """
    from ..ai.emotion_tagger import score_emotions, EMOTION_DIMENSIONS
    from qdrant_client import models as qm

    reporter.indeterminate()
    cancel.raise_if_set()

    if sha256s:
        docs = [doc async for doc in _iter_sha256_docs(db, sha256s)]
    else:
        docs = []
        offset = None
        while True:
            cancel.raise_if_set()
            pts, next_offset = await db._qc.scroll(
                collection_name="images",
                scroll_filter=qm.Filter(must=[
                    qm.IsEmptyCondition(
                        is_empty=qm.PayloadField(key="emotion_loneliness")
                    ),
                ]),
                limit=500,
                offset=offset,
                with_payload=qm.PayloadSelectorInclude(
                    include=["sha256", "positive_prompt", "wd14_tags"]
                ),
                with_vectors=False,
            )
            docs.extend(p.payload for p in pts if p.payload)
            if next_offset is None:
                break
            offset = next_offset

    total = len(docs)
    done = 0
    errors = 0
    reporter.indeterminate()

    cfg = {}
    try:
        from ..runtime_config import get_runtime_config
        cfg = await get_runtime_config(db)
    except Exception:
        pass
    concurrency = int(cfg.get("pipeline_concurrency", 4))
    vlm_model: str | None = cfg.get("vlm_model") or None

    sem = asyncio.Semaphore(concurrency)

    async def process_one(doc: dict) -> None:
        nonlocal done, errors
        sha256 = doc.get("sha256")
        if not sha256:
            return
        async with sem:
            cancel.raise_if_set()
            scores = await score_emotions(
                doc.get("positive_prompt") or "",
                doc.get("wd14_tags") or [],
                ollama,
                model=vlm_model,
            )
            if scores:
                payload = {f"emotion_{dim}": scores[dim] for dim in EMOTION_DIMENSIONS}
                await db.set_payload(sha256, payload)
                done += 1
            else:
                errors += 1
            if total > 0:
                reporter.update(done / total, f"感情タグ付け {done}/{total}")

    await asyncio.gather(*(process_one(doc) for doc in docs), return_exceptions=True)
    logger.info("[emotion_tag] done=%d errors=%d total=%d", done, errors, total)
    return {"done": done, "errors": errors, "total": total}


async def _iter_sha256_docs(db, sha256s: list[str]):
    """Yield payload dicts for a list of sha256 hashes."""
    for sha256 in sha256s:
        doc = await db.get(sha256)
        if doc:
            yield doc


# ── Chronicle (story) pipeline ────────────────────────────────────────────────

async def _save_and_register_chronicle_image(img_bytes: bytes, original_name: str, db) -> str | None:
    """Save a chronicle-generated image to generated_images_dir/Chronicles/.

    Unlike invoke/, this subfolder is NOT skipped by the watcher, so the auto
    AI pipeline (wd14 tagging, embedding) picks these images up like any other
    generated image.
    """
    import hashlib as _hl
    from datetime import datetime as _dt

    from ..config import settings as _settings
    from ..scanner.scanner import register_image as _register_image

    sha256 = _hl.sha256(img_bytes).hexdigest()
    gen_dir = _settings.generated_images_dir / "Chronicles"
    gen_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(original_name).suffix or ".png"
    ts = _dt.now().strftime("%Y%m%d_%H%M%S")
    path = gen_dir / f"chronicle_{ts}_{sha256[:8]}{suffix}"

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, path.write_bytes, img_bytes)
    try:
        await _register_image(path, db)
        return sha256
    except Exception as exc:
        logger.error("[chronicle] register_image failed: %s", exc)
        return None


async def run_chronicle_candidates(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    body_dict: dict,
    db,
    ollama,
    spooler,
    comfy,
    token_queue: asyncio.Queue,
    story_id: str | None = None,
    temperature: float | None = None,
) -> None:
    """PROMPT lane — Chronicle Phase 1: extract vision + pitch three candidates.

    Emits {"type": "candidates", story_id, candidates} then a None sentinel.
    The three candidates diverge along the faithful/rebel/stranger axes and are
    written in the user's locale. A draft story record persists the candidates
    plus the extracted vision context so Phase 2 (expand) can pick up from it
    without redoing Stage 1.

    When story_id is given this is a RESPIN: the draft's stored context is
    reused (Stage 1 skipped), candidates are regenerated at the raised
    temperature, and the previous set is archived in respin_history.
    """
    from ..api.ai import _build_weighted_wd14_context
    from ..runtime_config import get_runtime_config
    from ..story import db as story_db
    from ..story.api import ChronicleRequest
    from ..story.generator import (
        build_candidates_prompt,
        build_vision_prompt,
        character_tags_from_wd14,
        parse_candidates_json,
        split_vision_sections,
    )

    body = ChronicleRequest(**body_dict)
    temp = body.temperature if temperature is None else temperature

    def _put(event: dict | None) -> None:
        token_queue.put_nowait(event)

    def _phase(code: str, progress: float, text: str) -> None:
        reporter.update(progress, text)
        _put({"type": "phase", "code": code, "progress": progress})

    _abort = asyncio.Event()
    cancel.on_cancel(_abort.set)

    try:
        cfg = await get_runtime_config(db)
        vlm_model = body.vlm_model or cfg["vlm_model"]
        options = {"temperature": temp}

        draft = None
        if story_id:
            draft = await story_db.get_story(db, story_id)
            if not draft:
                _put({"type": "error", "message": "Draft story not found"})
                return
        ctx: dict = (draft or {}).get("context") or {}

        if not ctx:
            # ── Stage 1: visual vocabulary ────────────────────────────────────
            _phase("loadingImage", 0.03, "Loading base image...")
            doc = await db.get(body.base_sha256)
            if not doc:
                _put({"type": "error", "message": "Base image not found"})
                return
            fp = Path(doc.get("path", ""))
            if not fp.exists():
                _put({"type": "error", "message": f"Base image file missing: {fp.name}"})
                return
            image_bytes = fp.read_bytes()

            # Ollama VLMs may reject WebP — convert to JPEG for compatibility
            if fp.suffix.lower() == ".webp":
                import io
                from PIL import Image as _PILImage
                _buf = io.BytesIO()
                _PILImage.open(io.BytesIO(image_bytes)).convert("RGB").save(_buf, format="JPEG", quality=95)
                image_bytes = _buf.getvalue()

            wd14_tags = doc.get("wd14_tags") or []
            character_tags = character_tags_from_wd14(wd14_tags)

            _phase("extractingVision", 0.08, "Reading the image...")
            vision_prompt = build_vision_prompt(full_extraction=not character_tags)
            vis_tokens: list[str] = []
            async for event in ollama.generate_vlm_stream(
                vision_prompt, [image_bytes], model=vlm_model, options=options
            ):
                if _abort.is_set():
                    raise JobCancelled()
                _put(event)
                if event["type"] == "token":
                    vis_tokens.append(event["text"])
                    if len(vis_tokens) % 8 == 0:
                        reporter.update(
                            0.08 + 0.35 * min(len(vis_tokens) / 250, 0.97),
                            "Reading the image...",
                        )
            visual_text = "".join(vis_tokens).strip()
            cancel.raise_if_set()

            literal_text, story_hooks = split_vision_sections(visual_text)
            if character_tags:
                character_desc = "[visual tags] " + ", ".join(character_tags)
                scene_desc = literal_text
            else:
                character_desc = literal_text
                scene_desc = literal_text

            wd14_context = ""
            if wd14_tags:
                try:
                    wd14_context, _ = _build_weighted_wd14_context([(doc, 0)], [1.0], set())
                except Exception as exc:
                    logger.warning("[chronicle] wd14 context build failed: %s", exc)

            ctx = {
                "character_desc": character_desc,
                "scene_desc": scene_desc,
                "character_tags": character_tags,
                "wd14_tags": wd14_tags,
                "wd14_context": wd14_context,
                "story_hooks": story_hooks,
                "body": body_dict,
            }

        # ── Stage 2a: three story candidates (single JSON call) ───────────────
        _phase("candidates", 0.55, "Imagining story candidates...")
        raw = await ollama.generate_text(
            build_candidates_prompt(
                character_desc=ctx.get("character_desc", ""),
                scene_desc=ctx.get("scene_desc", ""),
                user_topic=body.user_topic,
                worldview=body.worldview,
                base_axis=body.base_time_axis,
                time_scale=body.time_scale,
                emotion=body.emotion,
                locale=body.locale,
            ),
            model=vlm_model, options=options, fmt="json",
        )
        cancel.raise_if_set()
        candidates = parse_candidates_json(raw)
        if not candidates:
            _put({"type": "error", "message": "Failed to generate story candidates"})
            return

        if story_id:
            hist = list(draft.get("respin_history") or [])
            hist.append({
                "kind": "candidates",
                "temperature": temp,
                "candidates": draft.get("candidates") or [],
            })
            await story_db.set_story_payload(db, story_id, {
                "candidates": candidates,
                "respin_history": hist,
            })
        else:
            payload = story_db.new_story_payload(
                base_image_id=body.base_sha256,
                base_time_axis=body.base_time_axis,
                worldview=body.worldview,
                workflow_name=body.workflow_name,
                group_id=body.group_id,
                time_scale=body.time_scale,
                user_topic=body.user_topic,
                emotion=body.emotion,
                locale=body.locale,
                status="draft",
                candidates=candidates,
                context=ctx,
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


async def run_chronicle_expand(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    story_id: str,
    candidate_id: str,
    time_scale: str,
    temperature: float,
    db,
    ollama,
    spooler,
    comfy,
    token_queue: asyncio.Queue,
) -> None:
    """PROMPT lane — Chronicle Phase 2: expand a chosen candidate, then prompts.

    Loads the draft, expands the selected candidate into the three acts in the
    user's locale, extracts shared identity tags, translates to English when the
    story is Japanese (image prompts are always English), refines per-axis image
    prompts (dropping the base image's WD14 scene tags and running a per-axis
    story-conflict pass), finalises the record, and submits image jobs.

    Re-invoking this (respin) archives the previous final story in
    respin_history before overwriting.
    """
    from ..api.ai import (
        _apply_must_replacements,
        _check_natural_prose,
        _clean_markdown,
        _correct_prose_wd14_conflicts,
        _parse_positive_negative,
        _sample_mutation_tags,
    )
    from ..runtime_config import get_runtime_config
    from ..spooler.models import JobLane
    from ..story import db as story_db
    from ..story.api import ChronicleRequest
    from ..story.generator import (
        AXES,
        base_pose_tags,
        build_axis_prompt,
        build_expand_prompt,
        build_visual_examination_prompt,
        build_overall_prompt,
        build_story_repair_prompt,
        build_story_tags_prompt,
        build_title_prompt,
        build_translation_to_english_prompt,
        collect_prompt_tags,
        identity_tags_for_scale,
        inject_identity_tags,
        is_multi_character,
        parse_english_translation_json,
        parse_story_json,
        parse_story_sections,
        parse_tags_json,
        parse_visual_plan_json,
        remove_conflict_tags,
    )

    def _put(event: dict | None) -> None:
        token_queue.put_nowait(event)

    def _phase(code: str, progress: float, text: str) -> None:
        reporter.update(progress, text)
        _put({"type": "phase", "code": code, "progress": progress})

    _abort = asyncio.Event()
    cancel.on_cancel(_abort.set)

    try:
        draft = await story_db.get_story(db, story_id)
        if not draft:
            _put({"type": "error", "message": "Draft story not found"})
            return
        ctx: dict = draft.get("context") or {}
        body = ChronicleRequest(**(ctx.get("body") or {}))
        body.time_scale = time_scale or body.time_scale
        body.temperature = temperature
        locale = body.locale

        selected = next(
            (c for c in (draft.get("candidates") or []) if c.get("id") == candidate_id),
            None,
        )
        if selected is None:
            _put({"type": "error", "message": f"Candidate {candidate_id!r} not found"})
            return

        cfg = await get_runtime_config(db)
        vlm_model = body.vlm_model or cfg["vlm_model"]
        options = {"temperature": temperature}

        doc = await db.get(body.base_sha256)
        if not doc:
            _put({"type": "error", "message": "Base image not found"})
            return

        character_desc = ctx.get("character_desc", "")
        scene_desc = ctx.get("scene_desc", "")
        character_tags = ctx.get("character_tags") or []
        wd14_tags = ctx.get("wd14_tags") or []
        wd14_context = ctx.get("wd14_context", "")
        story_hooks = ctx.get("story_hooks", "")
        multi = is_multi_character(wd14_tags)
        identity_tags = identity_tags_for_scale(
            character_tags, body.time_scale, multi_character=multi
        )

        # Respin of an already-finalised story: archive the previous version
        if draft.get("status") == "final":
            hist = list(draft.get("respin_history") or [])
            hist.append({
                "kind": "expand",
                "temperature": temperature,
                "title": draft.get("title"),
                "overall": draft.get("overall_story"),
                "axes": draft.get("axes"),
            })
            await story_db.set_story_payload(db, story_id, {"respin_history": hist})

        # ── Divergence: sample "related but absent" mutation tags to weave into
        # the past/future acts. Surfaced to the client so the user can see which
        # tags the divergence dial injected into the story seed.
        divergence = max(0.0, min(1.0, body.divergence))
        mutation_tags: list[str] = []
        if divergence > 0 and wd14_tags:
            _phase("mutatingTags", 0.08, "Sampling mutation tags...")
            try:
                mutation_tags = await _sample_mutation_tags(
                    db, ollama, {"common_tags": list(wd14_tags)}, divergence
                )
            except Exception as exc:
                logger.warning("[chronicle] mutation tag sampling failed: %s", exc)
            if mutation_tags:
                _put({"type": "mutation_tags", "tags": mutation_tags})

        # ── Stage 2b: expand the chosen candidate (user's locale) ─────────────
        _phase("expanding", 0.10, "Expanding the story...")
        _put({"type": "token", "text": "\n\n"})
        expand_prompt = build_expand_prompt(
            selected=selected,
            character_desc=character_desc,
            scene_desc=scene_desc,
            base_axis=body.base_time_axis,
            worldview=body.worldview,
            time_scale=body.time_scale,
            story_hooks=story_hooks,
            divergence=divergence,
            emotion=body.emotion,
            locale=locale,
            mutation_tags=mutation_tags,
            user_topic=body.user_topic,
        )
        story_tokens: list[str] = []
        async for event in ollama.generate_text_stream(
            expand_prompt, model=vlm_model, options=options
        ):
            if _abort.is_set():
                raise JobCancelled()
            _put(event)
            if event["type"] == "token":
                story_tokens.append(event["text"])
                if len(story_tokens) % 8 == 0:
                    reporter.update(
                        0.10 + 0.28 * min(len(story_tokens) / 600, 0.97),
                        "Expanding the story...",
                    )
        raw_story = "".join(story_tokens)
        sections = parse_story_sections(raw_story)
        cancel.raise_if_set()

        if not all(sections.get(a) for a in AXES) or not sections.get("overall"):
            _phase("repairingStory", 0.40, "Repairing story format...")
            try:
                raw_fix = await ollama.generate_text(
                    build_story_repair_prompt(raw_story),
                    model=vlm_model, options=options, fmt="json",
                )
                fixed = parse_story_json(raw_fix)
                for key, value in fixed.items():
                    if value and not sections.get(key):
                        sections[key] = value
            except Exception as exc:
                logger.warning("[chronicle] story repair pass failed: %s", exc)
            cancel.raise_if_set()
        if not all(sections.get(a) for a in AXES):
            missing = [a for a in AXES if not sections.get(a)]
            _put({"type": "error", "message": f"Story acts missing: {', '.join(missing)}"})
            return

        stories = {a: sections[a] for a in AXES}
        title = sections["title"]
        overall = sections["overall"]
        if not title:
            try:
                raw_title = await ollama.generate_text(
                    build_title_prompt(stories), model=vlm_model, options=options,
                )
                title = raw_title.strip().splitlines()[0].strip().strip('*"「」') if raw_title.strip() else ""
            except Exception as exc:
                logger.warning("[chronicle] title fallback failed: %s", exc)
        if not title:
            title = selected.get("title") or "Untitled Chronicle"
        if not overall:
            try:
                raw_ov = await ollama.generate_text(
                    build_overall_prompt(title, stories), model=vlm_model, options=options,
                )
                overall = raw_ov.strip()
            except Exception as exc:
                logger.warning("[chronicle] overall fallback failed: %s", exc)
        # Emit the story in the display (user) language for streaming preview
        _put({"type": "story", "title": title, "overall": overall, "axes": stories})

        # ── English text for Stage 3 (image prompts are always English) ───────
        if locale == "ja":
            title_ja, overall_ja = title, overall
            stories_ja = dict(stories)
            en_map: dict = {}
            try:
                _phase("translating", 0.42, "Translating to English...")
                raw_tr = await ollama.generate_text(
                    build_translation_to_english_prompt(title, overall, stories),
                    model=vlm_model, options=options, fmt="json",
                )
                en_map = parse_english_translation_json(raw_tr)
            except Exception as exc:
                logger.warning("[chronicle] to-English translation failed: %s", exc)
            en_title = en_map.get("title") or title
            en_overall = en_map.get("overall") or overall
            en_stories = {a: (en_map.get(a) or stories[a]) for a in AXES}
        else:
            title_ja, overall_ja = "", ""
            stories_ja = {a: "" for a in AXES}
            en_title, en_overall, en_stories = title, overall, stories

        # ── Stage 3: per-axis Visual Script prompt ────────────────────────────
        gen_axes = [a for a in AXES if a != body.base_time_axis]
        prompts: dict[str, dict] = {}
        for i, axis in enumerate(gen_axes):
            # Per-axis WD14-style tags inferred from THIS act's own story, so the
            # past and future scenes each get their own rich (~50) tag set.
            _phase("taggingAxis", 0.52 + 0.12 * i, f"Tagging {axis} scene...")
            axis_tags: list[str] = []
            try:
                raw_at = await ollama.generate_text(
                    build_story_tags_prompt(en_stories[axis]),
                    model=vlm_model, options=options, fmt="json",
                )
                axis_tags = parse_tags_json(raw_at)
            except Exception as exc:
                logger.warning("[chronicle] %s tag inference failed: %s", axis, exc)

            # Stage 3a: decide the shot (pose/camera) BEFORE writing the prompt,
            # so the pose expresses the story instead of a default upright stance.
            # For the base_axis the pose is LOCKED to the base image's wd14 tags
            # so the rendered base image matches the thumbnail the user picked.
            _phase("examining", 0.53 + 0.12 * i, f"Framing the {axis} shot...")
            visual_plan: dict = {}
            axis_base_pose = (
                base_pose_tags(wd14_tags) if axis == body.base_time_axis else []
            )
            try:
                raw_vp = await ollama.generate_text(
                    build_visual_examination_prompt(
                        story_text=en_stories[axis],
                        axis=axis,
                        base_axis=body.base_time_axis,
                        time_scale=body.time_scale,
                        character_desc=character_desc,
                        emotion=body.emotion,
                        locale="en",
                        base_pose_tags=axis_base_pose,
                        user_topic=body.user_topic,
                    ),
                    model=vlm_model, options=options, fmt="json",
                )
                visual_plan = parse_visual_plan_json(raw_vp)
            except Exception as exc:
                logger.warning("[chronicle] %s visual examination failed: %s", axis, exc)
            cancel.raise_if_set()

            _phase("refiningPrompt", 0.55 + 0.12 * i, f"Refining {axis} prompt...")
            _put({"type": "token", "text": f"\n\n— {axis} prompt —\n"})
            axis_prompt = build_axis_prompt(
                story_text=en_stories[axis],
                character_tags=character_tags,
                character_desc=character_desc,
                prompt_style=body.prompt_style,
                wd14_context=wd14_context,
                time_scale=body.time_scale,
                axis=axis,
                base_axis=body.base_time_axis,
                title=en_title,
                overall=en_overall,
                all_stories=en_stories,
                axis_tags=axis_tags,
                visual_plan=visual_plan,
                emotion=body.emotion,
                user_topic=body.user_topic,
            )
            axis_tokens: list[str] = []
            async for event in ollama.generate_text_stream(
                axis_prompt, model=vlm_model, options=options
            ):
                if _abort.is_set():
                    raise JobCancelled()
                _put(event)
                if event["type"] == "token":
                    axis_tokens.append(event["text"])
                    if len(axis_tokens) % 8 == 0:
                        reporter.update(
                            0.55 + 0.12 * (i + min(len(axis_tokens) / 700, 0.97)),
                            f"Refining {axis} prompt...",
                        )
            cancel.raise_if_set()
            positive, negative = _parse_positive_negative("".join(axis_tokens))
            positive = _clean_markdown(positive)
            negative = _clean_markdown(negative)
            if not positive:
                _put({"type": "error", "message": f"Prompt refinement failed for {axis}"})
                return

            if body.prompt_style == "danbooru":
                tag_line, prose = positive, ""
            elif body.prompt_style == "natural":
                tag_line, prose = "", positive
            else:  # danbooru+natural: first block = tags, rest = prose
                head, _, tail = positive.partition("\n\n")
                tag_line, prose = head.strip(), tail.strip()

            if tag_line:
                tag_line = _strip_quality_metatags(tag_line)
                tag_line = _ensure_subject_anchor(tag_line, [(doc, 0)])

            # This act's story wins over the base image: one conflict pass per
            # axis over the identity candidates + everything the LLM generated.
            # Higher stakes than Refine — every axis is a different time/scene.
            cand_tags = collect_prompt_tags(f"{tag_line}\n\n{prose}".strip())
            # _find_conflict_tags only reads the first 80, so cap here to match.
            sources = list(dict.fromkeys(identity_tags + axis_tags + cand_tags))[:80]
            conflicts: set[str] = set()
            if sources:
                conflicts = await _find_conflict_tags(
                    en_stories[axis][:400], sources, db, ollama, vlm_model
                )
                if conflicts:
                    logger.info("[chronicle] %s: removed %d story-conflict tags: %s",
                                axis, len(conflicts), ", ".join(sorted(conflicts)))

            inject = [t for t in identity_tags if t not in conflicts]
            if tag_line and inject:
                tag_line = inject_identity_tags(tag_line, inject)
                parts = [t.strip() for t in tag_line.split(",") if t.strip()]
                tag_line = ", ".join(_apply_must_replacements(parts, inject))
            if prose and inject:
                prose = _correct_prose_wd14_conflicts(prose, inject)
            if body.prompt_style != "danbooru" and not _check_natural_prose(prose):
                _put({"type": "warning",
                      "message": f"{axis}: prose paragraphs look thin — consider re-generating."})
            positive = f"{tag_line}\n\n{prose}".strip() if tag_line and prose else (tag_line or prose)
            positive = remove_conflict_tags(positive, conflicts, include_prose_groups=True)
            prompts[axis] = {"positive": positive, "negative": negative}
            _put({"type": "axis_prompt", "axis": axis,
                  "positive": positive, "negative": negative})

        # ── Finalise the record (patch draft → final) ─────────────────────────
        _phase("savingStory", 0.82, "Saving story...")
        prev_axes = draft.get("axes") or {}
        axes_payload: dict = {}
        for axis in AXES:
            axes_payload[axis] = {
                "story": en_stories[axis],
                "story_ja": stories_ja[axis],
                "prompt_positive": prompts.get(axis, {}).get("positive"),
                "prompt_negative": prompts.get(axis, {}).get("negative"),
                "image_id": body.base_sha256 if axis == body.base_time_axis
                else (prev_axes.get(axis) or {}).get("image_id"),
            }

        embedding = None
        try:
            embedding = await ollama.embed(
                " ".join([en_title, en_overall, *(en_stories[a] for a in AXES)])[:4000]
            )
        except Exception as exc:
            logger.warning("[chronicle] story embed failed: %s", exc)

        await story_db.set_story_payload(db, story_id, {
            "status": "final",
            "selected_candidate": candidate_id,
            "time_scale": body.time_scale,
            "workflow_name": body.workflow_name,
            "title": en_title,
            "title_ja": title_ja,
            "overall_story": en_overall,
            "overall_story_ja": overall_ja,
            "axes": axes_payload,
            "divergence": divergence,
            "mutation_tags": mutation_tags,
        })
        if embedding:
            try:
                await story_db.set_story_embedding(db, story_id, embedding)
            except Exception as exc:
                logger.warning("[chronicle] set embedding failed: %s", exc)
        _put({"type": "story_saved", "story_id": story_id})
        if locale == "ja":
            _put({"type": "translation", "title_ja": title_ja, "overall_ja": overall_ja,
                  **{f"{a}_ja": stories_ja[a] for a in AXES}})

        # ── Seed resolution ───────────────────────────────────────────────────
        import random as _random

        seed: int | None = None
        seed_warning = ""
        if body.use_ref_seed:
            seed = (doc.get("model_info") or {}).get("seed") \
                or (doc.get("creation_record") or {}).get("seed")
            if seed is None:
                seed_warning = "Base image has no seed info — using a new random seed."
        if seed is None:
            seed = _random.randint(0, (1 << 64) - 1)
        if seed_warning:
            _put({"type": "warning", "message": seed_warning})

        # ── Submit image jobs (auto mode) ─────────────────────────────────────
        image_jobs: list[dict] = []
        if not body.manual_mode and body.workflow_name:
            for axis in gen_axes:
                cancel.raise_if_set()
                gen_job_id = spooler.submit(
                    JobLane.GENERATION,
                    "chronicle_image",
                    run_chronicle_image_generate,
                    meta={"group_id": body.group_id, "story_id": story_id, "axis": axis},
                    db=db,
                    comfy=comfy,
                    story_id=story_id,
                    axis=axis,
                    workflow_name=body.workflow_name,
                    positive=prompts[axis]["positive"],
                    negative=prompts[axis]["negative"],
                    seed=seed,
                )
                image_jobs.append({"axis": axis, "job_id": gen_job_id})
            _put({"type": "image_jobs", "jobs": image_jobs})
        elif not body.workflow_name:
            _put({"type": "warning",
                  "message": "No workflow selected — image generation skipped."})

        _put({
            "type": "done",
            "story_id": story_id,
            "group_id": body.group_id,
            "seed": seed,
            "manual_mode": body.manual_mode,
            "title": en_title,
            "title_ja": title_ja,
            "overall": en_overall,
            "overall_ja": overall_ja,
            "axes": axes_payload,
        })
    except JobCancelled:
        raise
    except Exception as exc:
        logger.exception("[chronicle] expand pipeline failed")
        _put({"type": "error", "message": str(exc)})
    finally:
        _put(None)


async def run_chronicle_image_generate(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    comfy,
    story_id: str,
    axis: str,
    workflow_name: str,
    positive: str,
    negative: str,
    seed: int | None,
) -> dict:
    """GEN lane. Generate one chronicle axis image, save to Chronicles/, link it."""
    import random as _random

    from ..creation.schema import CreationRecord
    from ..story import db as story_db

    reporter.indeterminate()

    if seed is None:
        seed = _random.randint(0, (1 << 64) - 1)

    wf = comfy.load_workflow(workflow_name)
    patched = comfy.patch_workflow(wf, positive, negative, "", "", 1, seed=seed)
    prompt_id = await comfy.queue_prompt(patched)
    reporter.update(0.0, "Waiting in ComfyUI queue...")

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

    cancel.on_cancel(lambda: asyncio.create_task(_cancel_comfy()))

    async def _finalize(sha256: str) -> None:
        try:
            await story_db.update_story_axis(db, story_id, axis, {"image_id": sha256})
        except Exception as exc:
            logger.error("[chronicle] story link failed for %s/%s: %s", story_id, axis, exc)
        record = CreationRecord(
            method="chronicle",
            prompt_style="",
            workflow_name=workflow_name,
            positive_prompt_generated=positive,
            negative_prompt_generated=negative,
            seed=seed,
        )
        await db.set_payload(sha256, {
            "creation_record": record.model_dump(),
            "chronicle_story_id": story_id,
            "chronicle_axis": axis,
        })

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
                    sha256 = await _save_and_register_chronicle_image(
                        img_bytes, img_ref["filename"], db
                    )
                    if sha256:
                        saved_sha256s.append(sha256)
                        saved_filenames.add(img_ref["filename"])
                        await _finalize(sha256)
                except Exception as exc:
                    logger.error("[chronicle] image save error: %s", exc)

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
            sha256 = await _save_and_register_chronicle_image(
                img_bytes, img_ref["filename"], db
            )
            if sha256:
                saved_sha256s.append(sha256)
                await _finalize(sha256)
        except Exception as exc:
            logger.error("[chronicle] history image save error: %s", exc)

    reporter.update(1.0, f"{len(saved_sha256s)} images generated")
    return {"sha256s": saved_sha256s, "story_id": story_id, "axis": axis, "seed": seed}
