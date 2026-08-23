"""GEN-lane runner for a single parameterised render.

``run_generation`` in ``runners.py`` is the Refine/direct path: it takes a batch
count and writes a Refine-shaped CreationRecord. Muse and the character board
need something different — one image, explicit width/height/steps/cfg, a chosen
subfolder, and a caller-supplied hook to attach the result to whatever asked for
it. Rather than grow a fifth set of near-duplicate flags on ``run_generation``,
this is that job.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Awaitable, Callable

from ..creation.schema import CreationRecord
from ..scanner.save import save_generated_image
from ..spooler.models import CancelToken, ProgressReporter

logger = logging.getLogger(__name__)

AttachFn = Callable[[str, dict], Awaitable[None]]
PreviewFn = Callable[[bytes], Awaitable[None]]

# One preview frame per sampler step is more than any viewer needs and more than
# an SSE stream should carry, so frames inside this window are dropped. Nothing
# is lost at the end: the finished image lands through comfy_output, which is the
# real picture rather than a decoded latent.
PREVIEW_MIN_INTERVAL = 0.4


async def run_render(
    reporter: ProgressReporter,
    cancel: CancelToken,
    *,
    db,
    comfy,
    workflow_name: str,
    positive: str,
    negative: str = "",
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    cfg: float | None = None,
    seed: int | None = None,
    batch_count: int = 1,
    subdir: str = "",
    prefix: str = "gen",
    method: str = "muse",
    payload_extra: dict[str, Any] | None = None,
    attach: AttachFn | None = None,
    preview: PreviewFn | None = None,
    reference_image: bytes | None = None,
) -> dict:
    """Render one image and hand the sha256 back to ``attach``.

    Returns ``{"sha256s", "seed", "prompt_id", "unpatched"}``. ``unpatched``
    lists the knobs this workflow had nowhere to put — a draft asked for at
    2 steps that silently rendered at 30 is the single most confusing failure
    in this pipeline, so it is reported rather than swallowed.

    Optional ``reference_image``: when the workflow has an OpenPose / DWPose
    preprocessor fed by LoadImage, the still is uploaded and wired in.
    """
    reporter.indeterminate()
    if seed is None:
        seed = random.randint(0, (1 << 64) - 1)

    wf = comfy.load_workflow(workflow_name)

    requested = {"steps": steps, "cfg": cfg, "width": width, "height": height}
    patchable = comfy.patchable_fields(wf)
    unpatched = [k for k, v in requested.items() if v is not None and not patchable.get(k)]
    if unpatched:
        logger.warning(
            "[render] %s cannot take %s — those values are ignored",
            workflow_name, ", ".join(unpatched),
        )

    openpose_info: dict[str, Any] = {}
    if reference_image:
        try:
            wf, openpose_info = await comfy.apply_openpose_reference(
                wf, reference_image,
                filename=f"muse_direction_{prefix}.jpg",
            )
            if openpose_info.get("injected"):
                logger.info(
                    "[render] openpose reference injected into node %s (%s)",
                    openpose_info.get("image_node_id"), workflow_name,
                )
            elif openpose_info.get("has_openpose"):
                logger.info(
                    "[render] workflow has openpose lineage but cannot auto-inject (%s)",
                    workflow_name,
                )
        except Exception:
            logger.warning("[render] openpose reference skipped", exc_info=True)

    patched = comfy.patch_workflow(
        wf, positive, negative, "", "", max(1, int(batch_count)),
        seed=seed, width=width, height=height, steps=steps, cfg=cfg,
        append_negative=True,
    )

    # **この描画だけの clientId。** 一つを共有していたので、やり直しで
    # 二本目が同じ id で繋がり、ComfyUI が古いほうを落としていた。
    client_id = comfy.new_client_id()
    prompt_id = await comfy.queue_prompt(
        patched, preview=preview is not None, client_id=client_id,
    )
    reporter.update(0.0, "Waiting in ComfyUI queue...")

    queued = True

    async def _cancel_comfy() -> None:
        if queued:
            try:
                await comfy.delete_from_queue(prompt_id)
            except Exception as exc:
                logger.warning("[render] queue delete failed: %s", exc)
        try:
            await comfy.interrupt()
        except Exception as exc:
            logger.warning("[render] interrupt failed: %s", exc)

    cancel.on_cancel(lambda: asyncio.create_task(_cancel_comfy()))

    saved: list[str] = []
    seen_filenames: set[str] = set()

    async def _keep(img_ref: dict) -> None:
        img_bytes = await comfy.fetch_image(
            img_ref["filename"],
            img_ref.get("subfolder", ""),
            img_ref.get("type", "output"),
        )
        sha256 = await save_generated_image(
            img_bytes, img_ref["filename"], db, subdir=subdir, prefix=prefix,
        )
        if not sha256:
            return
        saved.append(sha256)
        seen_filenames.add(img_ref["filename"])
        record = CreationRecord(
            method=method,
            workflow_name=workflow_name,
            positive_prompt_generated=positive,
            negative_prompt_generated=negative,
            seed=seed,
            steps=steps, cfg=cfg, width=width, height=height,
        )
        try:
            await db.set_payload(sha256, {
                "creation_record": record.model_dump(),
                **(payload_extra or {}),
            })
        except Exception as exc:
            logger.warning("[render] payload write failed for %s: %s", sha256, exc)
        if attach is not None:
            try:
                await attach(sha256, {"seed": seed, "job": prompt_id})
            except Exception as exc:
                logger.error("[render] attach failed for %s: %s", sha256, exc)

    last_preview = 0.0

    async for event in comfy.stream_progress(prompt_id, client_id=client_id):
        cancel.raise_if_set()
        queued = False
        if event["type"] == "comfy_progress":
            v = event.get("value", 0)
            m = max(event.get("max", 1), 1)
            reporter.update(v / m, f"Step {v}/{m}")
        elif event["type"] == "comfy_preview" and preview is not None:
            now = time.monotonic()
            if now - last_preview >= PREVIEW_MIN_INTERVAL:
                last_preview = now
                try:
                    await preview(event["image"])
                except Exception as exc:
                    logger.warning("[render] preview publish failed: %s", exc)
        elif event["type"] == "comfy_output":
            for img_ref in event.get("images", []):
                cancel.raise_if_set()
                try:
                    await _keep(img_ref)
                except Exception as exc:
                    logger.error("[render] image save error: %s", exc)
        elif event["type"] in ("comfy_failed", "error"):
            # Let it out. Swallowing this is what left a dead render looking
            # like a live one: the job stayed `running`, kept the GPU resource,
            # and the generation lane queued behind a job nobody could cancel.
            raise RuntimeError(
                f"ComfyUI failed: {event.get('message') or 'unknown error'}"
            )

    # The websocket drops frames under load; /history is the backstop.
    for img_ref in await comfy.fetch_history(prompt_id):
        if img_ref.get("filename") in seen_filenames:
            continue
        try:
            await _keep(img_ref)
        except Exception as exc:
            logger.error("[render] history image save error: %s", exc)

    reporter.update(1.0, f"{len(saved)} image(s)")
    return {
        "sha256s": saved,
        "seed": seed,
        "prompt_id": prompt_id,
        "unpatched": unpatched,
        "openpose": openpose_info,
    }
