"""Giving diary pages back the photos they were never handed.

Measured on the live studio: one session pressed ③ four times, rendered eight
final photos, and its diary page carries two. The session document only ever
held the take being made at that moment, so the earlier three were overwritten
before anything read them.

The photos were never lost. Each one stores `muse_session_id` in its own
payload, so the shoot can be asked of the images and the page repaired — which
is the only way to fix a page that was written months before the session
learned to keep its takes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.characters import presets as presets_db  # noqa: E402


class _ImageDb:
    """Only the one query the backfill makes."""

    def __init__(self, rows):
        self.rows = rows

    async def scroll_all(self, **kw):
        return [
            r for r in self.rows
            if r.get("muse_session_id") == kw.get("muse_session_id")
        ]


def _row(sha, mtime, sid):
    return {"sha256": sha, "mtime": mtime, "muse_session_id": sid}


@pytest.fixture
def studio(monkeypatch):
    """One character, whose pages and writes the test can inspect."""
    state = {
        "preset": {
            "id": "mio",
            "diaries": [
                {"id": "d1", "session_id": "s1",
                 "image_id": "ccc", "image_ids": ["ccc", "ddd"]},
                {"id": "d2", "session_id": "s2", "image_ids": ["eee"]},
                {"id": "d3", "image_ids": ["fff"]},   # no session — untouchable
            ],
        },
        "written": [],
    }

    async def _list(db, **kw):
        return [{"id": "mio", "name": "Mio"}]

    async def _get(db, pid):
        return state["preset"] if pid == "mio" else None

    async def _update(db, pid, patch):
        state["written"].append((pid, patch))
        state["preset"].update(patch)

    monkeypatch.setattr(presets_db, "list_presets", _list)
    monkeypatch.setattr(presets_db, "get_preset", _get)
    monkeypatch.setattr(presets_db, "update_preset", _update)
    return state


def _page(state, diary_id):
    return next(d for d in state["preset"]["diaries"] if d["id"] == diary_id)


@pytest.mark.asyncio
async def test_the_earlier_takes_come_back(studio):
    db = _ImageDb([
        _row("aaa", "2026-08-16T17:18:43Z", "s1"),
        _row("bbb", "2026-08-16T17:19:27Z", "s1"),
        _row("ccc", "2026-08-16T17:37:01Z", "s1"),
        _row("ddd", "2026-08-16T17:37:44Z", "s1"),
    ])

    result = await presets_db.backfill_diary_photos(db)

    assert _page(studio, "d1")["image_ids"] == ["aaa", "bbb", "ccc", "ddd"]
    assert result["photos_added"] == 2
    assert result["repaired"] == 1


@pytest.mark.asyncio
async def test_the_cover_of_a_repaired_page_does_not_move(studio):
    """She has been looking at that photo at the top of the page."""
    db = _ImageDb([
        _row("aaa", "2026-08-16T17:18:43Z", "s1"),
        _row("ccc", "2026-08-16T17:37:01Z", "s1"),
        _row("ddd", "2026-08-16T17:37:44Z", "s1"),
    ])

    await presets_db.backfill_diary_photos(db)

    assert _page(studio, "d1")["image_id"] == "ccc"


@pytest.mark.asyncio
async def test_a_page_whose_shoot_has_no_photos_keeps_what_it_has(studio):
    """Nothing found is not a reason to empty a page."""
    db = _ImageDb([])

    result = await presets_db.backfill_diary_photos(db)

    assert _page(studio, "d1")["image_ids"] == ["ccc", "ddd"]
    assert _page(studio, "d2")["image_ids"] == ["eee"]
    assert result["repaired"] == 0
    assert studio["written"] == [], "an unchanged page must not be rewritten"


@pytest.mark.asyncio
async def test_a_page_with_no_session_is_left_alone(studio):
    db = _ImageDb([_row("aaa", "2026-08-16T17:18:43Z", "s1")])

    result = await presets_db.backfill_diary_photos(db)

    assert _page(studio, "d3")["image_ids"] == ["fff"]
    assert result["scanned"] == 2, "only the pages that name a shoot are scanned"


@pytest.mark.asyncio
async def test_running_it_twice_changes_nothing_the_second_time(studio):
    db = _ImageDb([
        _row("aaa", "2026-08-16T17:18:43Z", "s1"),
        _row("ccc", "2026-08-16T17:37:01Z", "s1"),
        _row("ddd", "2026-08-16T17:37:44Z", "s1"),
    ])

    await presets_db.backfill_diary_photos(db)
    writes = len(studio["written"])
    again = await presets_db.backfill_diary_photos(db)

    assert again["repaired"] == 0
    assert len(studio["written"]) == writes


@pytest.mark.asyncio
async def test_a_very_long_shoot_is_capped(studio):
    db = _ImageDb([
        _row(f"x{i:03d}", f"2026-08-16T17:{i:02d}:00Z", "s1") for i in range(40)
    ])

    await presets_db.backfill_diary_photos(db, limit=24)

    assert len(_page(studio, "d1")["image_ids"]) == 24
