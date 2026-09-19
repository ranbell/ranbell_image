"""**On a turn where the picture did not move, the re-check does not run.**
(2026-09-10)

Verify's job is "does the ledger match the director's intent". On a turn where
not one field moved and the director's line does not look like picture talk
("thank you for today"), there is nothing to compare against. It still read
2,797 characters every time and wrote a line in her voice — about 4 seconds on
the input alone.

**No hole is opened.** A turn that looks like a picture instruction where the
writer wrote nothing (`missed`) is exactly the turn verify should catch, so it
runs.

**What is measured is only what the director moved (added 2026-09-10).**
Comparing the top of the turn against the ledger now counted a turn where she
added one expression word as "moved", and a 5.9-6.8 second re-check ran every
time. The thing to compare against is `after_director` — the point where the
director's patch has been applied.
"""
from __future__ import annotations

import inspect

from app.muse import service


def test_the_gate_reads_both_conditions():
    src = inspect.getsource(service.chat)
    gate = src[src.index("moved_by_director = "):src.index("ok, comment, repair")]
    # 台帳が動いていない **かつ** 取りこぼしでもない、の両方が要る
    assert "if not moved_by_director and not missed:" in gate
    assert "verify_skipped" in gate


def test_a_missed_picture_line_still_gets_checked():
    """`missed` is a turn that "looks like a picture instruction and the writer came
    back empty".

    Skip it and the only stage that catches the writer's misses is gone.
    """
    src = inspect.getsource(service.chat)
    assert "force_repair_hint=missed" in src
    gate = src[src.index("moved_by_director = "):src.index("ok, comment, repair")]
    assert "missed" in gate


def test_changed_fields_is_what_decides():
    """The decision is made by comparing the ledger before and after — not by
    guessing."""
    from app.muse import ledger as L

    before = {**L.blank(), "beat": "standing"}
    same = {**L.blank(), "beat": "standing"}
    moved = {**L.blank(), "beat": "sitting"}
    assert L.changed_fields(before, same) == []
    assert L.changed_fields(before, moved) == ["beat"]


def test_the_gate_measures_the_director_not_her():
    """The thing to compare against is "the point where the director's patch has been
    applied".

    Her propose is held to filling empty fields by `guard_muse_propose`, so it
    never overwrites the director's instruction. There is no reason to run a
    re-check for that one word.
    """
    src = inspect.getsource(service.chat)
    # 監督のパッチを当てた直後に控えていること（女優の段より前）
    assert "after_director = dict(led)" in src
    assert src.index("after_director = dict(led)") < src.index("actress = await writer.actress_turn")
    gate = src[src.index("moved_by_director = "):src.index("ok, comment, repair")]
    assert "changed_fields(before, after_director)" in gate
