"""制作スタッフ: PLAN/COSTUME → living notebook → scripter craft compile."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook, service, session_db
from app.muse.chain import MuseTurn
from tests.muse.test_duet_notebook import NotebookOllama, _scripter_block
from tests.muse.test_service import FakeDb, FakeOllama


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


async def _crew_session(db, **over):
    session = await service.create_session(db, {
        "theme": "夕暮れの屋上で待つ",
        "character_id": "char-1",
        "workflow": "w.json",
        "model": "m",
        "crew_preset": "trio",
        **over,
    })
    session["character"] = {
        "id": "char-1", "name": "Hana", "name_ja": "花",
        "identity_tags": ["1girl", "black_hair"],
        "personality": {}, "palette": [], "signature_prop": "",
    }
    service._rebuild_brief(session)
    await session_db.save(db, session)
    return session


def test_uses_notebook_after_crew_seed():
    session = {
        "mode": "",
        "inputs": {"theme": "夕暮れの屋上で待つ", "locale": "ja", "crew_ids": ["actress"]},
        "plan": {
            "place": "rooftop", "hour": "sunset", "light": "golden hour",
            "action": "waiting",
        },
        "costume": {
            "hero": "school blazer",
            "garments": "top=blazer / bottom=skirt / feet=loafers",
            "tags": ["blazer", "skirt", "loafers"],
        },
        "craft": {},
        "notebook": {},
    }
    assert service.uses_notebook(session) is False
    service.sync_crew_notebook(session, force_wearing=True, force_scene=True)
    assert service.uses_notebook(session) is True
    nb = notebook.of(session)
    assert "rooftop" in nb["scene"]
    assert "blazer" in nb["wearing"] or "school blazer" in nb["wearing"]
    assert int(nb["rev"] or 0) >= 1


def test_costume_change_forces_wearing_refresh():
    session = {
        "mode": "",
        "notebook_craft": True,
        "inputs": {"theme": "x", "locale": "ja", "crew_ids": ["actress"]},
        "notebook": {**notebook.blank(), "wearing": "old raincoat", "rev": 1},
        "costume": {
            "hero": "red cardigan",
            "garments": "top=cardigan / bottom=jeans",
            "tags": ["cardigan", "jeans"],
        },
        "plan": {},
        "craft": {},
    }
    service.sync_crew_notebook(session, force_wearing=True)
    assert "cardigan" in notebook.of(session)["wearing"]
    assert "raincoat" not in notebook.of(session)["wearing"]


@pytest.mark.asyncio
async def test_start_table_seeds_notebook():
    db, ollama = FakeDb(), FakeOllama()
    session = await _crew_session(db)
    session = await service.start_table(db, ollama, session)
    assert session.get("notebook_craft") is True
    nb = notebook.of(session)
    assert int(nb.get("rev") or 0) >= 1 or nb.get("atmosphere") or nb.get("scene")


@pytest.mark.asyncio
async def test_crew_note_runs_scripter_compile(monkeypatch):
    db = FakeDb()
    session = await _crew_session(db)
    session["status"] = "chat"
    session["table_stage"] = "full"
    session["craft"] = {
        "prompt": "1girl, rooftop, blazer",
        "tags": "1girl, rooftop, blazer, skirt",
        "scene": "She waits on the rooftop in a blazer.",
        "pose_intent": "waiting",
    }
    session["plan"] = {
        "place": "rooftop", "hour": "sunset", "light": "gold", "action": "waiting",
    }
    session["costume"] = {
        "hero": "blazer",
        "garments": "top=blazer / bottom=skirt",
        "tags": ["blazer", "skirt"],
    }
    session["spoken"] = ["plan", "actress", "lens", "wardrobe"]
    service.sync_crew_notebook(session, force_wearing=True, force_scene=True)

    scripts = {
        "カーディガン": _scripter_block(
            intent="shot",
            scene="rooftop at sunset",
            wearing="red cardigan over a white blouse; denim skirt",
            beat="hugging her elbows, waiting",
            tags="1girl, rooftop, sunset, red_cardigan, white_blouse, denim_skirt, standing",
            craft_scene=(
                "She stands on the rooftop at sunset in a red cardigan over a "
                "white blouse and a denim skirt, hugging her elbows while she waits."
            ),
        ),
    }
    ollama = NotebookOllama(scripts=scripts)

    async def _no_banter(*_a, **_k):
        return None

    monkeypatch.setattr(service, "_run_banter", _no_banter)

    async def _fake_muse_turn(*_a, **_k):
        return MuseTurn(
            muse_id="actress",
            say="了解、カーディガンにします。",
            prompt="",
            pose_intent="",
            tags="",
            scene="",
            raw="",
        ), 1

    monkeypatch.setattr(service, "_run_muse_turn", _fake_muse_turn)

    out = await service.post_chat(
        db, ollama, None, None, session, "赤いカーディガンにして",
    )
    assert ollama.scripter_prompts, "crew note must call the scripter"
    tags = str((out.get("craft") or {}).get("tags") or "").lower()
    wearing = notebook.of(out).get("wearing", "").lower()
    assert "cardigan" in tags or "cardigan" in wearing
