"""S1: the LLM writes the board prompts directly.

This used to be two steps — split the theme into six sections, then search
``wd14_vocab`` for tags near each one — and the search was where every run went
wrong. Asked for "library, rain, stained glass" it returned ``closed_eyes`` and
``kimono``, because people get photographed in libraries; asked for a character
whose section had become wardrobe-only it returned ``maid_headdress`` and
``chinese_clothes`` and the board came back as a costume chart. Vector
neighbours of a theme are not a description of a picture.

So the model writes the tags. It is good at "thirty danbooru tags for a rainy
library", which is a much easier request than the one the search was answering,
and the result goes straight to the board.

The vocabulary search still exists — it moved to ``topup``, after the image is
real, where its job is small and checkable: name a few things the picture is
missing. Retrieval is better at that than at inventing a picture from a phrase.
"""
from __future__ import annotations

import logging
from typing import Any

from ..ai.json_util import parse_json_object
from ..ai.llm_options import llm_options
from ..api.inspire import _normalize_section
from ..tags.conflict import contradicts_any
from ..tags.junk import is_junk_tag
from .schema import TRACKS
from .tracks import belongs_to_track

logger = logging.getLogger(__name__)

TAGS_PER_TRACK = 30

_PROMPT = """\
# ROLE
You are a Danbooru tag expert writing the prompt for one illustration. You write
two tag lists: one for the SETTING, one for the CHARACTER in it.

# THEME
{theme}
{character_block}
# BACKGROUND — about {count} tags
The place and nothing else: architecture, furniture, weather, time of day, the
quality of the light, objects sitting in the scene.
NO people. No bodies, no faces, no clothing, no poses — not even "1girl".
Somebody else is drawing the character; you are drawing the room she walks into.

# CHARACTER — about {count} tags
Her, in this scene: the clothes this theme calls for, what she is holding, her
pose, her hands, her expression.
NO location, NO backdrop, NO weather, no "simple_background", no scenery.

# RULES FOR BOTH
- Real Danbooru tags, underscore_format (long_hair, stained_glass, holding_book).
- Be specific and visual. "old" and "light" say nothing; "peeling_paint" and
  "backlighting" say something.
- Never negate. Leave a thing out instead of writing no_humans or no_eyes.
- No quality words (masterpiece, best_quality, highres, absurdres).
- No framing or sheet words (multiple_views, reference_sheet, alternate_costume,
  border, fisheye, chibi).
{identity_rule}
- Do not repeat a tag across the two lists.

# OUTPUT (JSON only)
{{"background": "tag, tag, ...", "person": "tag, tag, ..."}}"""

_IDENTITY_RULE_LOCKED = (
    "- The character's hair, eyes and body are FIXED above. Never write a hair\n"
    "  colour, hair length, eye colour, body type, age or species — anything you\n"
    "  write there can only contradict her."
)
_IDENTITY_RULE_FREE = (
    "- Give the character a hair colour, eye colour and hair style; nobody has\n"
    "  chosen them yet."
)


def _character_block(character: dict[str, Any]) -> str:
    identity = [t for t in (character.get("identity_tags") or []) if t]
    if not identity:
        return ""
    bits = ["\n# THE CHARACTER (FIXED — do not redescribe)", ", ".join(identity)]
    wardrobe = [t for t in (character.get("outfit_tags") or []) if t]
    if wardrobe:
        bits.append("Her usual clothes (change them if the theme calls for it): "
                    + ", ".join(wardrobe))
    props = [t for t in (character.get("prop_tags") or []) if t]
    if props:
        bits.append("She usually carries: " + ", ".join(props))
    return "\n".join(bits) + "\n"


async def compose_tracks(
    theme: str,
    character: dict[str, Any],
    ollama,
    *,
    model: str,
    num_ctx: int | None = None,
    count: int = TAGS_PER_TRACK,
) -> dict[str, list[dict[str, Any]]]:
    """Theme (+ character) → ``{track: [{tag, source}]}`` ready to render.

    Both lists come out of one call so they describe the same picture; two
    calls drift into two pictures that happen to share a theme.
    """
    identity = [t for t in (character.get("identity_tags") or []) if t]
    prompt = _PROMPT.format(
        theme=theme,
        count=count,
        character_block=_character_block(character),
        identity_rule=_IDENTITY_RULE_LOCKED if identity else _IDENTITY_RULE_FREE,
    )
    raw = await ollama.generate_text(
        prompt,
        model=model,
        options=llm_options(model=model, num_ctx=num_ctx),
        fmt="json",
    )
    parsed = parse_json_object(raw if isinstance(raw, str) else str(raw))

    out: dict[str, list[dict[str, Any]]] = {}
    for track in TRACKS:
        out[track] = _clean(parsed.get(track), track, identity)
    return out


def _clean(raw: Any, track: str, identity: list[str]) -> list[dict[str, Any]]:
    """Normalise, then drop what this track should never carry.

    The model is told all of this in the prompt and mostly obeys. The filter is
    here because "mostly" put ``1girl`` in a background list often enough to
    render a person into an empty room.
    """
    if isinstance(raw, (list, tuple)):
        raw = ", ".join(str(v) for v in raw)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for piece in str(raw or "").split(","):
        tag = _normalize_section(piece.strip()) or piece.strip()
        tag = tag.strip().replace(" ", "_")
        if not tag or tag.lower() in seen:
            continue
        if is_junk_tag(tag) or not belongs_to_track(tag, track):
            continue
        if identity and contradicts_any(tag, identity):
            continue
        seen.add(tag.lower())
        out.append({"tag": tag, "source": "compose"})
    return out
