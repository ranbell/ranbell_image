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
from . import vocab
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
  Description is the one exception: it is an English sentence, written with
  spaces like ordinary prose. Never underscore it.
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
    "- The character's hair, eyes and BUILD are FIXED above. Never write a hair\n"
    "  colour, hair length, eye colour, age, species, or a build word like slim\n"
    "  or petite — anything you write there can only contradict her.\n"
    "- Her body PARTS are not fixed and the Body aspect wants them. Which parts\n"
    "  the picture must show is decided by the theme, not by her: if she was\n"
    "  caught in the rain, name the legs and name them wet."
)
_IDENTITY_RULE_FREE = (
    "- Nobody has chosen her hair or eye colour yet; the Outfit aspect may\n"
    "  assume any."
)


def _joined(values: Any, limit: int) -> str:
    kept = [str(v).strip() for v in list(values or []) if str(v).strip()]
    return ", ".join(kept[:limit])


def _character_block(character: dict[str, Any]) -> str:
    """Who she is, not just what she looks like.

    This carried the identity tags, her wardrobe and her props, and stopped
    there — so every aspect that should differ between two characters in the
    same situation didn't. A stargazing theme gave the patient, solitary
    observer `Emotion: blush`, and her `thermos coffee` never reached a cold
    hilltop although it is the second thing on her list of likes.

    The preset has all of it and always did; nothing here asked. The shape is
    the one ``characters/board.py`` uses to cast her reference sheet, which is
    the same question in a different frame: what does *this* person do here.
    """
    identity = [t for t in (character.get("identity_tags") or []) if t]
    if not identity:
        return ""
    bits = ["\n# THE CHARACTER (FIXED — do not redescribe)", ", ".join(identity)]

    person = character.get("personality") or {}
    for label, value in (
        ("She is",        _joined(person.get("traits"), 6)),
        ("In one line",   str(person.get("summary") or "").strip()),
        ("Privately",     _joined(person.get("inner"), 2)),
        ("Likes",         _joined(person.get("likes"), 5)),
        ("Dislikes",      _joined(person.get("dislikes"), 3)),
        ("Her face does", _joined(character.get("expression_vocab"), 4)),
        ("She tends to",  _joined(character.get("gesture_vocab"), 4)),
        ("Her colours",   _joined(character.get("palette"), 4)),
    ):
        if value:
            bits.append(f"{label}: {value}")

    style = str(person.get("outfit_style") or "").strip()
    wardrobe = [t for t in (character.get("outfit_tags") or []) if t]
    if wardrobe:
        line = "Her usual clothes (change them if the theme calls for it): " \
               + ", ".join(wardrobe)
        bits.append(f"{line} — {style}" if style else line)
    elif style:
        bits.append(f"She usually dresses: {style}")
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

    _rehome(filled, active)

    if supplement and db is not None:
        await _supplement(filled, active, theme, identity, db, ollama)

    _settle(filled, active)

    # Cap and de-restate last, so a slot the vocabulary padded is trimmed too.
    for slot in active:
        rows = filled.get(slot.key) or []
        kept = slot_defs.dedupe_slot([r["tag"] for r in rows], slot.cap)
        by_tag = {r["tag"]: r for r in rows}
        filled[slot.key] = [by_tag[t] for t in kept]
    return filled


def _rehome(filled: dict[str, list[dict[str, Any]]], active: tuple[Slot, ...]) -> None:
    """Move a written tag to the aspect the catalog says it belongs to.

    The model answers each aspect separately and still files things under the
    wrong one — a stargazing theme put ``binoculars`` under Action, spending a
    pose on a prop and leaving the pose unwritten.

    Moves only when the slot it was written into *rejects* it and another slot
    claims it. Several slots share a catalog set on purpose — ``PROPS`` belongs
    to both Accessories and Object, because "what she holds" and "what is in the
    room" are the same nouns — so preferring the first claimant would drag every
    prop forward into Accessories. If the slot it is in accepts it, the model's
    placement stands, and an unrouted tag stays too: the catalog's silence is
    not an opinion.
    """
    keys = {s.key for s in active}
    for slot in active:
        # Description is a sentence, and no slot accepts a sentence — least of
        # all Description itself, which has no catalog behind it. Left in, this
        # filed "A blue-haired girl is waiting for a bus at a bus stop in the
        # pouring rain." under Action, because the routing found the word
        # "waiting" in it.
        if slot.key == "description":
            continue
        rows = filled.get(slot.key) or []
        staying: list[dict[str, Any]] = []
        for row in rows:
            if slot_defs.accepts(slot, row["tag"]):
                staying.append(row)
                continue
            home = slot_defs.place_tag(row["tag"])
            if home and home != slot.key and home in keys:
                filled.setdefault(home, []).append(row)
            else:
                staying.append(row)
        filled[slot.key] = staying


