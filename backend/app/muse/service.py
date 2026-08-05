"""Muse orchestration.

    theme + character  ->  brief
    brief              ->  stage A prompt      ->  N drafts, one seed, one latent
    a chosen draft     ->  WD14                ->  B -> C (-> D), rendering each

Two steps with a person between them. There is no mode that skips the choice:
the draft decides how good the rest of the run can be, and no arrangement of
instructions judges four pictures as well as looking at them does. The draft
render streams previews for the same reason — the useful moment to abandon a
prompt is five steps in, not after four images have finished.
"""
from __future__ import annotations

import logging
import random
from typing import Any

from ..characters import presets as presets_db
from ..runtime_config import get_runtime_config
from ..spooler.models import JobLane
from . import brief as brief_mod
from . import chain, events, identity, runner, session_db
from .runtime import render_settings
from .schema import missing_inputs, new_session

logger = logging.getLogger(__name__)


class MuseError(Exception):
    """A step could not run. The message goes straight to the user."""


def _inputs(session: dict[str, Any]) -> dict[str, Any]:
    return session.get("inputs") or {}


def _identity_tags(session: dict[str, Any]) -> list[str]:
    character = session.get("character") or {}
    return [str(t) for t in (character.get("identity_tags") or []) if str(t).strip()]


def _framing(inputs: dict[str, Any]) -> str:
    return identity.normalize_framing(str(inputs.get("framing") or "auto"))


def _text_model(inputs: dict[str, Any]) -> str:
    return str(inputs.get("model") or "")


def _vision_model(inputs: dict[str, Any]) -> str:
    return str(inputs.get("vision_model") or inputs.get("model") or "")


def _num_ctx(inputs: dict[str, Any], cfg: dict[str, Any]) -> int | None:
    """Muse asks for a bigger window than the app default when it can.

    Thinking spends thousands of tokens before the answer begins, on top of a
    brief and an image that are already in the window.
    """
    return int(inputs.get("num_ctx") or cfg.get("ollama_num_ctx") or 0) or None


def _prompt_token_publisher(session_id: str, stage: str):
    """Forward answer tokens to the session SSE stream while a stage writes."""
    def _publish(text: str) -> None:
        events.publish(session_id, {
            "type": "prompt_delta",
            "stage": stage,
            "text": text,
        })
    return _publish


def _prompt_done(session_id: str, stage: str, prompt: str) -> None:
    events.publish(session_id, {
        "type": "prompt_done",
        "stage": stage,
        "prompt": prompt,
    })


