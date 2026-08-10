"""StreamParser: <think> splitting, and repair of llama.cpp byte-fallback
tokens (`<0xNN>`) that leak through in place of multi-byte UTF-8 characters.

Root cause and repair approach: https://note.com/zephel01/n/ne3cd50457fc6 —
Ollama moved to a llama.cpp-backed engine whose detokenizer emits this hex
notation when it cannot assemble a character. The real fix is upstream (an
Ollama/engine version); this is a best-effort client-side repair.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.ai.ollama import StreamParser


def _drain(parser: StreamParser, chunks: list[str]) -> str:
    out = []
    for chunk in chunks:
        for event in parser.feed(chunk):
            out.append(event["text"])
    for event in parser.flush():
        out.append(event["text"])
    return "".join(out)


def test_a_byte_fallback_run_split_across_chunks_is_reassembled():
    """The reported failure: a full-width space arrives as three separate
    byte tokens, split across two stream chunks mid-run."""
    out = _drain(StreamParser(), [
        "見ちゃいました？<0xE3><0x80",
        "><0x80>それより総監督",
    ])
    assert out == "見ちゃいました？　それより総監督"


def test_a_byte_fallback_run_in_one_chunk_is_reassembled():
    out = _drain(StreamParser(), ["残酷な蹂<0xE8><0xBA><0x99>だった"])
    assert out == "残酷な蹂躙だった"


def test_a_lone_continuation_byte_is_left_exactly_as_it_arrived():
    """Not valid UTF-8 on its own — repairing it would mean inventing a
    character, so the raw token stays rather than being guessed at."""
    out = _drain(StreamParser(), ["oops <0x80> alone"])
    assert out == "oops <0x80> alone"


def test_think_tag_splitting_still_works_alongside_byte_tokens():
    parser = StreamParser()
    events = parser.feed("<think>考え中<0xE3><0x80><0x80></think>SAY: 結果")
    events += parser.flush()
    kinds = {e["type"]: e["text"] for e in events}
    assert kinds["think"] == "考え中　"
    assert kinds["token"] == "SAY: 結果"


def test_a_token_that_never_closes_is_still_flushed_without_crashing():
    """End of stream mid-token — nothing more is coming, so flush() must
    hand back whatever it has rather than losing it silently."""
    out = _drain(StreamParser(), ["最後に<0xE3"])
    assert out == "最後に<0xE3"


def test_ordinary_text_with_no_byte_tokens_is_unaffected():
    out = _drain(StreamParser(), ["SAY: 今日は放送室で撮影しました。"])
    assert out == "SAY: 今日は放送室で撮影しました。"
