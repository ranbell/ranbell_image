"""**切るなら、切れ目で切る。**（2026-09-18）

総監督「prompt のオーバフローで文字が切れる場合があるようです」。

まず枠を数えた —— いちばん重い実機セッション（`1b78ac2b`・会話81行）を材料に、
段ごとの前置きを組み直して Ollama にトークンを数えさせた
（`private/muse/crew_lab/ctx_overflow.py`）:

    席 lens        2,111tok   出力に残る枠 14,273
    会議 beat（2席） 2,772tok   出力に残る枠 13,612
    台本係 writer   1,589tok   出力に残る枠 14,795
    主演 actress   5,719tok   出力に残る枠 10,665   ← いちばん長い前置きでも 35%

**枠（16,384）は溢れていない。** では何が切っていたか —— コードの中の
「固定の長さで切る」所だった。いちばん効くのは絵に渡す散文で、実測は
145〜831字に対して上限 900字（**92% まで来ている**）。超えた回は
**単語の途中で切れたまま** CLIP に渡る。

上限は変えない。**どこで切るか**だけ直す。あわせて、枠に当たって止まった回は
記録に残す（`done_reason: "length"`）—— 黙って短い返事にしない。
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
    """**生のスライスを残さない。** 上限は要るが、切り方は一つ。

    実機の保存を 18 の上限値で総当たりしたら、**上限ちょうどで止まっている
    文字列**が二種類あった（どちらも語の途中）:

        notebook.scene  800字  …the consoles and the concen   （`32cc5fab`・09-17）
        _recent_memories 900字  彼女の前置きに入る日記の抜粋

    どちらも絵のプロンプトには載らないが、**同じ壊れ方**なので一緒に直す。
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
