"""Decide the context length in one place. **So it matches the clerks'.**
(2026-09-10)

The Showrunner: "I think it is simply slow to infer — a Muse conversation turn
takes 20-30 sec". What it really was: the model being loaded again.

Ollama **reloads as a separate instance when the context length differs**.
Measured (26B, production):

    same length repeatedly    1st 21.8s (load 20.4s) → 2nd 0.2s (load 0.0s)
    alternating lengths       12.8s every time (load 11.3s)

Refine passed no `num_ctx` at all and took the default, while the clerks
(`muse.chain._call`) pass `ollama_num_ctx` (16384). Within a single turn:

    clerk 16384 → nsfw 16384 → abuse 16384 → writer default → actress default →
    verify default

so it alternated, carrying **at least two loads of 11-24 seconds**. Generation
itself was fine at 35-48 tok/s.

Uses the same formula as `muse.service._num_ctx` — **it is pointless unless the
number is identical**.
"""
from __future__ import annotations

from typing import Any


def refine_num_ctx(session: dict[str, Any] | None) -> int | None:
    """The context length this session uses, so it equals the clerks'."""
    inputs = dict((session or {}).get("inputs") or {})
    cfg = dict((session or {}).get("_runtime_cfg") or {})
    return int(inputs.get("num_ctx") or cfg.get("ollama_num_ctx") or 0) or None
