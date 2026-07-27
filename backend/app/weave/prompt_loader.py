"""Load Weave prompt markdown files."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    path = _DIR / name
    return path.read_text(encoding="utf-8")


def clear_cache() -> None:
    load_prompt.cache_clear()
