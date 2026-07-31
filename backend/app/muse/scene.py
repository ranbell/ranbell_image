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

_CONDENSE_PROMPT = """\
# ROLE
You describe a single illustration in plain prose, for an image generation model.

# THE SCENE
{idea}

# THE TAGS THAT MUST HOLD
{tags}

# RULES
- Exactly two sentences. No more.
- Describe only what is visible in one frame: subject, what she is doing, where
  she is, the light. No backstory, no interior monologue, no "she remembers".
- Do not contradict the tags. Do not list them either — write prose.
- English only.

# OUTPUT (JSON only)
{{"scene": "<two sentences>"}}"""


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


async def condense_to_two_sentences(
    idea: str,
    tags: list[str],
    ollama,
    *,
    model: str,
    num_ctx: int | None = None,
) -> str:
    """A chosen brainstorm idea → the two sentences that ship with the prompt."""
    prompt = _CONDENSE_PROMPT.format(
        idea=str(idea or "").strip()[:2000],
        tags=", ".join(tags[:60]),
    )
    try:
        raw = await ollama.generate_text(
            prompt,
            model=model,
            options=llm_options(model=model, num_ctx=num_ctx),
            fmt="json",
        )
        parsed = parse_json_object(raw if isinstance(raw, str) else str(raw))
        scene = str(parsed.get("scene") or "").strip()
        if scene:
            return scene
    except Exception as exc:
        logger.warning("[muse] scene condense failed: %s", exc)
    # Falling back to the idea's own first sentences is better than shipping
    # nothing: the tags carry the image either way, this is the seasoning.
    return _first_sentences(idea, 2)


def _first_sentences(text: str, n: int) -> str:
    flat = " ".join(str(text or "").split())
    parts = re.split(r"(?<=[.!?。！？])\s+", flat)
    return " ".join(p for p in parts[:n] if p).strip()


def compose_final_prompt(tags: list[str], scene_text: str) -> str:
    """Tags first, prose second.

    The tag line is what the model actually conditions on; the prose nudges
    composition and mood. Putting the prose first buries the tags past the point
    where attention still reaches them.
    """
    line = ", ".join(t for t in tags if t)
    scene = str(scene_text or "").strip()
    return f"{line}\n\n{scene}" if scene else line


def summarise_for_log(candidates: list[dict[str, Any]]) -> str:
    return json.dumps([c.get("title", "") for c in candidates], ensure_ascii=False)
