"""Options for every Weave LLM call.

Ollama defaults ``num_ctx`` to 2048. The Storywright prompt alone is ~6200
characters (roughly 2000–3100 tokens) and the story it must return is another
one to two thousand, so on the default window the front of the prompt — ROLE,
the hard rules, the output schema — is silently truncated away and the model
improvises. That produced both the "Expecting ',' delimiter" parse failures
and bundles with an empty world.setting and empty visible_change.

Every Weave call goes through here so no site can forget again.
"""
from __future__ import annotations

from typing import Any

# Room for the prompt and the answer, matching runtime_config's default and
# the "num_ctx ≥ 16384" note on OllamaGateway.chat_text.
WEAVE_NUM_CTX = 16384


def weave_options(
    options: dict[str, Any] | None = None,
    *,
    temperature: float = 0.7,
    num_ctx: int | None = None,
) -> dict[str, Any]:
    """Merge caller options over the Weave defaults, always with a context size."""
    out: dict[str, Any] = {"temperature": temperature}
    out.update(options or {})
    if not out.get("num_ctx"):
        out["num_ctx"] = int(num_ctx or WEAVE_NUM_CTX)
    return out


async def weave_options_from_config(
    db,
    options: dict[str, Any] | None = None,
    *,
    temperature: float = 0.7,
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
    return weave_options(options, temperature=temperature, num_ctx=configured)
