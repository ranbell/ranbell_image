"""Subjective emotion scoring for images using Ollama LLM."""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

EMOTION_DIMENSIONS = [
    "loneliness",
    "nostalgia",
    "ephemeral",
    "melancholy",
    "serenity",
    "wonder",
    "joy",
    "tension",
    "warmth",
    "mystery",
    "desolation",
    "vitality",
]

_EMPTY_SCORES: dict[str, float] = {dim: 0.0 for dim in EMOTION_DIMENSIONS}

_PROMPT_TEMPLATE = (
    "You are an emotion analyst for AI-generated images.\n"
    "Given the image generation prompt and visual tags below, assign a score from 0.0 to 1.0\n"
    "for each of the 12 emotional dimensions.\n"
    "0.0 = completely absent, 1.0 = strongly dominant.\n"
    "Be nuanced — most images will have at most 2-3 high scores (≥0.6).\n\n"
    "Prompt: {prompt}\n"
    "Visual tags: {tags}\n\n"
    "Output ONLY valid JSON, no markdown:\n"
    '{{"loneliness":0.0,"nostalgia":0.0,"ephemeral":0.0,"melancholy":0.0,'
    '"serenity":0.0,"wonder":0.0,"joy":0.0,"tension":0.0,'
    '"warmth":0.0,"mystery":0.0,"desolation":0.0,"vitality":0.0}}'
)


async def score_emotions(
    positive_prompt: str,
    wd14_tags: list[str],
    ollama,
    model: str | None = None,
) -> dict[str, float] | None:
    """Score 12 emotion dimensions for one image.

    Returns a dict of {dimension: score} on success, None on failure.
    Tags are capped at 60 to avoid context overflow; prompt at 500 chars.
    """
    tags_str = ", ".join(wd14_tags[:60])
    prompt_str = (positive_prompt or "")[:500]

    if not prompt_str and not tags_str:
        return None

    full_prompt = _PROMPT_TEMPLATE.format(prompt=prompt_str, tags=tags_str)

    try:
        kwargs: dict = {}
        if model:
            kwargs["model"] = model
        raw = await ollama.generate_text(
            full_prompt,
            fmt="json",
            options={"temperature": 0.1, "num_ctx": 2048},
            **kwargs,
        )
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw.strip())
        data = json.loads(raw)
        scores: dict[str, float] = {}
        for dim in EMOTION_DIMENSIONS:
            v = data.get(dim)
            if isinstance(v, (int, float)):
                scores[dim] = round(max(0.0, min(1.0, float(v))), 3)
            else:
                scores[dim] = 0.0
        return scores
    except Exception as e:
        logger.warning("score_emotions failed: %s", e)
        return None
