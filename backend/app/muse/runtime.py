"""Small shared readers for session inputs.

Separate from `service` so the GEN-lane runner can use them without importing
the orchestration it is launched by.
"""
from __future__ import annotations

import logging
from typing import Any

from . import crew, identity
from . import family as family_mod
from .defaults import ALL_DEFAULTS

logger = logging.getLogger("app.muse.runtime")


def style_for(session: dict[str, Any]) -> str:
    """The look everything downstream obeys. `service._style` delegates here.

    It lives beside `negative_for` because the negative needs it too — a look
    is a choice, and the rendering it rules out belongs on the other side of
    the prompt.
    """
    from . import crew_room

    inputs = session.get("inputs") or {}
    # **The gate opens on the crew's existence (2026-09-13).**
    #
    # This used to split on `mode == "duet"`. **Every Refine session gets
    # `mode: "duet"` from `service.new_session`**, so the branch that averages the
    # crew's taste was never entered — all six presets came out as
    # `anime illustration`, and neither `photoreal` nor `flat` ever reached the
    # picture. The same rut as [[project-refine-as-muse]]'s "do not make `is_duet()`
    # the gate".
    if not crew_room.has_crew(session):
        # No cast to average: the lead shoot (主演撮り) has no room. See
        # `crew.NEUTRAL_LOOK`.
        return (
            crew.look_style(str(inputs.get("look") or ""))
            or str(inputs.get("style") or "").strip()
            or crew.NEUTRAL_LOOK
        )
    return crew.base_style_for(
        crew.resolve_crew(
            preset=str(inputs.get("crew_preset") or crew.DEFAULT_PRESET),
            crew_ids=list(inputs.get("crew_ids") or []) or None,
        ),
        inputs.get("style") or "",
        inputs.get("look") or "",
    )


def negative_for(session: dict[str, Any], *, family: str = "") -> str:
    """The negative prompt for one render.

    **A family that does not take one gets `""` (2026-09-20).** The Showrunner:
    "krea2 needs no negative prompt". `comfy.patch_workflow` leaves the negative
    node alone when the string is empty, so what the workflow itself bakes in
    survives — that text is its author's choice, not ours to overwrite.

    `service` had a copy of this that nothing ever called, while the GEN-lane
    runner kept its own — so anything added to the service version reached no
    render at all. It lives here now because this module is the one both sides
    are allowed to import.

    Two things go in, and nothing else: what the Showrunner wrote in the
    negative box, and what the Showrunner refused in conversation.

    The figure lock used to be pushed from both sides — every body tag that
    contradicts the sheet, plus a fixed age list (`mature_female, old, loli,
    child, petite`), went in on every render. Measured on a live session that
    was 21 of 35 tokens spent restating a lock that is already absolute on the
    other side: `identity.assemble_positive` refuses those same tags entry to
    the POSITIVE prompt, so the sampler is never asked for them in the first
    place. Keeping a word out is the guard; naming it again in the negative
    only crowds out the tags that describe the picture.
    """
    inputs = session.get("inputs") or {}
    banned = [str(t) for t in (session.get("banned") or []) if str(t).strip()]
    built = identity.merge_negative(
        str(inputs.get("negative_prompt") or ""),
        identity.framing_negative(str(inputs.get("framing") or "auto")),
        # The rendering the chosen look rules out. Three flat tags among forty
        # cannot outvote what the checkpoint does by default; naming the
        # opposite is the half of the prompt where "not this" works.
        ", ".join(crew.look_negative(style_for(session))),
        # What the Showrunner refused. This is the only place in the pipeline
        # where "do not draw this" is a mechanism rather than a request — put it
        # in the positive prompt and the sampler makes it more likely, not less.
        ", ".join(banned),
    )
    if family and not family_mod.sends_negative(family):
        # **Never dropped in silence.** `patch_workflow` skips an empty negative
        # without a word, which is how a knob with nowhere to go stays invisible
        # (the diary's kana page was found the same way: by what was not logged).
        if built.strip():
            logger.info("[muse.family] %s sends no negative — %d chars dropped: %r",
                        family, len(built), built[:120])
        return ""
    return built


def render_settings(
    inputs: dict[str, Any], *, draft: bool, family: str = "",
) -> dict[str, Any]:
    """The size and sampler knobs for one render.

    Width and height are shared: the draft and everything downstream are the
    same canvas, so the only thing that changes between stages is the prompt.
    That is what makes the four pictures of a run comparable at all.

    **A family fills in only what the Showrunner has not set (2026-09-20).**
    Every session carries all of `ALL_DEFAULTS`, so "he chose this" cannot be read
    from the key's presence — it is read from the value still being the shipped
    one. Set steps to 6 and switch to a krea2 workflow and it stays 6; leave them
    alone and krea2's 4/8 apply. A family whose cfg is `None` leaves the key out
    entirely, and the workflow's own cfg is what runs.
    """
    prefix = "draft" if draft else "final"
    steps_key, cfg_key = f"{prefix}_steps", f"{prefix}_cfg"
    out: dict[str, Any] = {
        "width": int(inputs.get("width", 896)),
        "height": int(inputs.get("height", 1152)),
        "steps": int(inputs.get(steps_key, 12 if draft else 30)),
        "cfg": float(inputs.get(cfg_key, 4.0 if draft else 4.5)),
    }
    if not family:
        return out
    wanted = family_mod.render_overrides(family, draft=draft)
    if wanted.get("steps") is not None and _untouched(inputs, steps_key):
        out["steps"] = int(wanted["steps"])
    if _untouched(inputs, cfg_key):
        if wanted.get("cfg") is None:
            out.pop("cfg")          # the workflow keeps its own
        else:
            out["cfg"] = float(wanted["cfg"])
    return out


def _untouched(inputs: dict[str, Any], key: str) -> bool:
    """Is this knob still the value every session ships with?

    The one piece of provenance available without new bookkeeping: sessions
    created before families existed have none, and a stored `inputs_touched` list
    would be a second source of truth to keep honest.
    """
    if key not in ALL_DEFAULTS:
        return False
    stored = inputs.get(key, ALL_DEFAULTS[key])
    try:
        return float(stored) == float(ALL_DEFAULTS[key])  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return stored == ALL_DEFAULTS[key]
