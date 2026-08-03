"""Small shared readers for session inputs.

Separate from `service` so the GEN-lane runner can use them without importing
the orchestration it is launched by.
"""
from __future__ import annotations

from typing import Any


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
