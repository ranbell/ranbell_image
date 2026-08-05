"""GEN-lane job that walks one chosen draft through the refine stages.

The chain cannot be queued as independent jobs: every stage needs the picture
the stage before it produced. So it is one job that alternates LLM and renderer,
and it calls :func:`run_render` directly rather than reimplementing it — the
workflow patching, the unpatchable-knob warning, the image saving and the
CreationRecord are all wanted here unchanged.

Unload before each render is opt-in (``inputs.unload_vlm``). Left resident with
think off, the VLM answers B/C/D in seconds; that is the point of the multi-stage
prompt chain. Turn unload on only when Comfy and the VLM fight for the card.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from . import chain, events, harvest, identity, session_db
from .runtime import render_settings


def _negative_for(session: dict[str, Any]) -> str:
    inputs = session.get("inputs") or {}
    tags = [
        str(t) for t in ((session.get("character") or {}).get("identity_tags") or [])
        if str(t).strip()
    ]
    return identity.merge_negative(
        str(inputs.get("negative_prompt") or ""),
        identity.opposing_negative(tags),
        identity.framing_negative(str(inputs.get("framing") or "auto")),
    )


def _vision_model(inputs: dict[str, Any]) -> str:
    return str(inputs.get("vision_model") or inputs.get("model") or "")

logger = logging.getLogger(__name__)


async def _image_bytes(db, sha256: str) -> bytes:
    doc = await db.get(sha256)
    path = Path(str((doc or {}).get("path") or ""))
    if not path.exists():
        raise RuntimeError(f"image missing on disk: {sha256}")
    return path.read_bytes()


def preview_publisher(session_id: str, label: str):
    """Forward latent frames to the session's SSE stream.

    The frame is the payload rather than a nudge to refetch: there is nowhere on
    the server it is kept, and by the time a client came back for it the render
    would have moved on.
    """
    async def _publish(jpeg: bytes) -> None:
        events.publish(session_id, {
            "type": "preview", "label": label,
            "image": base64.b64encode(jpeg).decode(),
        })
    return _publish


def finished_image(shas: list[str]) -> str:
    """The picture a workflow meant to end on.

    A render returns every image it saved, in the order the graph produced them.
    Workflows here often end in an upscale or a detailer, which is a second
    output node: taking the first sha kept the raw sampler output and threw the
    finished one away — every refine stage came back at 896×1152 while the
    workflow had also written a larger, cleaner version.
    """
    return shas[-1]


async def run_draft_job(
    reporter, cancel, *, db, comfy, session_id: str,
) -> dict[str, Any]:
    """Render the draft batch and mark the step finished when it stops.

    Completion cannot be inferred from how many images arrive: a workflow with
    an upscale tail emits one per batch item *per output node*, so a batch of
    four lands as eight. The job knowing it is over is the only reliable signal.
    """
    from ..jobs.render import run_render
    from ..scanner.drafts import PLAYGROUND_SUBDIR

    session = await session_db.load(db, session_id)
    if session is None:
        raise RuntimeError("session is gone")
    inputs = session.get("inputs") or {}
    draft = session.get("draft") or {}

    async def _attach(sha256: str, meta: dict) -> None:
        await session_db.attach_draft_image(db, session_id, sha256, meta)

    error = ""
    try:
        return await run_render(
            reporter, cancel,
            db=db, comfy=comfy,
            workflow_name=str(inputs.get("workflow") or ""),
            positive=str(draft.get("prompt") or ""),
            negative=_negative_for(session),
            seed=int(draft.get("seed") or 0) or None,
            batch_count=max(1, int(inputs.get("draft_count", 4))),
            subdir=PLAYGROUND_SUBDIR,
            prefix="muse_draft",
            method="muse_draft",
            payload_extra={"muse_session_id": session_id, "muse_stage": "draft"},
            attach=_attach,
            preview=preview_publisher(session_id, "draft"),
            **render_settings(inputs, draft=True),
        )
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        await session_db.finish_draft(db, session_id, error=error)


async def run_chain_job(
    reporter, cancel, *, db, comfy, ollama, session_id: str, chain_index: int,
) -> dict[str, Any]:
    """Run every refine stage of one chain, rendering after each."""
    # Deferred: importing the render job at module scope drags in the scanner and
    # the image API, which is a heavy tail for a module the tests import directly.
    from ..jobs.render import run_render
    from ..scanner.drafts import PLAYGROUND_SUBDIR

    session = await session_db.load(db, session_id)
    if session is None:
        raise RuntimeError("session is gone")

    inputs = session.get("inputs") or {}
    chains = session.get("chains") or []
    if not 0 <= chain_index < len(chains):
        raise RuntimeError("no such chain")
    link = chains[chain_index]

    model = _vision_model(inputs)
    brief = str(session.get("brief") or "")
    render = render_settings(inputs, draft=False)
    num_ctx = int(inputs.get("num_ctx") or 0) or None
    framing = identity.normalize_framing(str(inputs.get("framing") or "auto"))
    identity_tags = [
        str(t) for t in ((session.get("character") or {}).get("identity_tags") or [])
        if str(t).strip()
    ]
    pose_intent = str(link.get("pose_intent") or "")
    negative = _negative_for(session)

    image = await _image_bytes(db, str(link.get("source_image_id") or ""))

    # Read the draft back once. Body tags that fight the locked identity are
    # dropped so a draft guess cannot become the chain's figure.
    tags = await harvest.read_tags(
        image,
        threshold=float(inputs.get("wd14_threshold", 0.2)),
        model_dir=(await _wd14_dir(db)),
        drop_rating_tags=bool(inputs.get("drop_rating_tags", False)),
        drop_character_tags=bool(inputs.get("drop_character_tags", True)),
        identity_tags=identity_tags,
    )
    await session_db.record_wd14(db, session_id, chain_index, tags)

    unload_vlm = bool(inputs.get("unload_vlm", False))
    previous = ""
    for stage_index, (stage, prompt_file) in enumerate(
        chain.stages_for(inputs.get("refine_stages", 3))
    ):
        cancel.raise_if_set()
        reporter.indeterminate()
        reporter.update(0.0, f"{chain.STAGE_LABELS.get(stage, stage)} — writing")

        def _on_token(text: str, _stage=stage) -> None:
            events.publish(session_id, {
                "type": "prompt_delta",
                "stage": _stage,
                "chain": chain_index,
                "text": text,
            })

        stage_result = await chain.run_refine(
            ollama, stage_file=prompt_file, brief=brief, previous=previous,
            image=image, model=model, num_ctx=num_ctx, think=False,
            tags=tags if stage_index == 0 else "",
            pose=pose_intent if stage_index == 0 else "",
            identity_tags=identity_tags,
            framing=framing,
            on_token=_on_token,
        )
        prompt = stage_result.prompt
        await session_db.record_stage_prompt(db, session_id, chain_index,
                                             stage_index, prompt)
        events.publish(session_id, {
            "type": "prompt_done",
            "stage": stage,
            "chain": chain_index,
            "prompt": prompt,
        })
        if unload_vlm:
            await ollama.unload(model)

        render_result = await run_render(
            reporter, cancel,
            db=db, comfy=comfy,
            workflow_name=str(inputs.get("workflow") or ""),
            positive=prompt,
            negative=negative,
            seed=int(link.get("seed") or 0) or None,
            subdir=PLAYGROUND_SUBDIR,
            prefix=f"muse_{stage}",
            method="muse_refine",
            payload_extra={"muse_session_id": session_id, "muse_stage": stage},
            preview=preview_publisher(session_id, f"{chain_index}:{stage}"),
            **render,
        )
        shas = render_result.get("sha256s") or []
        if not shas:
            raise RuntimeError(f"stage {stage} rendered nothing")

        kept = finished_image(shas)
        await session_db.attach_stage_image(
            db, session_id, chain_index, stage_index, kept, render_result,
        )
        image = await _image_bytes(db, kept)
        previous = prompt

    return {"chain": chain_index}


async def _wd14_dir(db) -> str | None:
    from ..runtime_config import get_runtime_config
    cfg = await get_runtime_config(db)
    return cfg.get("wd14_model_dir")
