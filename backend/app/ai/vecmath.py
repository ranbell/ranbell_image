"""Embedding vector arithmetic, done client-side.

Qdrant takes any dense vector as a query, so "search for X but not Y" needs no
server-side feature — compose the vector here and hand it to the ordinary
search. Inspire has done this for a while (arithmetic, blend, outlier); Muse
uses the same trick to keep a background search from returning person tags.

Plain Python lists, no numpy: these run once per request over a few 768-float
vectors, and the list comprehensions are easier to read than the setup cost.
"""
from __future__ import annotations

import math


def normalize(vec: list[float]) -> list[float]:
    mag = math.sqrt(sum(x * x for x in vec))
    if mag == 0:
        return vec
    return [x / mag for x in vec]


def vec_add(a: list[float], b: list[float]) -> list[float]:
    return [x + y for x, y in zip(a, b)]


def vec_sub(a: list[float], b: list[float]) -> list[float]:
    return [x - y for x, y in zip(a, b)]


def vec_lerp(a: list[float], b: list[float], t: float) -> list[float]:
    return [x + t * (y - x) for x, y in zip(a, b)]


def vec_scale(vec: list[float], k: float) -> list[float]:
    return [x * k for x in vec]


def subtract_concept(
    base: list[float], concept: list[float], strength: float = 1.0,
) -> list[float]:
    """``base`` pushed away from ``concept``, renormalized.

    Both sides are normalized first so the result depends on direction only —
    otherwise a longer concept vector would dominate a shorter base one and the
    query would end up meaning mostly "not the concept" rather than "the base,
    away from the concept".

    ``strength`` 0 returns the base unchanged; 1.0 subtracts the whole unit
    concept. Above ~1.5 the result flips past orthogonal and starts meaning the
    opposite of the concept, which is rarely what a caller wants.
    """
    if not base or not concept or len(base) != len(concept) or strength <= 0:
        return normalize(base) if base else base
    return normalize(vec_sub(normalize(base), vec_scale(normalize(concept), strength)))
