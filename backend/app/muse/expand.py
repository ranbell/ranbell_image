"""S1 theme split and S2 tag gathering.

Neither step invents anything. The split is one small LLM call; the tags come
out of the ``wd14_vocab`` collection by vector search. The surprise is
retrieval-shaped, not model-shaped — see ``vocab_bank`` for how the frequency
bands and the ascending-score sort produce tags that are tethered to the theme
but nowhere near the obvious reading of it.

Two things here exist because a real run went wrong without them.

**The split is told who the character is.** Left to itself it writes a fresh
appearance every time — one run described "dark blue hair, no eyes" for a
character with black hair and brown eyes. Nine of that track's twenty-three
retrieved tags then came back ``blue_*``, and the board rendered a blue stranger.

**Each track's query is steered away from the other track.** A plain nearest
search on "library, rain, stained glass" returns ``closed_eyes`` and ``kimono``
too, because people are photographed in libraries; the background board then
rendered a person. Subtracting a person-concept vector from the query fixes the
cause, and an axis filter catches what the subtraction misses.
"""
from __future__ import annotations

import logging
from typing import Any

from ..ai.json_util import parse_json_object
from ..ai.llm_options import llm_options
from ..ai.vecmath import subtract_concept
from ..api.inspire import _normalize_section
from ..invoke.vocab_bank import (
    compute_frontier_hints,
    get_topic_tags,
    get_vocab_hints,
)
from ..tags import catalog as tag_catalog
from ..tags.conflict import contradicts_any
from ..tags.junk import is_junk_tag
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

# What each track is steered *away* from. Deliberately plain words rather than
# tags: this is embedded as a direction in concept space, not matched literally.
TRACK_AWAY_FROM: dict[str, str] = {
    "background": "a person, a girl, a face, eyes, hair, clothing, a portrait of someone",
    "background_negative": "1girl, 1boy, solo, multiple_girls, multiple_boys, portrait, face",
    "person": "an empty landscape, scenery, a room with nobody in it, a plain studio backdrop",
}

_BASE_SPLIT_PROMPT = """\
# ROLE
You are a Danbooru tag expert. Given a theme/topic, generate specific danbooru-compatible tags
for each of six categories. Think creatively and artistically — avoid obvious/generic tags.
Prefer visually striking, specific, non-obvious choices that create a vivid scene.

# THEME
{theme}
{character_block}
# RULES
- All tags must be real Danbooru tags (underscore_format, e.g. long_hair, coffee_cup)
- CHARACTER: {character_rule}
- BACKGROUND: location, time of day, weather, architectural/natural elements (5-10 tags)
- PROPS & ACCESSORIES: held objects, worn accessories, jewelry, nearby props (4-8 tags)
- ACTION: pose, gesture, facial expression, body language (3-6 tags)
- MOOD: lighting style, color palette, emotional atmosphere (e.g. soft_lighting, warm_color_palette, melancholic, dramatic_lighting) (3-6 tags)
- CAMERA: shot framing and angle (e.g. close-up, wide_shot, from_above, dutch_angle, full_body) (2-4 tags)
- Do NOT include quality meta-tags (masterpiece, best_quality, highres, etc.)
- Do NOT include negated tags (no_humans, no_eyes, ...) — leave a feature out instead of negating it
- Do NOT repeat tags across sections

# OUTPUT (JSON only)
{{
  "character": "tag1, tag2, ...",
  "background": "tag1, tag2, ...",
  "props": "tag1, tag2, ...",
  "action": "tag1, tag2, ...",
  "mood": "tag1, tag2, ...",
  "camera": "tag1, tag2, ..."
}}"""

_FREE_CHARACTER_RULE = (
    "hair color, eye color, hair style, clothing items (5-10 tags)"
)
_LOCKED_CHARACTER_RULE = (
    "clothing and worn items ONLY, chosen to suit this theme (5-10 tags). "
    "The character's body is already fixed above — do NOT output hair colour, "
    "hair style, eye colour, body type, age, or species. Those are decided."
)


def _character_block(identity_tags: list[str]) -> str:
    if not identity_tags:
        return ""
    return (
        "\n# THE CHARACTER (FIXED — do not redescribe)\n"
        + ", ".join(identity_tags)
        + "\n"
    )


async def split_theme(
    theme: str,
    ollama,
    *,
    model: str,
    num_ctx: int | None = None,
    identity_tags: list[str] | None = None,
) -> dict[str, str]:
    """Theme sentence → six comma-separated tag sections.

    The prompt is English and so is its output, deliberately: ``wd14_vocab``
    holds English Danbooru tags, so a Japanese theme has to cross over
    somewhere, and one instructed model does it better than substring matching.

    When ``identity_tags`` is given the character is presented as fixed and the
    model is asked for wardrobe only — anything it writes about hair or eyes
    would be a description of somebody else.
    """
    identity = [t for t in (identity_tags or []) if t]
    prompt = _BASE_SPLIT_PROMPT.format(
        theme=theme,
        character_block=_character_block(identity),
        character_rule=_LOCKED_CHARACTER_RULE if identity else _FREE_CHARACTER_RULE,
    )
    raw = await ollama.generate_text(
        prompt,
        model=model,
        options=llm_options(model=model, num_ctx=num_ctx),
        fmt="json",
    )
    parsed = parse_json_object(raw if isinstance(raw, str) else str(raw))

    out: dict[str, str] = {}
    for section in SPLIT_SECTIONS:
        text = _normalize_section(str(parsed.get(section) or "").strip())
        tags = [t for t in split_tag_text(text) if not is_junk_tag(t)]
        # Even told not to, a model sometimes returns an appearance. Anything
        # contradicting the locked character is a description of someone else.
        if identity:
            tags = [t for t in tags if not conflicts_with_identity(t, identity)]
        out[section] = ", ".join(tags)
    return out


