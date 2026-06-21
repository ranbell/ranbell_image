from __future__ import annotations

from typing import Literal


def normalize_tag(text: str) -> str:
    return text.replace("_", " ").lower().strip()


def tokenize(text: str) -> set[str]:
    return {t for t in normalize_tag(text).split() if len(t) >= 3}


def detect_prompt_style(prompt: str) -> Literal["danbooru", "natural"]:
    commas = prompt.count(",")
    words = len(prompt.split())
    if commas >= 3 and words > 0 and commas / words > 0.1:
        return "danbooru"
    return "natural"


def extract_prompt_concepts(prompt: str) -> list[str]:
    parts = [p.strip() for p in prompt.split(",") if p.strip()]
    if len(parts) <= 1:
        return parts if parts else [prompt.strip()]
    return parts


def compute_bm25_match(
    prompt: str,
    tags: list[str],
) -> tuple[float, list[str], list[str]]:
    """BM25-style token overlap matching between prompt concepts and image tags.

    Returns (match_rate, matched_concepts, unmatched_concepts).
    Handles "blonde_hair" vs "blonde hair" via normalize_tag on both sides.
    """
    concepts = extract_prompt_concepts(prompt)
    if not concepts:
        return 0.0, [], []

    # Single pass: build (normalized_text, token_set) pairs; set for O(1) exact lookup.
    tag_data = [(normalize_tag(t), tokenize(t)) for t in tags]
    norm_tag_set = {norm_t for norm_t, _ in tag_data}

    matched: list[str] = []
    unmatched: list[str] = []

    for concept in concepts:
        norm_concept = normalize_tag(concept)
        concept_tokens = tokenize(concept)

        if not concept_tokens:
            continue

        found = norm_concept in norm_tag_set or any(
            concept_tokens <= tag_tokens
            or len(concept_tokens & tag_tokens) >= min(2, len(concept_tokens))
            for _, tag_tokens in tag_data
        )

        (matched if found else unmatched).append(concept)

    total = len(matched) + len(unmatched)
    rate = round(len(matched) / total, 4) if total > 0 else 0.0
    return rate, matched, unmatched
