"""Read the chosen draft back as tags — the one place WD14 is still used.

This runs once, on the draft, and its output replaces stage A's prose as the
text stage B works from. That substitution is the point of the whole design: A
describes the picture it wants, WD14 describes the picture that exists, and only
one of those is a fact.

The threshold is well under the library default so the weak tail comes through.
The tail is where the checkpoint's own ideas are — things nobody asked for that
the draft drew anyway — and stage B is being asked to build on the drawing, not
on the request.

Nothing is cleaned up here. Muse used to run an LLM pass to prune this list and
a rules pass before that; both existed to protect a downstream that turned tags
into prompt lines with per-aspect budgets. Stage B is a vision model looking at
the same image, so a wrong tag is contradicted by what it can see.
"""
from __future__ import annotations

import logging

from ..ai.wd14 import CATEGORY_CHARACTER, CATEGORY_RATING, tags_scored_from_bytes

logger = logging.getLogger(__name__)


async def read_tags(
    img_bytes: bytes,
    *,
    threshold: float,
    model_dir: str | None = None,
    drop_rating_tags: bool = False,
    drop_character_tags: bool = True,
) -> str:
    """One image → a comma-separated tag string, strongest first."""
    scored = await tags_scored_from_bytes(
        img_bytes, threshold=threshold, model_dir=model_dir,
    )
    names: list[str] = []
    for name, _score, category in scored:
        # A named character is somebody else's character that the checkpoint
        # recognised inside its own draft. Left in, the rest of the chain
        # faithfully redraws that person instead of ours.
        if drop_character_tags and category == CATEGORY_CHARACTER:
            continue
        if drop_rating_tags and category == CATEGORY_RATING:
            continue
        names.append(name)
    return ", ".join(names)
