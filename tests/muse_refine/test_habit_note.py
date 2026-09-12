"""**癖メモが書かれるようにする。**（2026-09-10）

総監督「癖メモかけるようにしよう」。

スタジオ手帖の癖メモは classic の `finish_session` が出すが、二つの門がどちらも
`session["notes"]` を読む —— `lounge.should_write_habit`（材料が空なら即やめ）と、
書く側の `muse.service._director_highlights`（末尾8件を条文に入れる）。

**Refine は常設の指示を `standing` に入れていて `notes` を一度も埋めていなかった**
ので、Refine で撮った日は癖メモが一度も出ていなかった。
"""
from __future__ import annotations

import inspect

from app.muse import lounge as lounge_mod, shared as muse_service
from app.muse_refine import service


def test_a_picture_direction_is_kept():
    session: dict = {}
    service._keep_note(session, "窓際に立って、外を見て。")
    assert session["notes"] == ["窓際に立って、外を見て。"]


def test_the_same_line_twice_is_kept_once():
    """常設の指示は二度積めば二度効く。classic の `_add_note` と同じ。"""
    session: dict = {}
    service._keep_note(session, "座って。")
    service._keep_note(session, "座って。")
    assert session["notes"] == ["座って。"]


def test_notes_do_not_pile_up():
    session: dict = {}
    for i in range(60):
        service._keep_note(session, f"指示 {i}")
    assert len(session["notes"]) == service._NOTES_MAX
    assert session["notes"][-1] == "指示 59"


def test_only_picture_lines_are_kept():
    """「かわいいよ」は癖の材料にならない —— 台帳を動かした回だけ控える。"""
    src = inspect.getsource(service.chat)
    keep = src.index("_keep_note(session, text)")
    gate = src.rindex("if ledger_mod.touched_picture(patch):", 0, keep)
    assert keep - gate < 120, "台帳が動いた分岐の中に無い"


def test_both_gates_open_once_notes_exist():
    """材料さえあれば、classic 側の二つの門はどちらも通る。"""
    session = {"notes": ["窓際に立って、外を見て。", "カーディガンは脱いで。"]}
    # 門1: 材料が空だと即やめ（乱数の前）
    assert lounge_mod.should_write_habit(notes=[]) is False
    # 目が出る乱数を渡せば通ること（0.18 未満）
    class _R:
        @staticmethod
        def random(): return 0.01
    assert lounge_mod.should_write_habit(notes=session["notes"], rng=_R) is True
    # 門2: 書く側が条文に入れる材料
    got = muse_service._director_highlights(session)
    assert "窓際" in got and "カーディガン" in got


def test_the_wrap_says_whether_the_habit_can_happen():
    """総監督「これなかなか各タイミングが分かりにくい」——材料の有無を残す。"""
    src = inspect.getsource(service.finish_session)
    assert "wrap_handover" in src
    assert "habit_possible" in src
