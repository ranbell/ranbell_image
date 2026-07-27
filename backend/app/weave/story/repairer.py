"""One-shot StoryBundle repair from lint defects."""
from __future__ import annotations

import json
import logging
from typing import Any

from ..json_util import parse_json_object
from ..prompt_loader import load_prompt
from .storywright import normalize_story_bundle

logger = logging.getLogger(__name__)


def build_repair_prompt(
    *,
    story_bundle: dict[str, Any],
    defects: list[dict[str, Any]],
    character: dict[str, Any],
    topic: str = "",
) -> str:
    system = load_prompt("repairer.md")
    user = {
        "topic": topic,
        "signature_prop": character.get("signature_prop") or "",
        "prop_tags": character.get("prop_tags") or [],
        "defects": defects,
        "story_bundle": story_bundle,
    }
    return (
        system
        + "\n\n# INPUT\n"
        + json.dumps(user, ensure_ascii=False, indent=2)
        + "\n\nOutput the repaired StoryBundle JSON now."
    )


async def run_repairer(
    ollama,
    *,
    model: str,
    options: dict,
    story_bundle: dict[str, Any],
    defects: list[dict[str, Any]],
    character: dict[str, Any],
    topic: str = "",
) -> dict[str, Any]:
    prompt = build_repair_prompt(
        story_bundle=story_bundle,
        defects=defects,
        character=character,
        topic=topic,
    )
    raw = await ollama.chat_text(
        prompt,
        model=model,
        options=options,
        fmt="json",
        think=False,
    )
    data = parse_json_object(raw)
    return normalize_story_bundle(data)
