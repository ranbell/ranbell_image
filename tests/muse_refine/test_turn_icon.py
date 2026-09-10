"""**この回は喋っただけか、画を動かしたか。**（2026-09-10）

総監督「会話オンリーか画像プロンプト生成かはアイコンで分かるように」
「会話部分の情報が多いので、画の更新などの情報は表示しなくていいかな。
ログで見えるので」。

裏方の行（`ledger_change`）を会話から落とし、代わりに彼女の台詞に印を一つ。
"""
from __future__ import annotations

import inspect

from app.muse_refine import ledger as L, service


def _say_row(text: str = "うん") -> dict:
    return {"role": "assistant", "name": "澪", "text": text, "meta": {"kind": "say"}}


def test_a_moved_ledger_marks_the_say_row():
    before = {**L.blank(), "beat": "standing"}
    session = {
        "refine_ledger": {**L.blank(), "beat": "sitting on the railing"},
        "chat": [
            {"role": "user", "name": "Director", "text": "座って"},
            _say_row(),
        ],
    }
    service._mark_turn_shot(session, mark=0, before=before)
    assert session["chat"][1]["meta"]["shot"] is True


def test_a_talk_only_turn_marks_it_false():
    before = {**L.blank(), "beat": "standing"}
    session = {
        "refine_ledger": {**L.blank(), "beat": "standing"},
        "chat": [
            {"role": "user", "name": "Director", "text": "今日はありがとう"},
            _say_row(),
        ],
    }
    service._mark_turn_shot(session, mark=0, before=before)
    assert session["chat"][1]["meta"]["shot"] is False


def test_older_rows_are_left_alone():
    """**古い行には触らない。** 押していない行は画面に何も出さない。"""
    before = {**L.blank(), "beat": "standing"}
    old = _say_row("前の回")
    session = {
        "refine_ledger": {**L.blank(), "beat": "sitting"},
        "chat": [old, {"role": "user", "text": "座って"}, _say_row("今の回")],
    }
    service._mark_turn_shot(session, mark=1, before=before)
    assert "shot" not in session["chat"][0]["meta"]
    assert session["chat"][2]["meta"]["shot"] is True


def test_only_the_spoken_line_gets_the_mark():
    """内心（banter）や提案（pitch）は喋りの続き。印は台詞に一つだけ。"""
    before = {**L.blank()}
    session = {
        "refine_ledger": {**L.blank(), "scene": "rooftop"},
        "chat": [
            _say_row(),
            {"role": "assistant", "text": "…", "meta": {"kind": "banter"}},
            {"role": "assistant", "text": "A ｜ B", "meta": {"kind": "pitch"}},
        ],
    }
    service._mark_turn_shot(session, mark=0, before=before)
    assert session["chat"][0]["meta"]["shot"] is True
    assert "shot" not in session["chat"][1]["meta"]
    assert "shot" not in session["chat"][2]["meta"]


def test_the_change_row_is_gone_from_chat():
    """`ledger_change` は会話に積まない。記録のほうは残す。"""
    session: dict = {"chat": [], "refine_ledger": {}, "rewrite_log": []}
    before = {**L.blank(), "wearing": "sailor uniform"}
    after = {**L.blank(), "wearing": "white shirt"}
    service._change_event(
        session, source="director", patch={"wearing": "white shirt"},
        before=before, after=after, locale="ja",
    )
    kinds = [(r.get("meta") or {}).get("kind") for r in session["chat"]]
    assert "ledger_change" not in kinds
    assert session["chat"] == []
    # ログ（デバッグの書き換え記録）には残っていること
    assert session.get("rewrite_log")


def test_a_missed_direction_still_speaks_up():
    """反映できなかった回は、黙って落とさない —— 言い直しの合図。"""
    src = inspect.getsource(service.chat)
    assert '"kind": "ledger_missed"' in src