def conflicts_with_identity(tag: str, identity_tags: list[str]) -> bool:
    """True when ``tag`` contradicts a locked identity tag.

    Slot-based, not token-based: ``blue_hair`` loses to ``black_hair`` because
    both are a hair *colour*, while ``long_hair`` survives because length and
    colour are different slots and she can have both.
    """
    return contradicts_any(tag, identity_tags)


def split_tag_text(text: str) -> list[str]:
    return [
        t.strip().replace(" ", "_")
        for t in str(text or "").split(",")
        if t.strip()
    ]


# ── Track membership ────────────────────────────────────────────────────────
# `get_tag_axis` covers most of it; the raw frozensets cover what the axis map
# lumps into `always_fixed`, which mixes person attributes with props and
# composition and so cannot be dropped wholesale.
_PERSON_AXES = frozenset({"hair", "emotion", "action", "clothing", "parts"})
_SCENE_AXES = frozenset({"location", "time_weather"})

_PERSON_SETS = (
    tag_catalog.COUNT, tag_catalog.EYE_SHAPES, tag_catalog.BODY,
    tag_catalog.SKIN_FACE, tag_catalog.RACE,
)
_SCENE_SETS = (
    tag_catalog.ENVIRONMENT, tag_catalog.BACKGROUND, tag_catalog.ABSTRACT_BG,
)


def is_person_tag(tag: str) -> bool:
    name = str(tag or "").lower()
    if tag_catalog.get_tag_axis(name) in _PERSON_AXES:
        return True
    return any(name in s for s in _PERSON_SETS)


def is_scene_tag(tag: str) -> bool:
    name = str(tag or "").lower()
    if tag_catalog.get_tag_axis(name) in _SCENE_AXES:
        return True
    if any(name in s for s in _SCENE_SETS):
        return True
    # `blue_background` and friends are not in ABSTRACT_BG and have no axis, yet
    # they are exactly what turns a character board into a plain studio shot.
    return name.endswith("_background")


def belongs_to_track(tag: str, track: str) -> bool:
    """False when this tag is the other track's job."""
    if track == "background":
        return not is_person_tag(tag)
    return not is_scene_tag(tag)


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


async def track_query_vector(
    ollama, theme: str, split: dict[str, str], track: str, *, strength: float = 1.0,
) -> list[float] | None:
    """Embed this track's query, steered away from the other track's subject."""
    text = track_query(theme, split, track)
    if not text:
        return None
    try:
        base = await ollama.embed(text)
    except Exception as exc:
        logger.warning("[muse] track embed failed for %s: %s", track, exc)
        return None
    away = TRACK_AWAY_FROM.get(track)
    if not away or strength <= 0:
        return base
    try:
        return subtract_concept(base, await ollama.embed(away), strength)
    except Exception as exc:
        logger.warning("[muse] concept subtraction failed for %s: %s", track, exc)
        return base


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
    subtract_strength: float = 1.0,
    popularity_weight: float = 0.35,
    model: str = "",
) -> list[dict[str, Any]]:
    """One track's candidate tags, each labelled with where it came from.

    Order matters to the caller: the panel renders these as chips in this order
    and the merge step treats earlier ones as better-grounded.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def _add(tag: str, source: str) -> None:
        name = str(tag or "").strip().replace(" ", "_")
        if not name or name.lower() in seen:
            return
        if is_junk_tag(name) or not belongs_to_track(name, track):
            return
        seen.add(name.lower())
        out.append({"tag": name, "source": source})

    # The split's own tags come first: they are the literal reading of the theme.
    for section in TRACK_SECTIONS[track]:
        for tag in split_tag_text(split.get(section)):
            _add(tag, "split")

    query_vec = await track_query_vector(
        ollama, theme, split, track, strength=subtract_strength,
    )
    if query_vec:
        try:
            topic = await get_topic_tags(
                db, ollama, track_query(theme, split, track), None,
                limit=topic_tag_limit,
                query_vec=query_vec,
                popularity_weight=popularity_weight,
                model=model,
            )
            for tag in topic:
                _add(tag, "topic")
        except Exception as exc:
            logger.warning("[muse] topic tags failed for %s: %s", track, exc)

    # The surprise layer, on top of whatever is already grounded. No popularity
    # weighting here on purpose — it would rank the surprise straight back out.
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
        buckets = ("character", "mood") if track == "person" else ("scene", "mood")
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
