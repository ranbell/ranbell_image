"""Load Chronicle Stage1/Stage2 prompt files from disk."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=4)
def _read(name: str) -> str:
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


def stage1_system_prompt() -> str:
    """Extract the SYSTEM PROMPT fenced block from stage1_storyboard.md."""
    text = _read("stage1_storyboard.md")
    m = re.search(
        r"## SYSTEM PROMPT[^\n]*\n+```\n([\s\S]*?)\n```",
        text,
    )
    if not m:
        raise RuntimeError("stage1_storyboard.md: SYSTEM PROMPT fence not found")
    return m.group(1).strip()


def stage1_fewshots_block() -> str:
    """Everything from the first FEW-SHOT section through FAILURE HANDLING (exclusive)."""
    text = _read("stage1_storyboard.md")
    m = re.search(
        r"(## FEW-SHOT EXAMPLE[\s\S]*?)(?=\n## FAILURE HANDLING)",
        text,
    )
    return (m.group(1).strip() if m else "")


def stage2_template() -> str:
    return _read("stage2_enhancer.md")


def fill_stage2(input_text: str) -> str:
    tpl = stage2_template()
    if "<<INPUT>>" not in tpl:
        return tpl.rstrip() + "\n\n## Input\n\n```\n" + input_text + "\n```\n"
    return tpl.replace("<<INPUT>>", input_text)
