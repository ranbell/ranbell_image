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


def test_the_notice_is_said_when_the_field_really_did_not_move():
    session = {"session_id": "s1", "inputs": {"locale": "ja"}}
    nb = notebook.of(session)
    notebook.apply_patch(nb, {"beat": "standing by the fence"})
    session["notebook"] = nb
    _parked(session, field="beat", before="standing by the fence")

    service._settle_repair_notice(session)

    lines = _system_lines(session)
    assert lines and "beat" in lines[0] and "書き取れませんでした" in lines[0]
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

    lines = _system_lines(session)
    assert lines and "beat" in lines[0]
    assert "wearing" not in lines[0]


def test_settling_an_empty_notice_says_nothing():
    session = {"session_id": "s1", "inputs": {"locale": "ja"}}
    service._settle_repair_notice(session)
    assert not _system_lines(session)
