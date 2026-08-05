"""Read the chosen draft back as tags — the one place WD14 is still used.

This runs once, on the draft, and its output feeds stage B together with a short
pose intent from A. That substitution is the point of the design: A describes
the picture it wants, WD14 describes the picture that exists, and B repairs
toward the theme without inventing a new body.

The threshold is well under the library default so the weak tail comes through.
Body tags that fight the character's locked identity are dropped here — a draft
that drew a larger chest must not become the refine chain's "fact".
"""
from __future__ import annotations

import logging
from typing import Iterable

from ..ai.wd14 import CATEGORY_CHARACTER, CATEGORY_RATING, tags_scored_from_bytes
from .identity import drop_conflicting_tags

logger = logging.getLogger(__name__)


async def read_tags(
    img_bytes: bytes,
    *,
    threshold: float,
    model_dir: str | None = None,
    drop_rating_tags: bool = False,
    drop_character_tags: bool = True,
    identity_tags: Iterable[str] | None = None,
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
    tags = ", ".join(names)
    return drop_conflicting_tags(tags, identity_tags)
