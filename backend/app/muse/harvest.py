"""S5: read the board images back as tags.

The library tags at 0.35. This reads at 0.15, which brings in a long tail of
weak, half-wrong tags — and that tail is the point. It is where a board picks up
something nobody asked for and the final image becomes interesting. The optional
re-rank exists to make the result tidier, and it is off by default because tidy
is not what this step is for.
"""
from __future__ import annotations

import logging
from typing import Any

from ..ai.wd14 import CATEGORY_CHARACTER, CATEGORY_RATING, tags_scored_from_bytes
from ..tags.junk import is_junk_tag

logger = logging.getLogger(__name__)


async def harvest_image(
    img_bytes: bytes,
    *,
    threshold: float,
    model_dir: str | None = None,
    drop_rating_tags: bool = False,
    drop_character_tags: bool = True,
) -> list[dict[str, Any]]:
    """One board image → ``[{tag, score, category}]``, best first."""
    scored = await tags_scored_from_bytes(
        img_bytes, threshold=threshold, model_dir=model_dir,
    )
    out: list[dict[str, Any]] = []
    for name, score, category in scored:
        # `no_humans` off a background draft, `black_border` off a framed one —
        # both read correctly off the image and both wreck the final prompt.
        if is_junk_tag(name):
            continue
        # Named characters are copyrighted people the checkpoint happens to
        # recognise in its own draft. Letting one through means the final render
        # is of somebody else's character.
        if drop_character_tags and category == CATEGORY_CHARACTER:
            continue
        # Rating tags say nothing about what is in the picture, so they are
        # noise in a prompt either way — but they are only *dropped* on request.
        if drop_rating_tags and category == CATEGORY_RATING:
            continue
        out.append({"tag": name, "score": round(float(score), 4), "category": int(category)})
    return out


def fold_track(
    per_image: list[list[dict[str, Any]]],
    *,
    seed_tags: list[str] | None = None,
    frequency: dict[str, float] | None = None,
    rerank: bool = False,
) -> list[dict[str, Any]]:
    """Collapse a track's board images into one ranked tag list.

    Ranking, in order of weight:

    1. how many of the track's images agree on the tag — a tag on all three is
       a property of the idea, a tag on one is an accident of one seed;
    2. the best confidence it reached;
    3. whether the theme already asked for it;
    4. how common the tag is on Danbooru, when ``rerank`` is on — very rare tags
       cannot be drawn reliably and very common ones carry no information.

    Agreement leads because it is the only signal here that separates "the
    board really is like this" from "one render wandered off".
    """
    n_images = max(len(per_image), 1)
    seeds = {str(t).lower() for t in (seed_tags or [])}
    merged: dict[str, dict[str, Any]] = {}

    for image_tags in per_image:
        for row in image_tags:
            key = row["tag"].lower()
            entry = merged.setdefault(key, {
                "tag": row["tag"], "score": 0.0, "count": 0,
                "category": row.get("category", 0),
            })
            entry["score"] = max(entry["score"], float(row["score"]))
            entry["count"] += 1

    rows = list(merged.values())
    for row in rows:
        row["agreement"] = row["count"] / n_images
        row["from_theme"] = row["tag"].lower() in seeds
        rank = row["agreement"] * 2.0 + row["score"] + (0.5 if row["from_theme"] else 0.0)
        if rerank and frequency:
            freq = frequency.get(row["tag"].lower())
            if freq is not None:
                # Mid-band tags are the drawable, informative ones. Both ends
                # get pushed down rather than removed.
                rank += 0.4 if 0.01 <= freq <= 0.60 else -0.3
        row["rank"] = round(rank, 4)

    rows.sort(key=lambda r: (-r["rank"], -r["score"], r["tag"]))
    return rows
