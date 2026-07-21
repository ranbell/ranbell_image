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
    """FEW-SHOT sections only (LLM-facing examples; no agent/integration notes)."""
    text = _read("stage1_storyboard.md")
    m = re.search(r"(## FEW-SHOT EXAMPLE[\s\S]*)\Z", text)
    block = m.group(1).strip() if m else ""
    # Safety: never include agent-side implementation sections if present.
    for stop in (
        "\n## FAILURE HANDLING",
        "\n## GENERATION PARAMETERS",
        "\n## STAGE 2",
        "\n## INTEGRATION NOTES",
    ):
        i = block.find(stop)
        if i >= 0:
            block = block[:i].rstrip()
    return block


def stage2_template() -> str:
    return _read("stage2_enhancer.md")


def fill_stage2(input_text: str) -> str:
    tpl = stage2_template()
    if "<<INPUT>>" not in tpl:
        return tpl.rstrip() + "\n\n## Input\n\n```\n" + input_text + "\n```\n"
    return tpl.replace("<<INPUT>>", input_text)


def clear_prompt_cache() -> None:
    _read.cache_clear()
