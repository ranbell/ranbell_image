"""**If it has to be cut, cut it at a boundary.** (2026-09-18)

The Showrunner: "it looks like text gets cut off by prompt overflow".

The window was counted first — taking the heaviest live session (`1b78ac2b`, 81
chat rows), each stage's preamble was rebuilt and Ollama itself counted the tokens
(`private/muse/crew_lab/ctx_overflow.py`):

    seat lens          2,111 tok   window left for output 14,273
    corner beat (2)    2,772 tok   window left for output 13,612
    writer             1,589 tok   window left for output 14,795
    actress            5,719 tok   window left for output 10,665   ← 35% at the
                                                                     longest

**The window (16,384) is not overflowing.** So what was cutting? The fixed-length
slices in the code. The one that bites is the prose handed to the picture:
measured at 145-831 characters against a cap of 900 (**92% of the way there**).
A turn that exceeds it reaches CLIP **cut in the middle of a word**.

The cap does not change. Only **where the cut falls** does. And a turn stopped by
the window is recorded (`done_reason: "length"`) — never left to pass as a short
reply.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.muse import assemble, crew_room as C


# ── 絵に渡す散文 ────────────────────────────────────────────────────────

def test_a_long_paragraph_stops_at_a_full_stop():
    long = "She stands at the counter in a heavy knit cardigan. " * 20
    out = assemble._trim_to_a_sentence(long, assemble.PROSE_MAX)
    assert len(out) <= assemble.PROSE_MAX
    assert out.endswith("."), out[-40:]


def test_a_paragraph_under_the_cap_is_untouched():
    assert assemble._trim_to_a_sentence("Short one.", 900) == "Short one."


def test_without_a_full_stop_it_still_does_not_break_a_word():
    long = "word " * 400
    out = assemble._trim_to_a_sentence(long, 900)
    assert len(out) <= 900
    assert out.endswith("word"), out[-20:]


def test_the_prose_path_uses_the_sentence_trim():
    src = inspect.getsource(assemble.densify_scene_prose)
    assert "_trim_to_a_sentence(text, PROSE_MAX)" in src
    assert "text[:900]" not in src


# ── 席の CRAFT 行 ───────────────────────────────────────────────────────

def test_a_long_craft_line_is_cut_between_tags():
    clause = ", ".join(f"tag_number_{i}" for i in range(40))
    out = C._clip_craft(clause)
    assert len(out) <= C.CRAFT_MAX
    # **半分の語を台帳に入れない**
    assert all(t.strip().startswith("tag_number_") and t.strip()[11:].isdigit()
               for t in out.split(",") if t.strip()), out[-40:]


def test_a_short_craft_line_is_untouched():
    assert C._clip_craft("rim_light, backlighting | 逆光") == "rim_light, backlighting | 逆光"


# ── 枠で切られたら、記録に残す ──────────────────────────────────────────

def test_the_client_can_report_how_the_generation_ended():
    from app.ai import ollama as O

    src = inspect.getsource(O.OllamaClient._generate_stream)
    assert "done_reason" in src, "終わり方を読んでいない"
    assert '"type": "done"' in src
    # 既定では流さない（Inspire と job runner はイベントを画面へ転送している）
    assert "with_done: bool = False" in inspect.getsource(O.OllamaClient)


def test_the_seats_and_the_lead_watch_the_window():
    from app.muse import writer

    assert "on_done=_watch_the_window(" in inspect.getsource(C._seat_turn)
    assert "on_done=_watch_the_window(" in inspect.getsource(C._group_turn)
    assert "_watch_the_window(sess" in inspect.getsource(writer.actress_turn)
    for mod in (C, writer):
        assert "cut_by_the_window" in inspect.getsource(mod._watch_the_window)


# ── 切り方は一本（2026-09-18・総監督「文字数制限があるかどうか調べて」）──────

def test_every_long_cut_goes_through_the_same_door():
    """**Leave no raw slices.** The caps are needed; there is one way to cut.

    Sweeping the live store against 18 cap values found **strings stopping exactly
    at a cap**, of two kinds (both mid-word):

        notebook.scene        800 chars  …the consoles and the concen
                                         (`32cc5fab`, 09-17)
        _recent_diary_bodies  900 chars  the diary excerpt in her preamble

    Neither reaches the picture prompt, but **it is the same break**, so both are
    fixed together.
    """
    import inspect

    from app.muse import notebook, shared

    src = inspect.getsource(notebook.migrate)
    assert "digest[:800]" not in src and "digest[:400]" not in src
    assert "_cap_phrase(digest, max_chars=800)" in src

    mem = inspect.getsource(shared._recent_diary_bodies)
    assert "text[:900]" not in mem
    assert "trim_to_a_sentence(text, 900)" in mem


def test_the_trim_lives_in_one_place():
    from app.muse import assemble as A
    from app.muse import identity

    assert A._trim_to_a_sentence("x" * 2000, 900) == identity.trim_to_a_sentence("x" * 2000, 900)
    assert identity.trim_to_a_sentence("短い文。", 900) == "短い文。"
    # 日本語の句点でも止まる
    long_ja = "彼女はカウンターに立っている。" * 100
    out = identity.trim_to_a_sentence(long_ja, 900)
    assert len(out) <= 900 and out.endswith("。")
