"""What the log records, and what the diary is given — both measured short.

Three defects found by reading a real 73-line 主演撮り session off the server:

1. Eight test shots, eight timeline entries, and not one line in the chat —
   only 制作スタッフ had a seat to say it. Read back, the log showed four
   「承認を受け付けました」 and no sign a board had ever been asked for.
2. 「beat が書き取れませんでした」 was said 44 seconds before the fold pass
   wrote that very beat. The studio apologised for something the turn went on
   to get right.
3. Four ③ presses, four finished photos, one of them in her diary. `shoot` is
   one take and each press replaced the last.
"""
from __future__ import annotations

import asyncio

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import shared  # noqa: E402
from tests.muse.test_service import (  # noqa: E402
    FakeComfy, FakeDb, FakeSpooler,
)


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)




# ── 1. the test shot leaves a mark ──────────────────────────────────────────


# ── 2. every ③ reaches the diary ────────────────────────────────────────────

def test_every_take_of_the_day_reaches_the_diary():
    session = {
        "shoots": [
            {"prompt": "p1", "seed": 1, "images": [{"image_id": "aaa"}]},
            {"prompt": "p2", "seed": 2, "images": [{"image_id": "bbb"},
                                                   {"image_id": "ccc"}]},
        ],
        "shoot": {"prompt": "p3", "seed": 3, "images": [{"image_id": "ddd"}]},
    }
    assert shared.all_shoot_image_ids(session) == ["aaa", "bbb", "ccc", "ddd"]
    # The current take alone is still available — it is the diary's cover.
    assert shared._shoot_image_ids(session) == ["ddd"]


def test_older_sessions_stored_bare_sha_strings():
    session = {"shoots": [{"images": ["aaa"]}], "shoot": {"images": ["bbb", "aaa"]}}
    assert shared.all_shoot_image_ids(session) == ["aaa", "bbb"]


def test_a_session_with_one_take_is_unchanged():
    session = {"shoot": {"images": [{"image_id": "aaa"}]}}
    assert shared.all_shoot_image_ids(session) == ["aaa"]


# ── 2b. the photos are asked of the photos, not of the session ──────────────

class _PhotoDb:
    """Stands in for the image store: rows carry their own shoot id."""

    def __init__(self, rows, fail=False):
        self.rows = rows
        self.fail = fail
        self.kwargs: dict | None = None

    async def scroll_all(self, **kw):
        self.kwargs = kw
        if self.fail:
            raise RuntimeError("qdrant is having a day")
        return [
            r for r in self.rows
            if r.get("muse_session_id") == kw.get("muse_session_id")
        ]


def _row(sha, mtime, sid="s1"):
    return {"sha256": sha, "mtime": mtime, "muse_session_id": sid}


@pytest.mark.asyncio
async def test_a_shoot_that_predates_the_archive_still_finds_its_photos():
    """The measured case: four ③ presses, eight photos, a page holding two.

    Nothing archived those takes at the time, so the session cannot answer —
    but each photo stored its own `muse_session_id` and can.
    """
    db = _PhotoDb([
        _row("aaa", "2026-08-16T17:18:43Z"),
        _row("bbb", "2026-08-16T17:19:27Z"),
        _row("ccc", "2026-08-16T17:37:01Z"),
        _row("ddd", "2026-08-16T17:37:44Z"),
        _row("zzz", "2026-08-16T18:00:00Z", sid="other"),
    ])
    session = {  # only the last take survived on the session, as it used to
        "session_id": "s1",
        "shoot": {"images": [{"image_id": "ccc"}, {"image_id": "ddd"}]},
    }

    got = await shared.shoot_photos_of_session(db, session)

    assert got == ["aaa", "bbb", "ccc", "ddd"], "oldest press first"
    assert db.kwargs["muse_stage"] == "shoot", "board sketches are not the shoot"


@pytest.mark.asyncio
async def test_a_photo_the_image_store_has_not_seen_yet_is_not_dropped():
    db = _PhotoDb([_row("aaa", "2026-08-16T17:18:43Z")])
    session = {"session_id": "s1", "shoot": {"images": [{"image_id": "fresh"}]}}

    assert await shared.shoot_photos_of_session(db, session) == ["aaa", "fresh"]


@pytest.mark.asyncio
async def test_a_failed_lookup_falls_back_to_what_the_session_knows():
    db = _PhotoDb([], fail=True)
    session = {"session_id": "s1", "shoot": {"images": [{"image_id": "ccc"}]}}

    assert await shared.shoot_photos_of_session(db, session) == ["ccc"]


# ── 3. the notice waits for the whole turn ──────────────────────────────────







