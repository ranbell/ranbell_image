"""Small shared readers for session inputs.

Separate from `service` so the GEN-lane runner can use them without importing
the orchestration it is launched by.
"""
from __future__ import annotations

from typing import Any

from . import crew, identity


def style_for(session: dict[str, Any]) -> str:
    """The look everything downstream obeys. `service._style` delegates here.

    It lives beside `negative_for` because the negative needs it too — a look
    is a choice, and the rendering it rules out belongs on the other side of
    the prompt.
    """
    inputs = session.get("inputs") or {}
    if str(session.get("mode") or "") == "duet":
        # No cast to average: 主演撮り has no room. See `crew.NEUTRAL_LOOK`.
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


def negative_for(session: dict[str, Any]) -> str:
    """The negative prompt for one render.

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
    return identity.merge_negative(
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


def render_settings(inputs: dict[str, Any], *, draft: bool) -> dict[str, Any]:
    """The size and sampler knobs for one render.

    Width and height are shared: the draft and everything downstream are the
    same canvas, so the only thing that changes between stages is the prompt.
    That is what makes the four pictures of a run comparable at all.
    """
    prefix = "draft" if draft else "final"
    return {
        "width": int(inputs.get("width", 896)),
        "height": int(inputs.get("height", 1152)),
        "steps": int(inputs.get(f"{prefix}_steps", 12 if draft else 30)),
        "cfg": float(inputs.get(f"{prefix}_cfg", 4.0 if draft else 4.5)),
    }
