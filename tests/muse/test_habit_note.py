"""**Make the habit note get written.** (2026-09-10)

The Showrunner: "let us make the habit note writable".

The studio notebook's habit note is produced by classic's `finish_session`, but two
gates both read `session["notes"]` — `lounge.should_write_habit` (stop at once if
the material is empty) and, on the writing side,
`muse.service._director_highlights` (the last 8 go into the contract).

**Refine put standing orders into `standing` and never filled `notes` once**, so on
a day shot in Refine the habit note never appeared.
"""
from __future__ import annotations

import inspect

from app.muse import lounge as lounge_mod, shared as muse_service
from app.muse import service


def test_a_picture_direction_is_kept():
    session: dict = {}
    service._keep_note(session, "窓際に立って、外を見て。")
    assert session["notes"] == ["窓際に立って、外を見て。"]


def test_the_same_line_twice_is_kept_once():
    """Stack a standing order twice and it bites twice. The same as classic's
    `_add_note`."""
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
    """"You look lovely" is not material for a habit — only turns that moved the ledger
    are noted."""
    src = inspect.getsource(service.chat)
    keep = src.index("_keep_note(session, text)")
    gate = src.rindex("if ledger_mod.touched_picture(patch):", 0, keep)
    assert keep - gate < 120, "台帳が動いた分岐の中に無い"


def test_both_gates_open_once_notes_exist():
    """Given material, both of classic's gates open."""
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
    """The Showrunner: "the timing of each of these is quite hard to see" — so whether
    there was material is recorded."""
    src = inspect.getsource(service.finish_session)
    assert "wrap_handover" in src
    assert "habit_possible" in src
