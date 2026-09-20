"""**The opening theme has to reach the picture.** (2026-09-20)

The Showrunner: "Muse cannot handle the opening theme of a session — saying 'a
walk in the park' is not reflected."

Read back over every stored session that carried a theme (159), the ledger's
`scene` / `bg` / `beat` moved on the **first chat line**, never on the theme. In
most of those the first line was the theme typed a second time, which is exactly
what hid it. In the session he reported, he did not retype it — he said 「じゃあ
二人で構図を考えて」, the writer had no place to put it, only the two expressions
moved, and the turn came back marked 未反映.

`open_session` now hands the theme to the Scripter (`writer.write_patch`), which
is the one hand allowed to write the ledger. These tests walk the real
`open_session` with the model and the storage replaced.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.muse import assemble, persona, service, session_db, writer

THEME = "公園の遊歩道をおしゃべりしながら歩いて"
PARK = {"scene": "park walkway", "bg": "trees, gravel path", "beat": "walking, chatting"}


@pytest.fixture
def quiet(monkeypatch):
    """Silence the model and the storage. **`open_session` itself is untouched.**"""
    async def _save(db, session, **kw):
        return session

    async def _memory(db, session):
        return None

    async def _craft(db, ollama, session):
        session["craft"] = {"now": ""}
        return session

    async def _actress(ollama, **kw):
        _actress.seen = kw
        return {"say": "はい、総監督。", "aside": "", "propose": {},
                "my_feel": "", "card": "", "pitch": "", "blind": False}

    _actress.seen = {}
    monkeypatch.setattr(session_db, "save", _save)
    monkeypatch.setattr(persona, "load_memory", _memory)
    monkeypatch.setattr(assemble, "rebuild_craft", _craft)
    monkeypatch.setattr(writer, "actress_turn", _actress)
    return _actress


def _session(theme: str = THEME, **kw):
    s = service.new_session({"locale": "ja", "model": "m", "theme": theme})
    s["character"] = {
        "character_id": "c1", "name_ja": "二宮 かなで", "name": "Kanade",
        "wardrobe_sets": [{"key": "signature", "tags": ["sailor_uniform"], "props": []}],
    }
    s.update(kw)
    return s


def _writer(monkeypatch, patch, *, log=None):
    async def _patch(ollama, **kw):
        if log is not None:
            log.append(kw)
        return dict(patch)

    monkeypatch.setattr(writer, "write_patch", _patch)


@pytest.mark.asyncio
async def test_the_theme_lands_in_the_ledger(quiet, monkeypatch):
    """The defect itself: the place named at the door is in the ledger."""
    log: list[dict] = []
    _writer(monkeypatch, PARK, log=log)

    out = await service.open_session(None, object(), _session())

    assert out["refine_ledger"]["scene"] == "park walkway"
    assert out["refine_ledger"]["beat"] == "walking, chatting"
    assert [k["user_line"] for k in log] == [THEME], "お題そのものが Scripter に渡っていない"


@pytest.mark.asyncio
async def test_she_is_told_where_she_is_before_she_speaks(quiet, monkeypatch):
    """The opening line is written against the filled ledger, not a blank one."""
    _writer(monkeypatch, PARK)

    await service.open_session(None, object(), _session())

    assert quiet.seen["ledger"]["scene"] == "park walkway"


@pytest.mark.asyncio
async def test_a_theme_that_names_an_outfit_keeps_it(quiet, monkeypatch):
    """The Scripter runs **before** the signature wardrobe, which fills only
    what is still empty — so 「浴衣で」 is not overwritten by her usual clothes."""
    _writer(monkeypatch, {**PARK, "wearing": "yukata, geta"})

    out = await service.open_session(None, object(), _session("夏祭り、浴衣で"))

    assert out["refine_ledger"]["wearing"] == "yukata, geta"


@pytest.mark.asyncio
async def test_no_theme_asks_the_writer_nothing(quiet, monkeypatch):
    """A session opened without a theme costs no extra call, as before."""
    log: list[dict] = []
    _writer(monkeypatch, PARK, log=log)

    out = await service.open_session(None, object(), _session(""))

    assert log == []
    assert not out["refine_ledger"].get("scene")


@pytest.mark.asyncio
async def test_an_empty_answer_is_asked_again(quiet, monkeypatch):
    """**The theme always gets the second ask.** (measured, 2026-09-20)

    A chat turn only retries when `looks_like_picture_line` says the line looks
    like a direction. Of eight real themes it reads three as ordinary talk
    (「雨上がりの帰り道」「図書館で調べもの」「私たちの撮影スタジオが完成したよ！」)
    — and those are exactly the three where the writer answered a bare `{}` the
    first time, with the retry contract getting `scene` out of two of them.
    """
    calls: list[dict] = []

    async def _patch(ollama, **kw):
        calls.append(kw)
        return {"scene": "図書館"} if kw.get("retry") else {}

    monkeypatch.setattr(writer, "write_patch", _patch)

    out = await service.open_session(None, object(), _session("図書館で調べもの"))

    assert [bool(c.get("retry")) for c in calls] == [False, True]
    assert out["refine_ledger"]["scene"] == "図書館"


@pytest.mark.asyncio
async def test_a_theme_that_did_not_land_is_said_out_loud(quiet, monkeypatch):
    """**Never dropped in silence.** Both asks empty is the cue to say it
    again, so it appears in the conversation."""
    _writer(monkeypatch, {})

    out = await service.open_session(None, object(), _session())

    missed = [r for r in out["chat"] if (r.get("meta") or {}).get("kind") == "ledger_missed"]
    assert len(missed) == 1
    assert "ledger" in str(missed[0]["text"])


@pytest.mark.asyncio
async def test_a_broken_writer_does_not_stop_the_opening(quiet, monkeypatch):
    """She still has to greet him. The ledger stays as it was."""
    async def _boom(ollama, **kw):
        raise TypeError("write_patch() got an unexpected keyword")

    monkeypatch.setattr(writer, "write_patch", _boom)

    out = await service.open_session(None, object(), _session())

    assert out["opened"] is True
    assert any("総監督" in str(r.get("text") or "") for r in out["chat"])
