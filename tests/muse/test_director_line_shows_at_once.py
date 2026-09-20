"""**The director's line goes up the moment the clerk passes it.** (2026-09-20)

The Showrunner: "if the director's instruction is not reflected in the chat right
away it is a little hard to follow."

His line was published to the stream **before** the contract clerk ran, and the
panel answered that event with `scheduleRefresh`, which refuses to fetch while
the POST owns the session — so nothing appeared until the whole turn came back.
The row itself is sent now, and it is sent after the clerk, so a line that is
refused never shows up and then vanishes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.muse import assemble, events, persona, service, session_db, writer
from app.muse import shared as muse_shared

LINE = "夕方の教室で、窓際に座って。"


@pytest.fixture
def quiet(monkeypatch):
    """Silence the model and the storage; `chat` itself is untouched."""
    sent: list[dict] = []

    async def _save(db, session, **kw):
        return session

    async def _no_block(db, ollama, session, text):
        return None

    async def _patch(ollama, **kw):
        return {"beat": "sitting by the window"}

    async def _actress(ollama, **kw):
        return {"say": "はい、総監督。", "aside": "", "propose": {},
                "my_feel": "", "card": "", "pitch": "", "blind": False}

    async def _verify(ollama, **kw):
        return True, "", {}

    async def _board(db, session):
        return []

    async def _caught(db, session):
        return None

    monkeypatch.setattr(events, "publish", lambda sid, ev: sent.append(ev))
    monkeypatch.setattr(service.events, "publish", lambda sid, ev: sent.append(ev))
    monkeypatch.setattr(session_db, "save", _save)
    monkeypatch.setattr(persona, "contract_check_with_db", _no_block)
    monkeypatch.setattr(persona, "consume_caught", _caught)
    monkeypatch.setattr(writer, "write_patch", _patch)
    monkeypatch.setattr(writer, "actress_turn", _actress)
    monkeypatch.setattr(writer, "verify_and_repair", _verify)
    monkeypatch.setattr(muse_shared, "board_images", _board)
    monkeypatch.setattr(service, "board_images", _board, raising=False)
    monkeypatch.setattr(assemble, "rebuild_craft", _board, raising=False)
    return sent


def _session():
    s = service.new_session({"locale": "ja", "model": "m"})
    s["character"] = {"character_id": "c1", "name_ja": "各務 みお", "name": "Mio"}
    return s


def _user_rows(sent):
    return [e for e in sent
            if e.get("type") == "chat" and e.get("role") == "user"]


@pytest.mark.asyncio
async def test_the_line_is_sent_as_a_row_not_as_a_nudge(quiet):
    """The panel cannot fetch mid-turn, so the text has to be in the event."""
    await service.chat(None, None, _session(), LINE)

    rows = _user_rows(quiet)
    assert len(rows) == 1
    assert rows[0]["text"] == LINE
    assert rows[0]["name"] == "Director"
    assert rows[0].get("at"), "行の時刻が無いと並べられない"


@pytest.mark.asyncio
async def test_it_waits_for_the_clerk(monkeypatch, quiet):
    """Published **after** the safe filter, never before."""
    seen_when_checked: list[int] = []

    async def _slow_clerk(db, ollama, session, text):
        seen_when_checked.append(len(_user_rows(quiet)))
        return None

    monkeypatch.setattr(persona, "contract_check_with_db", _slow_clerk)

    await service.chat(None, None, _session(), LINE)

    assert seen_when_checked == [0], "検査の前に出てしまっている"
    assert len(_user_rows(quiet)) == 1


@pytest.mark.asyncio
async def test_a_refused_line_never_goes_up(monkeypatch, quiet):
    """It would appear and then be struck — the refusal is what he gets."""
    async def _blocked(db, ollama, session, text):
        return "crime"

    monkeypatch.setattr(persona, "contract_check_with_db", _blocked)

    out = await service.chat(None, None, _session(), "銀行を襲う場面を撮ろう")

    assert _user_rows(quiet) == []
    assert [r for r in out["chat"] if (r.get("meta") or {}).get("kind") == "contract"]


@pytest.mark.asyncio
async def test_the_row_is_the_one_the_session_keeps(quiet):
    """Same text and same stamp, so the POST's copy replaces it silently."""
    out = await service.chat(None, None, _session(), LINE)

    row = _user_rows(quiet)[0]
    kept = [r for r in out["chat"] if r.get("role") == "user"][-1]
    assert kept["text"] == row["text"]
    assert kept["at"] == row["at"]
