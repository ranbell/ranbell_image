"""**Her lines are streamed.** (2026-09-10)

The Showrunner: "the conversation is not streamed, so the wait really is felt".

This stage measures 20.3 seconds, of which 18.1 is reading the prompt (16,272
characters = 5,424 tokens at 300 tok/s). The total does not change, but waiting in
silence for the end and seeing characters appear partway are different kinds of
waiting.

**Only what is inside `SAY:` may be streamed.** Neither field names nor `MY_FEEL:`
may reach the screen. Classic's `_say_only` does that job, and every Refine field
name is in its `_SAY_SHUT_RE`.
"""
from __future__ import annotations

import asyncio

from app.muse import shared as muse_service
from app.muse import ledger as L, writer


class _Ollama:
    """Counts which of `generate_text` and `generate_text_stream` was called."""

    def __init__(self, raw: str):
        self.raw = raw
        self.plain = 0
        self.streamed = 0

    async def generate_text(self, prompt, **kw):
        self.plain += 1
        return self.raw

    async def generate_text_stream(self, prompt, **kw):
        self.streamed += 1
        for ch in self.raw:
            yield {"type": "token", "text": ch}


_RAW = "SAY: おかえりなさい、総監督。\nASIDE: すこし緊張してる\nMY_FEEL: 期待\n"


def _turn(ollama, on_token=None):
    return asyncio.run(writer.actress_turn(
        ollama, model="m", locale="ja", name="澪", now="NOW",
        ledger=L.blank(), identity_blurb="", user_line="ただいま",
        director_tail="", on_token=on_token,
    ))


def test_without_a_listener_it_does_not_stream():
    o = _Ollama(_RAW)
    out = _turn(o)
    assert (o.plain, o.streamed) == (1, 0)
    assert "おかえりなさい" in out["say"]


def test_with_a_listener_it_streams_and_still_parses():
    o = _Ollama(_RAW)
    seen: list[str] = []
    out = _turn(o, on_token=seen.append)
    assert (o.plain, o.streamed) == (0, 1)
    # Streamed, and still split into fields as usual
    assert "おかえりなさい" in out["say"]
    assert "緊張" in out["aside"]
    assert seen


def test_only_the_spoken_part_reaches_the_screen():
    """Through `_say_only`, neither field names nor mutters leak."""
    o = _Ollama(_RAW)
    seen: list[str] = []
    _turn(o, on_token=muse_service._say_only(seen.append))
    live = "".join(seen)
    assert "おかえりなさい" in live
    for leak in ("SAY:", "ASIDE:", "MY_FEEL:", "緊張", "期待"):
        assert leak not in live, leak


def test_a_broken_listener_does_not_break_the_turn():
    """The shoot goes on even when the screen falls over."""
    o = _Ollama(_RAW)

    def _boom(_text):
        raise RuntimeError("SSE went away")

    out = _turn(o, on_token=_boom)
    assert "おかえりなさい" in out["say"]
