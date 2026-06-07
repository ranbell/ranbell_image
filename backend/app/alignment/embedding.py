from __future__ import annotations

import math

from ..ai.ollama import OllamaClient


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
    emb_prompt = await ollama.embed(prompt, model=model)
    emb_tags = await ollama.embed(tags_text, model=model)
    score = cosine_similarity(emb_prompt, emb_tags)
    return round(max(0.0, min(1.0, score)), 4)
