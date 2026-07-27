"""Personality → visual inference."""
from __future__ import annotations

import json
import logging
from typing import Any

from ..json_util import parse_json_object
from ..prompt_loader import load_prompt
from .split_tags import enforce_identity_prop_split

logger = logging.getLogger(__name__)


def build_personality_prompt(
    *,
    personality_text: str,
    topic: str = "",
    author_style: str = "",
    age_band: str = "",
    gender_hint: str = "",
) -> str:
    system = load_prompt("personalitywright.md")
    user = {
        "personality_text": personality_text,
        "topic": topic,
        "author_style": author_style,
        "age_band": age_band,
        "gender_hint": gender_hint,
    }
    return (
        system
        + "\n\n# INPUT\n"
        + json.dumps(user, ensure_ascii=False, indent=2)
        + "\n\nOutput the JSON now."
    )


def apply_inference_to_character(character: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Merge parsed Personalitywright JSON into character dict (mutates)."""
    personality = data.get("personality") or {}
    visual = data.get("visual_inference") or {}
    identity, props, sig = enforce_identity_prop_split(
        list(visual.get("identity_tags") or []),
        list(visual.get("prop_tags") or []),
        signature_prop=str(visual.get("signature_prop") or ""),
    )
    character["personality"] = personality
    character["identity_tags"] = identity
    character["prop_tags"] = props
    character["signature_prop"] = sig
    character["palette"] = list(visual.get("palette") or [])
    character["do_not"] = list(visual.get("do_not") or [])
    character["reasoning_ja"] = str(visual.get("reasoning_ja") or "")
    character["board_briefs"] = list(data.get("board_briefs") or [])
    character["source"] = "personality"
    return character


async def run_personalitywright(
    ollama,
    *,
    model: str,
    options: dict,
    personality_text: str,
    topic: str = "",
    author_style: str = "",
) -> dict[str, Any]:
    prompt = build_personality_prompt(
        personality_text=personality_text,
        topic=topic,
        author_style=author_style,
    )
    raw = await ollama.chat_text(
        prompt,
        model=model,
        options=options,
        fmt="json",
        think=True,
    )
    data = parse_json_object(raw)
    return data
