"""**A conversation-only turn does not rebuild the picture.** (2026-09-10)

The Showrunner: "when we are not shooting, a conversation-only reply should come
back faster".

Reading `stage_ms` live, a conversation-only move carried all of this:

    writer                 4.45s   writes the ledger (needed)
    quality_enrich         4.42s   ┐ inside `rebuild_craft`; both call a model
    prose_densify          6.93s   ┘
    actress               21.93s   she speaks (needed)
    assemble_after_propose 9.55s   she added an expression, so it builds again
    verify                 6.81s
    ───────────────────────────── ≈54s in total

The assembled prose and tags are used only by the draft and the final shoot, and
both call `rebuild_craft` themselves. **There is no reason to assemble
mid-conversation.**

What is checked here is only the shape of the calls (no model is touched).
"""
from __future__ import annotations

import inspect

from app.muse import assemble, service


def _code(fn) -> str:
    """Only the lines that actually run, with comments and strings removed.

    **So the test does not catch our own prose.** The text here mentions both
    `rebuild_craft` and `quality_enrich`, so searching a raw `inspect.getsource`
    always matches.
    """
    import io, tokenize

    out: list[str] = []
    src = inspect.getsource(fn)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


def test_chat_never_rebuilds_the_craft():
    """That no `rebuild_craft` remains anywhere inside `chat`."""
    code = _code(service.chat)
    assert "rebuild_craft" not in code
    assert "touch_craft" in code


def test_the_three_seams_all_use_the_cheap_one():
    """Before the actress, after the propose, after the self-repair — all three."""
    code = _code(service.chat)
    assert code.count("assemble . touch_craft ( session )") == 3


def test_taking_a_picture_still_rebuilds():
    """The draft and the final still rebuild before they queue."""
    for fn in (service.start_board, service.start_shoot):
        assert "rebuild_craft" in _code(fn), fn.__name__


def test_the_rebuild_button_still_rebuilds():
    """"Regenerate prompt" stays a real rebuild."""
    assert "rebuild_craft" in _code(service.rebuild)


def test_touch_craft_uses_no_model():
    """Pure functions only. It does not take `ollama` — it cannot, so it cannot
    call one."""
    params = list(inspect.signature(assemble.touch_craft).parameters)
    assert params == ["session"]
    code = _code(assemble.touch_craft)
    for forbidden in ("await", "generate_text", "quality_enrich", "densify"):
        assert forbidden not in code, forbidden


def test_touch_craft_moves_now_and_flags_stale():
    session = {
        "inputs": {"locale": "ja"},
        "refine_ledger": {"wearing": "white shirt", "scene": "rooftop"},
        "craft": {"prompt": "（前の回に組んだもの）", "stale": False},
    }
    assemble.touch_craft(session)
    craft = session["craft"]
    # 正本の一行とタグは動く
    assert craft["now"]
    assert "white_shirt" in craft["tags"] or "white shirt" in craft["tags"]
    # 組み上げたプロンプトはそのまま残る（消さない）
    assert craft["prompt"] == "（前の回に組んだもの）"
    # 古いという旗が立つ
    assert craft["stale"] is True


def test_rebuild_lowers_the_flag():
    """Rebuilt just before the shutter, it is no longer stale.

    It really rebuilds and the flag is read (`ollama=None`, so no model runs).
    """
    import asyncio

    session = {
        "inputs": {"locale": "ja"},
        "refine_ledger": {"wearing": "white shirt", "scene": "rooftop"},
        "craft": {},
    }
    assemble.touch_craft(session)
    assert session["craft"]["stale"] is True

    asyncio.run(assemble.rebuild_craft(None, None, session))
    assert session["craft"]["stale"] is False
    assert session["craft"]["prompt"]
