"""Simple mode — tidy up the situation each turn and fix the previous prompt.

The Showrunner (2026-09-06): "I understand the reasoning behind guarding it with
functions, and the tests have produced results, but **if the director's instruction
does not reach the prompt directly, it is meaningless**".

What is checked here is not what the rewriter writes (that is measured live) but
only **the three things we decided to hold**:

    1. the identity line is copied through (a named hairstyle yields only the cut)
    2. negations and bans are dropped from the output
    3. no stage is walked (compile / the per-field seats / weave / the boxes)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain, shared  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)




def test_parse_reads_now_and_the_whole_prompt():
    now, prompt = chain.parse_simple_rewrite(
        "NOW: パーカーを脱いで、白シャツ一枚で立っている。\n"
        "PROMPT: 1girl, solo, Mio,\nMio: standing, white_shirt,\nindoors"
    )
    assert now.startswith("パーカーを脱いで")
    assert prompt.startswith("1girl, solo, Mio,")
    assert prompt.endswith("indoors")


def test_parse_gives_up_cleanly_when_the_shape_is_not_there():
    assert chain.parse_simple_rewrite("すみません、書けませんでした") == ("", "")


