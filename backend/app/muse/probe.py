"""Cheap probe renders — the crew's only honest look at what it is writing.

Two things are going on here.

**Small and fast.** A 512px twelve-step render takes seconds, so the crew can
look at a real frame between passes instead of arguing about one they have never
seen. `jobs.render.run_render` cannot be reused: it always writes through
`save_generated_image`, and a session that leaves forty throwaway 512s in the
library is worse than no probe at all. So this walks the same ComfyUI client and
hands the bytes back without touching Qdrant.

**Split.** The pose and the setting are rendered separately. That is partly to
show the Showrunner two clear things early, and partly because it is the only
way the measurements mean anything: a board that came back at 66% pure black
does not say whether the light or the character was at fault, and the object
ledger cannot be checked by WD14 while a character dominates the frame.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable

from . import identity

logger = logging.getLogger(__name__)

POSE = "pose"
SETTING = "setting"
MERGED = "merged"

# Slots each probe is built from. Anything not listed is left out entirely —
# a pose probe with the room in it measures the room.
_SLOTS: dict[str, tuple[str, ...]] = {
    POSE: ("subject", "pose", "wardrobe", "mood"),
    SETTING: ("place", "objects", "light", "camera"),
}
# Said out loud so the checkpoint does not fill the gap back in by itself.
_LEAD: dict[str, str] = {
    POSE: "simple background, white background",
    SETTING: "no humans, scenery",
}
_NEGATIVE: dict[str, str] = {
    POSE: "scenery, detailed background, cluttered background",
    SETTING: "1girl, 1boy, solo, person, face, portrait",
}


@dataclass(frozen=True)
class ProbeShot:
    kind: str
    positive: str
    negative: str


# ComfyUI is driven one graph at a time: `stream_progress` opens a websocket
# keyed on a single client id, so two overlapping renders fight over the same
# connection and one of them waits forever for a completion message delivered to
# the other. Rendering the pair concurrently looked like an easy win and hung
# the whole session. They are seconds apart at this size; keep them in order.
SEQUENTIAL = True


def split_prompts(
    shot: dict[str, Any],
    *,
    identity_tags: Iterable[str] | None = None,
    subject: Iterable[str] | None = None,
    style: str = "",
    framing: str | None = "auto",
    negative: str = "",
    slot_order: Iterable[str] = (),
) -> list[ProbeShot]:
    """One prompt for the person, one for the place. No LLM call needed.

    The setting probe drops identity and the subject count as well as the slots
    — a probe meant to answer "is this room lit and furnished" must not contain
    a girl, or WD14 spends its confidence on her hair.
    """
    order = list(slot_order) or list(shot.keys())
    out: list[ProbeShot] = []
    for kind in (POSE, SETTING):
        keep = _SLOTS[kind]
        trimmed = {k: v for k, v in (shot or {}).items() if k in keep}
        positive = identity.render_shot(
            trimmed,
            identity_tags=identity_tags if kind == POSE else [],
            subject=subject if kind == POSE else [],
            style=style,
            framing=framing if kind == POSE else "auto",
            slot_order=[s for s in order if s in keep],
            quality=(),
        )
        lead = _LEAD[kind]
        out.append(ProbeShot(
            kind=kind,
            positive=f"{lead}, {positive}" if positive else lead,
            negative=identity.merge_negative(negative, _NEGATIVE[kind]),
        ))
    return out


async def render(
    comfy,
    *,
    workflow_name: str,
    positive: str,
    negative: str = "",
    seed: int,
    size: int = 512,
    steps: int = 12,
    cfg: float = 4.0,
) -> bytes | None:
    """Render one small frame and return its bytes. Never writes to the library.

    Returns None rather than raising: a probe that cannot be taken is a reason
    to keep working from the last measurement, not to stop the session.
    """
    try:
        wf = comfy.load_workflow(workflow_name)
        patched = comfy.patch_workflow(
            wf, positive, negative, "", "", 1,
            seed=seed, width=size, height=size, steps=steps, cfg=cfg,
            append_negative=True,
        )
        prompt_id = await comfy.queue_prompt(patched, preview=False)

        refs: list[dict] = []
        async for event in comfy.stream_progress(prompt_id):
            if event.get("type") == "comfy_output":
                refs.extend(event.get("images") or [])
        if not refs:
            refs = await comfy.fetch_history(prompt_id)
        if not refs:
            logger.warning("[muse.probe] no image came back for %s", prompt_id)
            return None

        ref = refs[-1]
        return await comfy.fetch_image(
            ref["filename"], ref.get("subfolder", ""), ref.get("type", "output"),
        )
    except Exception:
        logger.warning("[muse.probe] probe render failed", exc_info=True)
        return None
