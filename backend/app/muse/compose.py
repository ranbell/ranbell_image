"""S1: fill the prompt's slots.

The model writes each slot; the vocabulary tops it up; every slot is capped.

Two earlier shapes failed here. A vector search over the whole theme returned
neighbours of a phrase rather than a description of a picture — ``closed_eyes``
for "library, rain", because people get photographed in libraries. Then the
model wrote thirty free tags per track and padded with synonyms of whatever it
found most interesting: ``swimwear``, ``black_bikini``, ``bikini``, one fact
written three times and weighted three times in the render.

Slots fix both. The model answers one small question per aspect instead of one
big one, so it cannot spend the whole budget on swimwear. And the vocabulary
search comes back with a *narrow* query — "clothing for a pool scene" rather
than "a pool scene" — which is the kind of question retrieval is actually good
at, the same reason ``topup`` works.

Order of operations per slot: the model proposes, the vocabulary supplements up
to the cap, near-restatements are dropped, and the user can replace the whole
thing afterwards.
"""
from __future__ import annotations

import logging
from typing import Any

from ..ai.json_util import parse_json_object
from ..ai.llm_options import llm_options
from ..api.inspire import _normalize_section
from ..tags.conflict import contradicts_any
from ..tags.junk import is_junk_tag
from . import slots as slot_defs
from .slots import COMPOSED, Slot

logger = logging.getLogger(__name__)

_PROMPT = """\
# ROLE
You are writing the prompt for one illustration, one aspect at a time.

# THEME
{theme}
{character_block}
# THE ASPECTS
{aspects}

# RULES
- Real Danbooru tags, underscore_format (long_hair, stained_glass, holding_book).
- **{cap_rule}** Do not pad. If an aspect only warrants one tag, give one.
- Never write two tags that say the same thing. "swimwear, black_bikini, bikini"
  is one fact spent three times; write the fact once and move on.
- Be specific and visual. "old" and "light" say nothing; "peeling_paint" and
  "backlighting" say something.
- Every tag must be a thing that is plainly there in THIS scene. Not a thing the
  previous tag reminded you of: a bakery has a knife, and a knife is not a
  reason to write sword. Whatever you write, the picture will contain.
- One body can hold one pose. "standing, kneeling" is two pictures; pick one.
- Never negate. Leave a thing out instead of writing no_humans or no_eyes.
- No quality words (masterpiece, best_quality, highres), no framing words
  (wide_shot, close-up, multiple_views) — those are chosen elsewhere.
{identity_rule}

# OUTPUT (JSON only)
{{{output_shape}}}"""

