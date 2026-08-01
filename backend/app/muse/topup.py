"""S4: name a few things the picture is missing.

This is what became of the vocabulary search. As the *first* step it had to
invent a picture out of a phrase, and it was bad at that — vector neighbours of
"library, rain" include ``closed_eyes`` because people get photographed in
libraries. Here the picture already exists and has been read back off the
canvas, so the question is much smaller and answerable: of the tags the theme
suggests, which ones are *not* in the image, and would any of them help?

Retrieval proposes, a model disposes. The search returns everything near the
theme that the drafts did not produce; the model picks the handful that would
actually reinforce what is already there. Five, because this is seasoning — the
image is the dish.
"""
from __future__ import annotations

import logging
from typing import Any

from ..ai.json_util import parse_json_object
from ..ai.llm_options import llm_options
from ..tags.junk import is_junk_tag
from .slots import restates
from .tracks import belongs_to_track

logger = logging.getLogger(__name__)

DEFAULT_PICKS = 5
# Cosine cutoff on the vocabulary search. Below this the "neighbours" of a theme
# are only loosely related and the candidate list turns into noise the model has
# to wade through.
DEFAULT_MIN_SCORE = 0.3

_PROMPT = """\
# ROLE
An illustration already exists. You are choosing a few tags to strengthen it.

# THEME — what the picture is meant to be about
{theme}

# WHAT THE PICTURE ALREADY HAS
{present}

# CANDIDATES (all absent from the picture, all suggested by the theme)
{candidates}

# CHOOSE AT MOST {picks}
Pick the ones that would make the existing picture *more* what it is — a
concrete thing the scene plainly lacks, a detail that sharpens the mood it
already has.

You are ADDING to this picture, never editing it. Do NOT pick:
- an ALTERNATIVE to something the picture already has. If she is wearing a
  bikini, one-piece_swimsuit is wrong. If she is walking, sitting is wrong.
  "another option for X" is exactly the answer this step does not want.
- anything that changes the subject, the action, the place or the time of day
- anything redundant with a tag the picture already has
- a person, a body or clothing if the picture is a place; a place if it is a person

Ask of each one: "is this a thing that could be added to the picture without
changing anything already in it?" If the answer is no, leave it.

Fewer is better. Choosing nothing is a valid answer.

# OUTPUT (JSON only)
{{"add": [{{"tag": "<exact candidate>", "why": "<a few words>"}}]}}"""


async def collect_candidates(
    db,
    ollama,
    *,
    theme: str,
    present: set[str],
    limit: int = 60,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list[dict[str, Any]]:
    """Theme-adjacent vocabulary the drafts did not already produce."""
    if not theme.strip():
        return []
    try:
        vec = await ollama.embed(theme)
    except Exception as exc:
        logger.warning("[muse] topup embed failed: %s", exc)
        return []
    try:
        hits = await db.search_wd14_vocab(vec, min_freq=0.01, max_freq=0.80, limit=limit)
    except Exception as exc:
        logger.warning("[muse] topup search failed: %s", exc)
        return []

    present_list = list(present)
    lowered = {t.lower() for t in present}
    out: list[dict[str, Any]] = []
    for hit in hits:
        name = str(hit.get("name") or "").strip().replace(" ", "_")
        if not name or name.lower() in lowered:
            continue
        # Exact absence is not enough. `puddle_reflection` is not `puddle`, but
        # offering it to a picture that already has `puddle` spends a pick on a
        # word for something already there — and the slot cap would drop it
        # again downstream anyway.
        if restates(name, present_list):
            continue
        if float(hit.get("score") or 0.0) < min_score:
            continue
        if is_junk_tag(name):
            continue
        out.append({
            "tag": name,
            "score": hit.get("score"),
            "count": hit.get("count", 0),
        })
    return out


async def pick_reinforcements(
    candidates: list[dict[str, Any]],
    ollama,
    *,
    theme: str,
    present: list[str],
    model: str = "",
    num_ctx: int | None = None,
    picks: int = DEFAULT_PICKS,
) -> list[dict[str, str]]:
    """``[{tag, why}]`` — at most ``picks``, all from ``candidates``."""
    if not candidates:
        return []
    known = {c["tag"].lower(): c["tag"] for c in candidates}
    prompt = _PROMPT.format(
        theme=theme or "(none given)",
        present=", ".join(present[:80]) or "(nothing)",
        candidates=", ".join(c["tag"] for c in candidates),
        picks=picks,
    )
    try:
        raw = await ollama.generate_text(
            prompt,
            model=model or None,
            options=llm_options(model=model, num_ctx=num_ctx),
            fmt="json",
        )
        parsed = parse_json_object(raw if isinstance(raw, str) else str(raw))
    except Exception as exc:
        logger.warning("[muse] topup pick failed: %s", exc)
        return []

    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parsed.get("add") or []:
        if isinstance(item, str):
            tag, why = item, ""
        elif isinstance(item, dict):
            tag, why = str(item.get("tag") or ""), str(item.get("why") or "")
        else:
            continue
        key = tag.strip().lower().replace(" ", "_")
        # Only from the offered list. A model that invents a tag here has
        # skipped the one job this step has, which is choosing.
        if key not in known or key in seen:
            continue
        seen.add(key)
        out.append({"tag": known[key], "why": why[:80]})
        if len(out) >= picks:
            break
    return out


def track_for(tag: str) -> str:
    """Which side of the merge a reinforcement belongs on."""
    return "background" if belongs_to_track(tag, "background") else "person"
