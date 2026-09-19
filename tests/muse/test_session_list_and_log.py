"""Listing sessions, and the road to the shoot log.

This was originally the test for the crew's review (`report.py`), but the review
and all retired into `private/muse_classic/` (2026-09-12). **What remains is these
four** — `session_db.list_recent`'s ordering and the partition between studios,
plus the road from the secret diary to that day's conversation log. Both are
alive.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest


# ── 一覧が読むもの ──────────────────────────────────────────────────────────
class _ScrollDb:
    """Qdrant's scroll has no ordering and pages via an offset cursor."""

    def __init__(self, rows):
        self.rows = rows
        self._qc = self
        self.pages = 0

    async def scroll(self, collection_name, limit, offset=None, with_payload=True):
        class _P:
            def __init__(self, payload):
                self.payload = payload
                self.id = payload["session_id"]
        start = int(offset or 0)
        page = self.rows[start:start + limit]
        self.pages += 1
        nxt = start + limit if start + limit < len(self.rows) else None
        return [_P(r) for r in page], nxt


@pytest.mark.asyncio
async def test_recent_sessions_are_the_newest_not_an_arbitrary_handful():
    """Scroll returns points in no particular order, so asking it for `limit`
    and sorting those gave five sessions picked at random. A report over "the
    last five sessions" built on that is worse than useless."""
    from app.muse import session_db

    rows = [
        {"session_id": f"s{i}", "status": "done", "created_at": float(i),
         "inputs": {"theme": f"t{i}"}}
        for i in range(600)
    ]
    # Shuffled the way an unordered scan would hand them back.
    rows = rows[300:] + rows[:300]
    db = _ScrollDb(rows)

    out = await session_db.list_recent(db, limit=5)

    assert [r["session_id"] for r in out] == ["s599", "s598", "s597", "s596", "s595"]
    assert db.pages > 1, "must page through the whole collection, not one window"


@pytest.mark.asyncio
async def test_the_two_studios_do_not_show_up_in_each_others_lists():
    """**Muse Refine sits in the same collection (2026-09-07).**

    Without a filter, a Refine session can be opened from classic's list — a
    session with no notebook read by classic's path. It would also mix into the
    seat scorecards and dilute the numbers.
    """
    from app.muse import session_db

    db = _ScrollDb([
        {"session_id": "m1", "status": "chat", "created_at": 2.0,
         "inputs": {"theme": "classic"}},
        {"session_id": "r1", "status": "chat", "created_at": 1.0,
         "inputs": {"theme": "refine"}, "studio": "muse_refine"},
    ])

    classic = await session_db.list_recent(db, limit=10, studio="")
    assert [r["session_id"] for r in classic] == ["m1"]

    refine = await session_db.list_recent(db, limit=10, studio="muse_refine")
    assert [r["session_id"] for r in refine] == ["r1"]

    # 既定は全部 —— 呼び元がスタジオを言わないうちは、これまでと同じ。
    both = await session_db.list_recent(db, limit=10)
    assert [r["session_id"] for r in both] == ["m1", "r1"]


def test_diary_shoot_log_route_exists():
    """The secret diary can lead back to that shoot's conversation log.

    The diary had carried `session_id` from the start ("so the entry can lead back
    to it") and there was no road to follow it.
    """
    from app.characters import api as characters_api
    paths = [r.path for r in characters_api.router.routes]
    assert "/api/characters/{character_id}/diaries/{diary_id}/log" in paths


def test_machine_lines_are_kept_out_of_the_shoot_log():
    """Runs of tag names stay out of the log a person reads (they survive only in old
    sessions)."""
    from app.characters.api import _MACHINE_LINE_RE
    assert _MACHINE_LINE_RE.match("（外しました: sitting、close-up。以降は書き戻されません）")
    assert _MACHINE_LINE_RE.match("（構成「間取り」が片付けました: concrete_blocks）")
    assert not _MACHINE_LINE_RE.match("全班入ります。スチールを見ながら詰めます。")
