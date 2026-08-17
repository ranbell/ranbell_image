"""Who this shoot is of — one answer, not two.

Measured on a live session: 倉田あさひ's diary page carried a photo of a
dark-haired girl who is not her, and the same photo could not be found by
filtering the gallery for her.

`inputs.character_id` was set and `session["character"]` was empty, and the two
readers of "who is this" read different fields:

    finish_session   → inputs.character_id   → her diary was filed correctly
    the renderer     → session["character"]  → no identity tags in the prompt,
                                               no cast stamped on the image

So the studio wrote a page about a shoot of somebody else, said nothing, and
the photo ended up belonging to nobody.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import service  # noqa: E402


@pytest.fixture
def picker(monkeypatch):
    """Records who the resolver went and fetched."""
    calls: list[str] = []

    async def _pick(db, session, character_id):
        calls.append(character_id)
        session["character"] = {"character_id": character_id, "name": "Asahi"}
        return session

    monkeypatch.setattr(service, "pick_character", _pick)
    return calls


@pytest.mark.asyncio
async def test_an_id_in_inputs_is_enough_to_be_cast(picker):
    session = {"inputs": {"character_id": "asahi"}}

    await service.ensure_character(None, session)

    assert picker == ["asahi"]
    assert session["character"]["character_id"] == "asahi"


@pytest.mark.asyncio
async def test_a_cast_already_resolved_is_not_fetched_again(picker):
    session = {
        "inputs": {"character_id": "asahi"},
        "character": {"character_id": "asahi", "name": "Asahi"},
    }

    await service.ensure_character(None, session)

    assert picker == []


@pytest.mark.asyncio
async def test_a_session_with_nobody_cast_stays_that_way(picker):
    session = {"inputs": {}}

    await service.ensure_character(None, session)

    assert picker == []
    assert not session.get("character")


@pytest.mark.asyncio
async def test_a_lookup_that_fails_does_not_stop_the_shoot(monkeypatch):
    """A missing preset is not a reason to refuse to open the room."""
    async def _boom(db, session, character_id):
        raise RuntimeError("no such preset")

    monkeypatch.setattr(service, "pick_character", _boom)
    session = {"inputs": {"character_id": "ghost"}}

    await service.ensure_character(None, session)

    assert not session.get("character")
