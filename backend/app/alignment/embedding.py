from __future__ import annotations

import asyncio
import math

from ..ai.ollama import OllamaClient
from .bm25_matcher import normalize_tag


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


async def compute_alignment_score(
    prompt: str,
    tags_text: str,
    ollama: OllamaClient,
    model: str | None = None,
) -> float:
    norm_prompt = normalize_tag(prompt)
    norm_tags = normalize_tag(tags_text)
    emb_prompt, emb_tags = await asyncio.gather(
        ollama.embed(norm_prompt, model=model),
        ollama.embed(norm_tags, model=model),
    )
    score = cosine_similarity(emb_prompt, emb_tags)
    return round(max(0.0, min(1.0, score)), 4)