async def create_session(db, inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    session = new_session(inputs)
    await session_db.save(db, session)
    return session


async def patch_inputs(db, session: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    session["inputs"] = {**_inputs(session),
                         **{k: v for k, v in patch.items() if v is not None}}
    _rebuild_brief(session)
    await session_db.save(db, session)
    return session


async def pick_character(db, session: dict[str, Any], character_id: str) -> dict[str, Any]:
    preset = await presets_db.get_preset(db, character_id)
    if preset is None:
        raise MuseError("character not found")
    # Frozen at pick time: editing the registry later does not change a run that
    # has already started drawing this person.
    session["character"] = {
        **presets_db.preset_to_character(preset),
        "character_id": character_id,
        "board": preset.get("board") or {},
        "name": preset.get("name") or "",
        "name_ja": preset.get("name_ja") or preset.get("name") or "",
    }
    session["inputs"] = {**_inputs(session), "character_id": character_id}
    _rebuild_brief(session)
    session_db.log(session, "character", session["character"].get("name", ""))
    await session_db.save(db, session)
    return session


def _rebuild_brief(session: dict[str, Any]) -> None:
    """Keep the brief in step with the inputs it is built from.

    Stored rather than assembled per call because every stage is handed the same
    text, and a brief that drifted between stage B and stage D would be exactly
    the identity drift this design removed the machinery for.
    """
    inputs = _inputs(session)
    character = session.get("character") or {}
    if not character or not str(inputs.get("theme") or "").strip():
        session["brief"] = ""
        return
    session["brief"] = brief_mod.build(
        character,
        str(inputs.get("theme") or ""),
        str(inputs.get("style") or ""),
        framing=_framing(inputs),
    )


def negative_for(session: dict[str, Any]) -> str:
    """Base negative plus opposing body tags and framing exclusions."""
    inputs = _inputs(session)
    return identity.merge_negative(
        str(inputs.get("negative_prompt") or ""),
        identity.opposing_negative(_identity_tags(session)),
        identity.framing_negative(_framing(inputs)),
    )


# ── draft ───────────────────────────────────────────────────────────────────
async def run_draft(db, ollama, comfy, spooler, session: dict[str, Any]) -> dict[str, Any]:
    """Stage A, then one render job that produces every draft at once."""
    missing = missing_inputs(session)
    if missing:
        raise MuseError(f"missing: {', '.join(missing)}")

    inputs = _inputs(session)
    _rebuild_brief(session)
    cfg = await get_runtime_config(db)
    model = _text_model(inputs)
    session_id = session["session_id"]
    framing = _framing(inputs)
    tags = _identity_tags(session)

    try:
        prompt = await chain.run_pose(
            ollama, brief=session["brief"], model=model,
            num_ctx=_num_ctx(inputs, cfg),
            think=bool(inputs.get("think", False)),
            identity_tags=tags,
            framing=framing,
            on_token=_prompt_token_publisher(session_id, "pose"),
        )
    except chain.ChainError as exc:
        raise MuseError(str(exc)) from exc
    _prompt_done(session_id, "pose", prompt)
    # Hand the card over before asking ComfyUI for a four-image latent.
    await ollama.unload(model)

    seed = random.randint(0, (1 << 64) - 1)
    count = max(1, int(inputs.get("draft_count", 4)))

    unpatched = _unpatchable(comfy, str(inputs.get("workflow") or ""),
                             render_settings(inputs, draft=True))
    if unpatched:
        _warn(session, f"workflow ignores: {', '.join(unpatched)}")

    # Written before the job is queued: the runner reads the prompt and the seed
    # back out of the session rather than being handed them, so there is one
    # copy of what is being drawn.
    session["draft"] = {
        "prompt": prompt,
        "pose_intent": identity.pose_summary(prompt),
        "seed": seed,
        "job_id": "",
        "images": [],
        "pending": True,
    }
    session["selected"] = []
    session["chains"] = []
    session["status"] = "drafting"
    await session_db.save(db, session)

    session["draft"]["job_id"] = spooler.submit(
        JobLane.GENERATION,
        "muse_draft",
        runner.run_draft_job,
        meta={"session_id": session_id, "step": "draft"},
        db=db, comfy=comfy, session_id=session_id,
    )
    session_db.log(session, "draft", f"{count} variations, seed {seed}")
    await session_db.save(db, session)
    return session


async def cancel_draft(db, spooler, session: dict[str, Any]) -> dict[str, Any]:
    """Stop a draft mid-render so stage A can be run again.

    The whole batch goes: the previews show one latent of the four, and a prompt
    judged wrong from that latent is wrong for all of them.
    """
    job_id = str((session.get("draft") or {}).get("job_id") or "")
    if job_id:
        await spooler.cancel(job_id)
    session["draft"] = {}
    session["selected"] = []
    session["chains"] = []
    session["status"] = "draft"
    session_db.log(session, "draft", "cancelled")
    await session_db.save(db, session)
    return session


# ── refine ──────────────────────────────────────────────────────────────────
async def run_refine(
    db, ollama, comfy, spooler, session: dict[str, Any], indices: list[int],
) -> dict[str, Any]:
    """Send one or more drafts down the chain. Each becomes its own job."""
    images = {i["index"]: i for i in ((session.get("draft") or {}).get("images") or [])
              if i.get("image_id")}
    chosen = [i for i in dict.fromkeys(indices) if i in images]
    if not chosen:
        raise MuseError("choose a draft that has finished rendering")

    inputs = _inputs(session)
    stages = chain.stages_for(inputs.get("refine_stages", 2))
    seed = int((session.get("draft") or {}).get("seed") or 0)
    pose_intent = str((session.get("draft") or {}).get("pose_intent") or "")
    if not pose_intent:
        pose_intent = identity.pose_summary(
            str((session.get("draft") or {}).get("prompt") or ""),
        )

    session["selected"] = chosen
    session["chains"] = [
        {
            "draft_index": idx,
            "source_image_id": images[idx]["image_id"],
            "seed": seed,
            "pose_intent": pose_intent,
            "wd14": "",
            "stages": [{"stage": name, "prompt": "", "image_id": "", "pending": True}
                       for name, _ in stages],
        }
        for idx in chosen
    ]
    session["status"] = "refining"
    await session_db.save(db, session)

    session_id = session["session_id"]
    for chain_index in range(len(chosen)):
        spooler.submit(
            JobLane.GENERATION,
            f"muse_refine:{chosen[chain_index]}",
            runner.run_chain_job,
            meta={"session_id": session_id, "step": "refine", "chain": chain_index},
            db=db, comfy=comfy, ollama=ollama,
            session_id=session_id, chain_index=chain_index,
        )

    session_db.log(session, "refine",
                   f"{len(chosen)} draft(s) × {len(stages)} stages")
    await session_db.save(db, session)
    return session


# ── plumbing ────────────────────────────────────────────────────────────────
def _unpatchable(comfy, workflow_name: str, wanted: dict[str, Any]) -> list[str]:
    """Knobs this workflow has nowhere to put.

    Worth saying up front: a draft asked for at 12 steps that quietly renders at
    30 costs full price and looks nothing like a draft.
    """
    try:
        wf = comfy.load_workflow(workflow_name)
        patchable = comfy.patchable_fields(wf)
    except Exception as exc:
        logger.warning("[muse] could not inspect %s: %s", workflow_name, exc)
        return []
    return [k for k, v in wanted.items() if v is not None and not patchable.get(k)]


def _warn(session: dict[str, Any], message: str) -> None:
    warnings = session.setdefault("warnings", [])
    if message not in warnings:
        warnings.append(message)
