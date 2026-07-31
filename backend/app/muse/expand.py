"""S1 theme split and S2 tag gathering.

Neither step invents anything. The split is one small LLM call reusing Inspire's
own theme-expansion prompt; the tags come out of the ``wd14_vocab`` collection
by vector search. The surprise is retrieval-shaped, not model-shaped — see
``vocab_bank`` for how the frequency bands and the ascending-score sort produce
tags that are tethered to the theme but nowhere near the obvious reading of it.
"""
from __future__ import annotations

import logging
from typing import Any

from ..ai.json_util import parse_json_object
from ..ai.llm_options import llm_options
from ..api.inspire import _EXPAND_THEME_PROMPT, _normalize_section
from ..invoke.vocab_bank import (
    compute_frontier_hints,
    get_topic_tags,
    get_vocab_hints,
)
from ..tags.topic_anchors import topic_anchor_groups
from .schema import SPLIT_SECTIONS, TRACKS

logger = logging.getLogger(__name__)

# The split feeds two tracks. Props/action ride with the character (they are
# things she holds and does); mood/camera ride with the background (they are
# properties of the shot, and putting them on the character track makes every
# board a portrait).
TRACK_SECTIONS: dict[str, tuple[str, ...]] = {
    "person": ("character", "props", "action"),
    "background": ("background", "mood", "camera"),
}


async def split_theme(theme: str, ollama, *, model: str, num_ctx: int | None = None) -> dict[str, str]:
    """Theme sentence → six comma-separated tag sections.

    The prompt is English and so is its output, deliberately: ``wd14_vocab``
    holds English Danbooru tags, so a Japanese theme has to cross over
    somewhere, and one instructed model does it better than substring matching.
    """
    prompt = _EXPAND_THEME_PROMPT.format(theme=theme)
    raw = await ollama.generate_text(
        prompt,
        model=model,
        options=llm_options(model=model, num_ctx=num_ctx),
        fmt="json",
    )
    parsed = parse_json_object(raw if isinstance(raw, str) else str(raw))
    return {
        section: _normalize_section(str(parsed.get(section) or "").strip())
        for section in SPLIT_SECTIONS
    }


def _split_tags(text: str) -> list[str]:
    return [
        t.strip().replace(" ", "_")
        for t in str(text or "").split(",")
        if t.strip()
    ]


def track_query(theme: str, split: dict[str, str], track: str) -> str:
    """The text this track's vocabulary search runs against.

    The theme goes in alongside the split sections. Without it a track drifts
    toward its own section's wording and loses the thing the user actually
    asked for; the bilingual anchors bridge a Japanese theme to the English
    vocabulary the embedding index was built from.
    """
    parts = [str(split.get(s) or "") for s in TRACK_SECTIONS[track]]
    anchors = topic_anchor_groups(theme)
    bridged = [word for group in anchors for word in group]
    return " ".join([theme, *parts, *bridged]).strip()


async def gather_tags(
    db,
    ollama,
    *,
    theme: str,
    split: dict[str, str],
    track: str,
    topic_tag_limit: int = 25,
    wildness: int = 3,
    frontier_count: int = 8,
) -> list[dict[str, Any]]:
    """One track's candidate tags, each labelled with where it came from.

    Order matters to the caller: the panel renders these as chips in this order
    and the merge step treats earlier ones as better-grounded.
    """
    query = track_query(theme, split, track)

    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def _add(tag: str, source: str) -> None:
        name = str(tag or "").strip().replace(" ", "_")
        if not name or name.lower() in seen:
            return
        seen.add(name.lower())
        out.append({"tag": name, "source": source})

    # The split's own tags come first: they are the literal reading of the theme.
    for section in TRACK_SECTIONS[track]:
        for tag in _split_tags(split.get(section)):
            _add(tag, "split")

    try:
        for tag in await get_topic_tags(db, ollama, query, None, limit=topic_tag_limit):
            _add(tag, "topic")
    except Exception as exc:
        logger.warning("[muse] topic tags failed for %s: %s", track, exc)

    # The surprise layer, on top of whatever is already grounded.
    grounded = [row["tag"] for row in out]
    try:
        hints = await get_vocab_hints(db, ollama, grounded, wildness=wildness)
        for source in ("stranger", "lunatic"):
            for tag in hints.get(source) or []:
                _add(tag, source)
    except Exception as exc:
        logger.warning("[muse] vocab hints failed for %s: %s", track, exc)

    try:
        frontier = await compute_frontier_hints(db, n_tags=frontier_count)
        # frontier splits by character/scene/mood; take the half this track owns.
        buckets = ("character",) if track == "person" else ("scene", "mood")
        for bucket in buckets:
            for tag in frontier.get(bucket) or []:
                _add(tag, "frontier")
    except Exception as exc:
        logger.warning("[muse] frontier hints failed for %s: %s", track, exc)

    return out


def apply_rejections(
    rows: list[dict[str, Any]], rejected: list[str], removal: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop what the user clicked away and what Admin excludes globally."""
    reject_set = {str(t).strip().lower().replace(" ", "_") for t in rejected if str(t).strip()}
    blocked = reject_set | {str(t).lower() for t in removal}
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for row in rows:
        if row["tag"].lower() in blocked:
            dropped.append(row["tag"])
        else:
            kept.append(row)
    return kept, dropped
