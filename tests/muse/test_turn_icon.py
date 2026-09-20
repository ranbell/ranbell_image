"""**Did this turn only talk, or did it move the picture?** (2026-09-10)

The Showrunner: "make it clear from an icon whether it was conversation only or
image-prompt generation", "there is a lot of conversation, so information such as
picture updates need not be shown — it is visible in the log".

The backstage row (`ledger_change`) comes out of the chat, and one mark goes on
her line instead.
"""
from __future__ import annotations

import inspect

from app.muse import ledger as L, service


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
    """**Older rows are not touched.** A row with nothing stamped shows nothing."""
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
    """Mutters (banter) and proposals (pitch) are a continuation of the talk. The mark
    goes on the spoken line alone."""
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
    """`ledger_change` is not stacked into the chat. The record itself stays."""
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
    # It survives in the log (the debug record of rewrites)
    assert session.get("rewrite_log")


def test_a_missed_direction_still_speaks_up():
    """A turn that could not be applied does not fail silently — it is the cue to say it
    again."""
    src = inspect.getsource(service.chat)
    assert '"kind": "ledger_missed"' in src
