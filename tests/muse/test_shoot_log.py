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

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook, service, session_db  # noqa: E402
from tests.muse.test_duet import TalkingOllama, _duet_session  # noqa: E402
from tests.muse.test_service import (  # noqa: E402
    FakeComfy, FakeDb, FakeSpooler,
)


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


def _system_lines(session) -> list[str]:
    return [
        str(m.get("text") or "") for m in (session.get("chat") or [])
        if m.get("kind") == "system"
    ]


# ── 1. the test shot leaves a mark ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_test_shot_is_recorded_in_the_lead_shoot_log():
    db, ollama, spooler = FakeDb(), TalkingOllama(), FakeSpooler()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)
    session = await service.post_duet_chat(db, ollama, session, "散らかった部屋で座って")

    before = len(session.get("chat") or [])
    session = await service.request_board(
        db, FakeComfy(), spooler, session, ollama=ollama,
    )

    assert len(session["chat"]) > before, "a press the log does not mention did not happen"
    assert any("試し撮り" in line for line in _system_lines(session))


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
    assert service.all_shoot_image_ids(session) == ["aaa", "bbb", "ccc", "ddd"]
    # The current take alone is still available — it is the diary's cover.
    assert service._shoot_image_ids(session) == ["ddd"]


def test_older_sessions_stored_bare_sha_strings():
    session = {"shoots": [{"images": ["aaa"]}], "shoot": {"images": ["bbb", "aaa"]}}
    assert service.all_shoot_image_ids(session) == ["aaa", "bbb"]


def test_a_session_with_one_take_is_unchanged():
    session = {"shoot": {"images": [{"image_id": "aaa"}]}}
    assert service.all_shoot_image_ids(session) == ["aaa"]


@pytest.mark.asyncio
async def test_a_second_final_press_keeps_the_first_photo():
    db, ollama, spooler = FakeDb(), TalkingOllama(), FakeSpooler()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)
    session = await service.post_duet_chat(db, ollama, session, "散らかった部屋で座って")
    session = await service.request_board(
        db, FakeComfy(), spooler, session, ollama=ollama,
    )
    session = await service.approve_and_shoot(
        db, FakeComfy(), spooler, session, ollama=ollama,
    )
    # The render lands.
    await session_db.attach_shoot_image(
        db, session["session_id"], "first_take_sha", {"seed": 1},
    )
    session = await session_db.load(db, session["session_id"])

    session = await service.approve_and_shoot(
        db, FakeComfy(), spooler, session, ollama=ollama,
    )
    await session_db.attach_shoot_image(
        db, session["session_id"], "second_take_sha", {"seed": 2},
    )
    session = await session_db.load(db, session["session_id"])

    assert service.all_shoot_image_ids(session) == [
        "first_take_sha", "second_take_sha",
    ]


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

    got = await service.shoot_photos_of_session(db, session)

    assert got == ["aaa", "bbb", "ccc", "ddd"], "oldest press first"
    assert db.kwargs["muse_stage"] == "shoot", "board sketches are not the shoot"


@pytest.mark.asyncio
async def test_a_photo_the_image_store_has_not_seen_yet_is_not_dropped():
    db = _PhotoDb([_row("aaa", "2026-08-16T17:18:43Z")])
    session = {"session_id": "s1", "shoot": {"images": [{"image_id": "fresh"}]}}

    assert await service.shoot_photos_of_session(db, session) == ["aaa", "fresh"]


@pytest.mark.asyncio
async def test_a_failed_lookup_falls_back_to_what_the_session_knows():
    db = _PhotoDb([], fail=True)
    session = {"session_id": "s1", "shoot": {"images": [{"image_id": "ccc"}]}}

    assert await service.shoot_photos_of_session(db, session) == ["ccc"]


# ── 3. the notice waits for the whole turn ──────────────────────────────────

def _parked(session, *, field: str, before: str) -> None:
    session["repair_notice"] = {"fields": [field], "before": {field: before}}


def test_the_notice_is_withdrawn_when_the_fold_writes_the_field():
    """The measured case: the fold wrote beat 44 seconds after the apology."""
    session = {"session_id": "s1", "inputs": {"locale": "ja"}}
    nb = notebook.of(session)
    notebook.apply_patch(nb, {"beat": "standing by the fence"})
    session["notebook"] = nb
    _parked(session, field="beat", before="standing by the fence")

    notebook.apply_patch(nb, {"beat": "standing by the fence, waving one hand"})

    service._settle_repair_notice(session)

    assert not _system_lines(session), "nothing failed, so nothing to apologise for"
    assert "repair_notice" not in session


def test_the_notice_is_recorded_when_the_field_really_did_not_move():
    """It goes to the panel, not the room.

    This used to interrupt with 「もう一度、そこだけ言ってもらえますか？」.
    Measured live, three of those went out in one run and two were fixed by the
    very next thing the system did — so it was breaking the room to ask for
    something that was not needed. The signal is what made this debuggable, so
    it stays; only the voice goes.
    """
    session = {"session_id": "s1", "inputs": {"locale": "ja"}}
    nb = notebook.of(session)
    notebook.apply_patch(nb, {"beat": "standing by the fence"})
    session["notebook"] = nb
    _parked(session, field="beat", before="standing by the fence")

    service._settle_repair_notice(session)

    assert not _system_lines(session), "no studio voice in the room"
    entry = (session.get("rewrite_log") or [])[-1]
    assert entry["source"] == "repair_missed"
    assert "beat" in (entry.get("changed") or {})
    assert "repair_notice" not in session


def test_only_the_fields_that_stayed_put_are_named():
    session = {"session_id": "s1", "inputs": {"locale": "ja"}}
    nb = notebook.of(session)
    notebook.apply_patch(nb, {"beat": "standing", "wearing": "sailor_fuku"})
    session["notebook"] = nb
    session["repair_notice"] = {
        "fields": ["beat", "wearing"],
        "before": {"beat": "standing", "wearing": "sailor_fuku"},
    }
    notebook.apply_patch(nb, {"wearing": "sailor_fuku, cardigan"})

    service._settle_repair_notice(session)

    changed = (session.get("rewrite_log") or [])[-1].get("changed") or {}
    assert "beat" in changed
    assert "wearing" not in changed


def test_settling_an_empty_notice_records_nothing():
    session = {"session_id": "s1", "inputs": {"locale": "ja"}}
    service._settle_repair_notice(session)
    assert not _system_lines(session)
    assert not session.get("rewrite_log")
