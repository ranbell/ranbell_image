"""セッションの一覧と、撮影ログへの道。

元は班の講評（`report.py`）の試験だったが、講評ごと
`private/muse_classic/` へ退いた（2026-09-12）。**残ったのはここの4本** ——
`session_db.list_recent` の並びとスタジオの仕切り、それに秘密の日記から
その日の会話ログへ辿る道。どちらも生きている。
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
    """**Muse Refine は同じコレクションに座っている（2026-09-07）。**

    絞らないと、classic の一覧から Refine のセッションが開けてしまう ——
    手帖を持たないセッションを classic の経路が読むことになる。席の成績
    レポートにも混ざって数字が薄まる。
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
    """秘密の日記から、その撮影の会話ログへ辿れること。

    日記は最初から `session_id` を持っていた（"so the entry can lead back to
    it"）のに、辿る道が無かった。
    """
    from app.characters import api as characters_api
    paths = [r.path for r in characters_api.router.routes]
    assert "/api/characters/{character_id}/diaries/{diary_id}/log" in paths


def test_machine_lines_are_kept_out_of_the_shoot_log():
    """タグ名の羅列は人が読むログに出さない（古いセッションにだけ残っている）。"""
    from app.characters.api import _MACHINE_LINE_RE
    assert _MACHINE_LINE_RE.match("（外しました: sitting、close-up。以降は書き戻されません）")
    assert _MACHINE_LINE_RE.match("（構成「間取り」が片付けました: concrete_blocks）")
    assert not _MACHINE_LINE_RE.match("全班入ります。スチールを見ながら詰めます。")
