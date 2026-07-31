"""Sampling options for feature LLM calls.

``num_ctx``: Ollama defaults it to 2048. A prompt carrying a tag briefing plus
the JSON it must return overflows that easily, and Ollama drops the *front* of
the context on overflow — the role, the rules, the output schema. The model
then answers from the tail of the input alone, which yields unparseable JSON
and half-empty objects. So always send a window large enough for our own prompt.

Sampling: sending a hardcoded ``temperature`` with no ``top_k`` / ``top_p``
overrides whatever the model was tuned for. Gemma is documented to want
temperature 1.0 with top_k 64 and top_p 0.95. For a model family we know, its
published sampling is the *default*; for one we do not, send no sampling
parameters at all so the model's own modelfile defaults apply. Either way an
explicit caller/session temperature wins, so it stays adjustable from the UI
alongside the model picker.
"""
from __future__ import annotations

import re
from typing import Any

# Long creative calls get the bigger window.
STORY_NUM_CTX = 32768
DEFAULT_NUM_CTX = 16384

# Published per-family sampling. Keys are matched against the model name.
_FAMILY_SAMPLING: tuple[tuple[re.Pattern[str], dict[str, Any]], ...] = (
    # Gemma 2 / 3 / 3n / 4: Google documents temperature 1.0, top_k 64, top_p 0.95.
    (re.compile(r"gemma", re.I), {"temperature": 1.0, "top_k": 64, "top_p": 0.95}),
)


def family_sampling(model: str) -> dict[str, Any]:
    """Recommended sampling for this model, or {} when we have no basis to guess."""
    name = str(model or "")
    for pattern, params in _FAMILY_SAMPLING:
        if pattern.search(name):
            return dict(params)
    return {}


def default_temperature(model: str) -> float | None:
    """What the UI should pre-fill for this model (None → the model's own)."""
    return family_sampling(model).get("temperature")


def llm_options(
    options: dict[str, Any] | None = None,
    *,
    model: str = "",
    temperature: float | None = None,
    num_ctx: int | None = None,
    story: bool = False,
) -> dict[str, Any]:
    """Family sampling → session temperature → caller options → context window."""
    out: dict[str, Any] = family_sampling(model)
    if temperature is not None:
        out["temperature"] = float(temperature)
    for key, value in (options or {}).items():
        if value is not None:
            out[key] = value
    if not out.get("num_ctx"):
        default = STORY_NUM_CTX if story else DEFAULT_NUM_CTX
        out["num_ctx"] = max(int(num_ctx or default), default if story else 1)
    return out


async def llm_options_from_config(
    db,
    options: dict[str, Any] | None = None,
    *,
    model: str = "",
    temperature: float | None = None,
    story: bool = False,
) -> dict[str, Any]:
    """Same, but honour the admin-configured ``ollama_num_ctx`` when reachable."""
    configured = None
    if db is not None:
        try:
            from ..runtime_config import get_runtime_config

            cfg = await get_runtime_config(db)
            configured = int(cfg.get("ollama_num_ctx") or 0) or None
        except Exception:
            configured = None
    return llm_options(
        options,
        model=model,
        temperature=temperature,
        num_ctx=configured,
        story=story,
    )
