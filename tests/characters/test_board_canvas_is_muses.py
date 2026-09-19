"""**The pictures in the roster keep Muse's own size.** (2026-09-20)

The Showrunner: "apply the resolution only to the images shown in Muse's list;
for an ordinary session leave it to the workflow, the same as krea2."

Two different jobs, and this is the line between them. A shoot follows the
workflow — that is the family switch (`muse/family.py`). The reference board is a
set of pictures that have to **match each other** in the picker, so the slot names
its own canvas and always has (`characters/board.SLOT_SIZE`). What it does not
name is steps and cfg: those stay the workflow's.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.characters.board import SLOT_SIZE  # noqa: E402


def test_each_slot_names_its_own_canvas():
    """A full-body sheet and a bust shot want different aspect ratios — one size
    for both is why the portrait slot used to render a second full-body image."""
    assert SLOT_SIZE, "the roster's sizes live here"
    for slot, (width, height) in SLOT_SIZE.items():
        assert width >= 256 and height >= 256, slot


def test_the_board_sends_a_size_and_leaves_the_sampler_alone():
    """The request's defaults are the contract: a canvas, and `None` for the rest."""
    import inspect

    from app.characters import api as characters_api

    src = inspect.getsource(characters_api)
    # The slot's canvas reaches the render...
    assert "width=slot_w" in src and "height=slot_h" in src
    # ...while steps and cfg are whatever the body carried, defaulting to None.
    body = inspect.getsource(characters_api.BoardRequest)
    assert "steps: int | None = None" in body
    assert "cfg: float | None = None" in body
