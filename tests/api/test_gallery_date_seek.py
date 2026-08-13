"""date_seek picks where to start; the cursor carries the scroll from there.

The timeline slider stays set after a jump, so the client keeps sending
date_seek on every page. Rebuilding the cursor from it each time pins the
scroll to the seek point — "next page" then serves the first page forever.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.api.images import list_images


class _RecordingDB:
    """Captures the cursor list_images() decided to scroll with."""

    def __init__(self):
        self.seen_cursors: list[str | None] = []

    async def scroll_images(self, *, cursor=None, limit=100, sort="newest",
                            exclude_drafts=True):
        self.seen_cursors.append(cursor)
        return [], None

    async def total_count(self, *, exclude_drafts=True):
        return 0


def _request(db):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))


async def _list(db, **kwargs):
    return await list_images(_request(db), **kwargs)


@pytest.mark.asyncio
async def test_date_seek_builds_the_opening_cursor():
    db = _RecordingDB()

    await _list(db, date_seek="2026-07-01T00:00:00+00:00")

    assert db.seen_cursors[0], "date_seek should have seeded a cursor"


@pytest.mark.asyncio
async def test_date_seek_does_not_override_a_live_cursor():
    """The continuation cursor must survive a still-set slider."""
    db = _RecordingDB()
    await _list(db, date_seek="2026-07-01T00:00:00+00:00")
    opening = db.seen_cursors[0]

    resumed = "cursor-from-page-1"
    await _list(db, cursor=resumed, date_seek="2026-07-01T00:00:00+00:00")

    assert db.seen_cursors[1] == resumed
    assert db.seen_cursors[1] != opening


@pytest.mark.asyncio
async def test_date_seek_is_ignored_for_non_mtime_sorts():
    db = _RecordingDB()

    await _list(db, sort="size_desc", date_seek="2026-07-01T00:00:00+00:00")

    assert db.seen_cursors[0] is None


@pytest.mark.asyncio
async def test_malformed_date_seek_does_not_seed_a_cursor():
    db = _RecordingDB()

    await _list(db, date_seek="last tuesday")

    assert db.seen_cursors[0] is None
