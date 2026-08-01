"""S7: turn the merged tag set into a scene the user can choose.

Inspire's brainstorm is the good part of this app — give it a tag set and it
comes back with three to five specific situations an illustrator would actually
want to draw, which is a very different thing from a model describing the tags
back at you. Muse feeds it the merged tags and lets the user pick one; the
chosen idea is then compressed to the two sentences that ride along with the
tags into the final render.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..ai.json_util import parse_json_object
from ..ai.llm_options import llm_options

logger = logging.getLogger(__name__)

_HEADING_RE = re.compile(r"^##\s+(.*)$", re.MULTILINE)

_PROSE_PROMPT = """\
# ROLE
You describe one illustration in plain prose, for an image generation model.

# WHAT THE PICTURE IS
{description}

# THE PICTURE, ASPECT BY ASPECT
{slots}
{idea_block}
# WRITE THREE SENTENCES
1. The subject and what she is doing, where.
2. Her appearance and what she is wearing or carrying — the details that make
   her recognisable.
3. The framing, the light and the style.

# RULES
- Say the same things the aspects above say. Do not add a fact that is not
  there, and do not leave out the character.
- Prose, not tags. Do not list.
- No backstory, no interior monologue, no "she remembers".
- English only.

# OUTPUT (JSON only)
{{"prose": "<three sentences>"}}"""


def parse_brainstorm_sections(markdown: str) -> list[dict[str, str]]:
    """Split the brainstorm stream into ``[{title, body}]`` cards.

    Mirrors what InspirePanel does client-side, so the panel can show the same
    cards without re-implementing the split.
    """
    text = str(markdown or "").strip()
    if not text:
        return []
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [{"title": "", "body": text}]
    out: list[dict[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append({
            "title": m.group(1).strip(),
            "body": text[m.end():end].strip(),
        })
    return out


async def write_prose(
    filled: dict[str, list[str]],
    ollama,
    *,
    model: str,
    num_ctx: int | None = None,
    idea: str = "",
) -> str:
    """The closing paragraph, written from the slots.

    Earlier this condensed a chosen brainstorm idea, and the result could
    disagree with the tags above it — a run themed "came to swim" ended with
    prose about sunbathing while the tags said swimming. Writing from the slots
    cannot contradict them, and the brainstorm idea rides along as flavour
    rather than as the source.
    """
    from . import slots as slot_defs

    description = " ".join(filled.get("description") or []) or "(not given)"
    lines = "\n".join(
        f"{slot.label}: {', '.join(filled.get(slot.key) or [])}"
        for slot in slot_defs.SLOTS
        if filled.get(slot.key) and slot.key != "description"
    )
    prompt = _PROSE_PROMPT.format(
        description=description,
        slots=lines or "(nothing)",
        idea_block=f"\n# THE MOOD SOMEBODY CHOSE\n{str(idea).strip()[:800]}\n" if idea else "",
    )
    try:
        raw = await ollama.generate_text(
            prompt,
            model=model,
            options=llm_options(model=model, num_ctx=num_ctx),
            fmt="json",
        )
        parsed = parse_json_object(raw if isinstance(raw, str) else str(raw))
        prose = str(parsed.get("prose") or "").strip()
        if prose:
            return prose
    except Exception as exc:
        logger.warning("[muse] prose failed: %s", exc)
    # The tags carry the image either way; this is the seasoning.
    return _first_sentences(idea, 3)


def _first_sentences(text: str, n: int) -> str:
    flat = " ".join(str(text or "").split())
    parts = re.split(r"(?<=[.!?。！？])\s+", flat)
    return " ".join(p for p in parts[:n] if p).strip()


def compose_final_prompt(tags: list[str], scene_text: str) -> str:
    """Tags first, prose second.

    Kept for the flat-tag path. The slotted prompt assembles itself in
    ``slots.render_prompt``, which puts the prose in the same place.
    """
    line = ", ".join(t for t in tags if t)
    scene = str(scene_text or "").strip()
    return f"{line}\n\n{scene}" if scene else line


def summarise_for_log(candidates: list[dict[str, Any]]) -> str:
    return json.dumps([c.get("title", "") for c in candidates], ensure_ascii=False)
