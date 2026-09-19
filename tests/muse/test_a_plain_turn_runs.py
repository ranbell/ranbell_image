"""**A turn with no crew must reach the end too.** (2026-09-18)

A live log from the Showrunner:

    File "/app/app/muse/service.py", line 945, in chat
        if floor:
    UnboundLocalError: cannot access local variable 'floor'

`floor` was only created inside `if crew_room.has_crew(session):`, so **a solo or
duet turn 500'd on every conversation** (for four days, from `0019b5b` on
2026-09-14). There are 39 tests around the seats and **not one that walks
`service.chat`** — which is why nobody noticed.

Here only the model and the storage are replaced, and **a whole turn really
runs**. This test watches that it reaches the end, not how good the output is, so
it does not reach into the details.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.muse import assemble, crew_room as C, persona, service, session_db
from app.muse import shared as muse_shared
from app.muse import writer


@pytest.fixture
def quiet(monkeypatch):
    """Silence the model and the storage. **`chat` itself is untouched.**"""
    async def _save(db, session, **kw):
        return session

    async def _no_block(db, ollama, session, text):
        return None

    async def _patch(ollama, **kw):
        return {"beat": "standing"}

    async def _actress(ollama, **kw):
        return {"say": "はい、総監督。", "aside": "……どきどき。", "propose": {},
                "my_feel": "", "card": "", "pitch": "", "blind": False}

    async def _verify(ollama, **kw):
        return True, "", {}

    async def _board(db, session):
        return []

    async def _caught(db, session):
        return None

    monkeypatch.setattr(session_db, "save", _save)
    monkeypatch.setattr(persona, "contract_check_with_db", _no_block)
    monkeypatch.setattr(persona, "consume_caught", _caught)
    monkeypatch.setattr(writer, "write_patch", _patch)
    monkeypatch.setattr(writer, "actress_turn", _actress)
    monkeypatch.setattr(writer, "verify_and_repair", _verify)
    monkeypatch.setattr(muse_shared, "board_images", _board)
    monkeypatch.setattr(service, "board_images", _board, raising=False)
    monkeypatch.setattr(assemble, "rebuild_craft", _board, raising=False)
    return monkeypatch


def _session(**kw):
    s = service.new_session({"locale": "ja", "model": "m"})
    s["character"] = {"character_id": "c1", "name_ja": "各務 みお", "name": "Mio"}
    s.update(kw)
    return s


@pytest.mark.asyncio
async def test_a_solo_turn_reaches_the_end(quiet):
    """**Solo shoot.** With no crew, `floor` stays empty and the turn still
    passes."""
    session = _session()
    out = await service.chat(None, None, session, "夕方の教室で、窓際に座って。")
    assert out is session
    said = [r for r in out["chat"] if (r.get("meta") or {}).get("kind") in (None, "say")]
    assert any("総監督" in str(r.get("text") or "") for r in said)
    # 班の跡が付いていないこと
    assert not [r for r in out["chat"] if (r.get("meta") or {}).get("kind") == "seat"]
    assert not out.get(C.CREW_WORDS)


@pytest.mark.asyncio
async def test_a_duet_turn_reaches_the_end(quiet):
    """**Duet.** No crew here either."""
    session = _session(partner_character={"character_id": "c2", "name_ja": "都築 あかり"})
    out = await service.chat(None, None, session, "二人で並んで、こっちを見て。")
    assert out["refine_ledger"]["beat"] == "standing"


@pytest.mark.asyncio
async def test_a_studio_turn_reaches_the_end(quiet, monkeypatch):
    """**Studio shoot.** A turn where the seats speak takes the same road."""
    async def _table(db, ollama, session, *, director_line, opening=False):
        return [{"muse_id": "gaffer:gyakkou", "role": "gaffer", "name": "逆光（照明）",
                 "field": "light", "say": "逆光で行くよ。",
                 "craft": "rim_light | 輪郭", "kind": "seat"}]

    monkeypatch.setattr(C, "run_table", _table)
    session = _session()
    session["inputs"] = {**session["inputs"], "crew_preset": "standard"}
    session[C.TABLE_OPEN] = True

    out = await service.chat(None, None, session, "光を硬くして。")
    assert out["refine_ledger"]["light"] == "rim_light", "席の結論が着地していない"
    assert out[C.CREW_WORDS]["light"] == ["rim_light"], "誰の語かの控えが残っていない"
    assert [r for r in out["chat"] if (r.get("meta") or {}).get("kind") == "seat"]


@pytest.mark.asyncio
async def test_a_broken_call_leaves_a_trace_instead_of_just_dots(monkeypatch):
    """**Do not let it end at 「……」.** (2026-09-18)

    `actress_turn` catches the exception and returns a placeholder (「……」), which
    keeps the turn from dying and **turns a programming error into "a turn where
    she was short of words"** — live, a `TypeError` (an argument not forwarded to
    the gateway) did exactly that, and because the ledger and the seed were right
    the e2e passed green. Recorded, it is obvious at a glance next time.
    """
    class _Boom:
        async def generate_text_stream(self, *a, **kw):
            raise TypeError("generate_text_stream() got an unexpected keyword")
            yield {}

        async def generate_text(self, *a, **kw):
            raise TypeError("generate_text() got an unexpected keyword")

    session = _session()
    out = await writer.actress_turn(
        _Boom(), model="m", locale="ja", name="各務 みお", now="",
        ledger={}, identity_blurb="", user_line="こっちを見て", director_tail="",
        session=session,
    )
    assert out["say"] == "……"
    notes = [n for n in (session.get("refine_log") or [])
             if n.get("kind") == "actress_failed"]
    assert notes, "黙って「……」になっている（記録に残っていない）"
    assert "TypeError" in str(notes[0].get("detail"))