def _settle(filled: dict[str, list[dict[str, Any]]], active: tuple[Slot, ...]) -> None:
    """Drop what a later aspect says that an earlier one has already settled.

    Contradiction was checked inside a slot and against the character, never
    across slots — so Place said ``night`` and Light said ``twilight``, and the
    render averaged two hours that cannot both be true. Slot order decides:
    Place comes before Light, so the hour the scene is in wins over the hour
    its lighting implies.
    """
    accepted: list[str] = []
    for slot in active:
        keep: list[dict[str, Any]] = []
        for row in filled.get(slot.key) or []:
            # Description is a sentence; it settles nothing and contradicts
            # nothing, and its words are not tags anyone can restate.
            if slot.key == "description":
                keep.append(row)
                continue
            if contradicts_any(row["tag"], accepted):
                continue
            # One fact spent three times is what the budgets exist to stop, and
            # they only ever stopped it inside a slot. A stargazing theme wrote
            # `looking_through_telescope`, `telescope` and `large_telescope`
            # into three different aspects and the render weighted the telescope
            # three times. Whichever aspect claims it first keeps it.
            if slot_defs.restates(row["tag"], accepted):
                continue
            keep.append(row)
            accepted.append(row["tag"])
        filled[slot.key] = keep


def _clean(raw: Any, slot: Slot, identity: list[str]) -> list[str]:
    if isinstance(raw, (list, tuple)):
        raw = ", ".join(str(v) for v in raw)
    # Description is a sentence. Splitting it on commas and underscoring the
    # pieces turned "A pink haired girl walks along a row of cherry trees" into
    # one enormous tag, which is not a sentence and not a tag either.
    if slot.key == "description":
        # Told "underscore_format" at the top of the rules, the model obeys it
        # here too and writes `a_slim_girl_is_looking_through_telescope_on_hill`
        # — a sentence wearing a tag's clothes. The rules now say otherwise;
        # this puts the spaces back when they are ignored anyway.
        text = " ".join(str(raw or "").replace("_", " ").split())
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

    A hit is spent once. Slots share catalog sets deliberately, so the same tag
    is acceptable to several of them and every short one took a copy —
    ``hair_ribbon`` landed in Outfit and Accessories both, one fact holding two
    budgets. The first short slot that accepts it keeps it.
    """
    spent: set[str] = {
        r["tag"].lower()
        for rows in filled.values() for r in rows or []
    }
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
                vec, min_freq=vocab.MIN_FREQ, max_freq=vocab.MAX_FREQ, limit=40,
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
            if not tag or key in have or key in spent or is_junk_tag(tag):
                continue
            # This step takes the first thing its slot accepts, however far
            # down the list that is. Without a floor the Object slot reached
            # rank 37 of 40 to find `sword`.
            if float(hit.get("score") or 0.0) < vocab.SUPPLEMENT_MIN_SCORE:
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
            spent.add(key)
            rows.append({"tag": tag, "source": "vocab"})
        filled[slot.key] = rows


def locked_slots(character: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Character and Body, straight off the chosen preset.

    Character replaces whatever was composed — it is hers and not open to a
    second opinion. Body only *seeds*: what she is built like is hers, but
    which parts the picture shows is the theme's, and the caller merges the two.
    Overwriting Body here discarded everything the model had written about the
    situation, so a run about soaked legs came back holding only `slim`.
    """
    identity = [t for t in (character.get("identity_tags") or []) if t]
    body_slot = slot_defs.BY_KEY["body"]
    body = [t for t in identity if slot_defs.accepts(body_slot, t)]
    return {
        "character": [{"tag": t, "source": "character"} for t in identity if t not in body],
        "body": [{"tag": t, "source": "character"} for t in body],
    }


def seed_locked(
    filled: dict[str, list[dict[str, Any]]], character: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Apply ``locked_slots`` to a composed set: replace Character, seed Body."""
    locked = locked_slots(character)
    out = dict(filled)
    out["character"] = locked["character"]
    seed = locked["body"]
    seeded = {r["tag"].lower() for r in seed}
    rest = [r for r in (out.get("body") or []) if r["tag"].lower() not in seeded]
    kept = slot_defs.dedupe_slot(
        [r["tag"] for r in seed + rest], slot_defs.BY_KEY["body"].cap,
    )
    by_tag = {r["tag"]: r for r in seed + rest}
    out["body"] = [by_tag[t] for t in kept]
    return out
