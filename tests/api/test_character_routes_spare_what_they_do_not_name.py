"""**The character routes remove what they name; a preview changes nothing.** (2026-11-21)

The cast is where the Showrunner's long work lives: characters written by hand,
their diaries, their boards. Three of these routes are one click from the
character screen and act on all of it — reset, erase-memory, delete — and two
of them have a preview the confirm dialog reads its numbers from. A preview
that wrote, or a delete that spread, would be found out only afterwards.

`backend/tests/test_memory_erase.py` checks the erase itself as functions; these
check the doors.
"""
from __future__ import annotations

import pytest

from backend.app.characters import presets as presets_db
from tests.memdb import sha

pytestmark = [pytest.mark.asyncio, pytest.mark.filterwarnings("ignore::UserWarning")]


async def _character(api, name: str) -> str:
    r = await api.http.post("/api/characters", json={"name": name})
    assert r.status_code == 200, r.text
    return str(r.json()["id"])


async def _names(api) -> set[str]:
    return {c.get("name") for c in (await api.http.get("/api/characters")).json()["characters"]}


async def _diary(api, cid: str, text: str) -> str:
    import time
    import uuid
    did = uuid.uuid4().hex
    await presets_db.add_preset_diary(api.db, cid, {"id": did, "text": text, "image_ids": [],
                                                    "timestamp": time.time()})
    return did


# ── delete ───────────────────────────────────────────────────────────────────

async def test_deleting_a_character_leaves_the_rest_of_the_cast(api):
    mine = await _character(api, "Mine")
    await _character(api, "Other")

    assert (await api.http.delete(f"/api/characters/{mine}")).status_code == 200

    assert (await api.http.get(f"/api/characters/{mine}")).status_code == 404
    assert "Other" in await _names(api)


@pytest.mark.parametrize("bad", ["00000000-0000-0000-0000-000000000000", "not-an-id", "999999"])
async def test_deleting_a_character_that_is_not_there_is_a_404(api, bad):
    await _character(api, "Keep")
    r = await api.http.delete(f"/api/characters/{bad}")
    assert r.status_code == 404
    assert "Keep" in await _names(api)


# ── diaries ──────────────────────────────────────────────────────────────────

async def test_deleting_a_diary_takes_that_page_only(api):
    cid = await _character(api, "Writer")
    keep = await _diary(api, cid, "kept")
    gone = await _diary(api, cid, "torn out")

    assert (await api.http.delete(f"/api/characters/{cid}/diaries/{gone}")).status_code == 200

    texts = [d.get("text") for d in (await api.http.get(f"/api/characters/{cid}/diaries")).json()["diaries"]]
    assert texts == ["kept"], texts
    assert keep


async def test_deleting_a_diary_that_is_not_there_is_a_404(api):
    cid = await _character(api, "Writer")
    await _diary(api, cid, "kept")
    r = await api.http.delete(f"/api/characters/{cid}/diaries/no-such-page")
    assert r.status_code == 404
    assert len((await api.http.get(f"/api/characters/{cid}/diaries")).json()["diaries"]) == 1


# ── erase-memory ─────────────────────────────────────────────────────────────

async def test_the_erase_preview_counts_and_touches_nothing(api):
    cid = await _character(api, "Rememberer")
    await _diary(api, cid, "a day")

    r = await api.http.post("/api/characters/erase-memory", json={"dry_run": True})

    assert r.status_code == 200 and r.json()["dry_run"] is True
    assert len((await api.http.get(f"/api/characters/{cid}/diaries")).json()["diaries"]) == 1


async def test_erasing_memory_keeps_the_characters_and_every_picture(api):
    cid = await _character(api, "Rememberer")
    await _diary(api, cid, "a day")
    await api.db.upsert_new(sha("a"), {"sha256": sha("a"), "path": "/x/a.png"})

    r = await api.http.post("/api/characters/erase-memory", json={"confirm": "confirm"})

    assert r.status_code == 200 and r.json()["dry_run"] is False
    assert (await api.http.get(f"/api/characters/{cid}/diaries")).json()["diaries"] == []
    assert "Rememberer" in await _names(api)
    assert await api.db.total_count() == 1, "nothing here deletes an image"


# ── reset ────────────────────────────────────────────────────────────────────

async def test_the_reset_preview_changes_nothing(api):
    await _character(api, "Handmade")
    before = await _names(api)
    r = await api.http.post("/api/characters/reset", json={"dry_run": True})
    assert r.status_code == 200
    assert await _names(api) == before


async def test_a_reset_keeps_hand_made_characters_and_their_diaries(api):
    cid = await _character(api, "Handmade")
    await _diary(api, cid, "mine")

    r = await api.http.post("/api/characters/reset", json={"confirm": "confirm"})

    assert r.status_code == 200
    assert "Handmade" in await _names(api)
    assert len((await api.http.get(f"/api/characters/{cid}/diaries")).json()["diaries"]) == 1


async def test_only_a_wipe_removes_hand_made_characters(api):
    await _character(api, "Handmade")
    r = await api.http.post("/api/characters/reset", json={"wipe": True, "confirm": "confirm"})
    assert r.status_code == 200
    assert "Handmade" not in await _names(api)


# ── the phrase ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("route,body", [
    ("/api/characters/erase-memory", None),
    ("/api/characters/erase-memory", {}),
    ("/api/characters/erase-memory", {"dry_run": False, "confirm": "yes"}),
    ("/api/characters/reset", None),
    ("/api/characters/reset", {"wipe": True}),
])
async def test_erase_and_reset_need_the_phrase_from_the_server_too(api, route, body):
    """A bare POST used to run them: the phrase was asked for only in the dialog."""
    cid = await _character(api, "Handmade")
    await _diary(api, cid, "mine")

    r = await api.http.post(route, json=body) if body is not None else await api.http.post(route)

    assert r.status_code == 400 and r.json()["detail"]["error"] == "confirm_required"
    assert "Handmade" in await _names(api)
    assert len((await api.http.get(f"/api/characters/{cid}/diaries")).json()["diaries"]) == 1