_IDENTITY_RULE_LOCKED = (
    "- The character's hair, eyes and body are FIXED above. Never write a hair\n"
    "  colour, hair length, eye colour, body type, age or species — anything you\n"
    "  write there can only contradict her."
)
_IDENTITY_RULE_FREE = (
    "- Nobody has chosen her hair or eye colour yet; the Outfit aspect may\n"
    "  assume any."
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


def _aspect_lines(active: tuple[Slot, ...]) -> str:
    return "\n".join(
        f"- {s.label} (at most {s.cap}): {s.guidance}" for s in active
    )


async def compose_slots(
    theme: str,
    character: dict[str, Any],
    ollama,
    *,
    model: str,
    num_ctx: int | None = None,
    db=None,
    supplement: bool = True,
) -> dict[str, list[dict[str, Any]]]:
    """Theme (+ character) → ``{slot: [{tag, source}]}``.

    ``source`` is ``compose`` for what the model wrote and ``vocab`` for what
    retrieval added, so the panel can show which is which and the user can throw
    either away.
    """
    active = COMPOSED
    identity = [t for t in (character.get("identity_tags") or []) if t]
    prompt = _PROMPT.format(
        theme=theme,
        character_block=_character_block(character),
        aspects=_aspect_lines(active),
        cap_rule="Never exceed an aspect's limit.",
        identity_rule=_IDENTITY_RULE_LOCKED if identity else _IDENTITY_RULE_FREE,
        output_shape=", ".join(f'"{s.key}": "tag, tag"' for s in active),
    )

    parsed: dict[str, Any] = {}
    try:
        raw = await ollama.generate_text(
            prompt,
            model=model,
            options=llm_options(model=model, num_ctx=num_ctx),
            fmt="json",
        )
        parsed = parse_json_object(raw if isinstance(raw, str) else str(raw))
    except Exception as exc:
        logger.warning("[muse] compose failed: %s", exc)

    filled: dict[str, list[dict[str, Any]]] = {}
    for slot in active:
        written = _clean(parsed.get(slot.key), slot, identity)
        filled[slot.key] = [{"tag": t, "source": "compose"} for t in written]

    if supplement and db is not None:
        await _supplement(filled, active, theme, identity, db, ollama)

    # Cap and de-restate last, so a slot the vocabulary padded is trimmed too.
    for slot in active:
        rows = filled.get(slot.key) or []
        kept = slot_defs.dedupe_slot([r["tag"] for r in rows], slot.cap)
        by_tag = {r["tag"]: r for r in rows}
        filled[slot.key] = [by_tag[t] for t in kept]
    return filled


def _clean(raw: Any, slot: Slot, identity: list[str]) -> list[str]:
    if isinstance(raw, (list, tuple)):
        raw = ", ".join(str(v) for v in raw)
    # Description is a sentence. Splitting it on commas and underscoring the
    # pieces turned "A pink haired girl walks along a row of cherry trees" into
    # one enormous tag, which is not a sentence and not a tag either.
    if slot.key == "description":
        text = " ".join(str(raw or "").split())
        return [text] if text else []
    out: list[str] = []
    for piece in str(raw or "").split(","):
        tag = (_normalize_section(piece.strip()) or piece.strip()).strip().replace(" ", "_")
        if not tag or is_junk_tag(tag):
            continue
        if identity and contradicts_any(tag, identity):
            continue
        out.append(tag)
    return out


async def _supplement(
    filled: dict[str, list[dict[str, Any]]],
    active: tuple[Slot, ...],
    theme: str,
    identity: list[str],
    db,
    ollama,
) -> None:
    """Top each short slot up from the vocabulary.

    The query is the slot's own question plus the theme — "clothing and garments
    / came to swim at a pool" — which is narrow enough that the neighbours are
    about clothing rather than about pools in general. This is the same reason
    the top-up step works and the old whole-theme search did not.
    """
    for slot in active:
        rows = filled.get(slot.key) or []
        if len(rows) >= slot.cap or not slot.query:
            continue
        # An exclusive slot the model already answered is not short, it is done.
        if slot.exclusive and rows:
            continue
        try:
            vec = await ollama.embed(f"{slot.query} / {theme}")
            hits = await db.search_wd14_vocab(
                vec, min_freq=0.01, max_freq=0.80, limit=40,
            )
        except Exception as exc:
            logger.warning("[muse] slot supplement failed for %s: %s", slot.key, exc)
            continue

        have = {r["tag"].lower() for r in rows}
        for hit in hits:
            if len(rows) >= slot.cap:
                break
            tag = str(hit.get("name") or "").strip().replace(" ", "_")
            key = tag.lower()
            if not tag or key in have or is_junk_tag(tag):
                continue
            # Retrieval is only allowed to fill the slot it was asked about.
            # Without this the pool search puts `swimsuit` into Place.
            if not slot_defs.accepts(slot, tag):
                continue
            if identity and contradicts_any(tag, identity):
                continue
            # `expressionless` next to `happy`, `kneeling` next to `walking` —
            # the slot already answered, and a second answer is not a top-up.
            if contradicts_any(tag, [r["tag"] for r in rows]):
                continue
            have.add(key)
            rows.append({"tag": tag, "source": "vocab"})
        filled[slot.key] = rows


def locked_slots(character: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Character and Body, straight off the chosen preset."""
    identity = [t for t in (character.get("identity_tags") or []) if t]
    body_slot = slot_defs.BY_KEY["body"]
    body = [t for t in identity if slot_defs.accepts(body_slot, t)]
    return {
        "character": [{"tag": t, "source": "character"} for t in identity if t not in body],
        "body": [{"tag": t, "source": "character"} for t in body],
    }
