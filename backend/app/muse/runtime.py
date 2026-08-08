"""Small shared readers for session inputs.

Separate from `service` so the GEN-lane runner can use them without importing
the orchestration it is launched by.
"""
from __future__ import annotations

from typing import Any

from . import identity


def negative_for(session: dict[str, Any]) -> str:
    """The negative prompt for one render.

    `service` had a copy of this that nothing ever called, while the GEN-lane
    runner kept its own — so anything added to the service version reached no
    render at all. It lives here now because this module is the one both sides
    are allowed to import.
    """
    inputs = session.get("inputs") or {}
    tags = [
        str(t) for t in ((session.get("character") or {}).get("identity_tags") or [])
        if str(t).strip()
    ]
    banned = [str(t) for t in (session.get("banned") or []) if str(t).strip()]
    return identity.merge_negative(
        str(inputs.get("negative_prompt") or ""),
        identity.opposing_negative(tags),
        identity.framing_negative(str(inputs.get("framing") or "auto")),
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
