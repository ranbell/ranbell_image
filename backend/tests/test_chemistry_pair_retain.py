"""Chemistry: one note per partner pair; load filtered by current partner."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.characters import presets as presets_db


@pytest.mark.asyncio
async def test_same_partner_keeps_only_latest_chemistry(monkeypatch):
    store = {
        "lead": {"id": "lead", "name": "Lead", "name_ja": "主演", "chemistry": []},
        "mio": {"id": "mio", "name": "Mio", "name_ja": "みお", "chemistry": []},
        "sumire": {"id": "sumire", "name": "Sumire", "name_ja": "すみれ", "chemistry": []},
    }

    async def fake_get(_db, pid):
        return store.get(pid)

    async def fake_update(_db, pid, patch):
        store[pid].update(patch)
        return store[pid]

    monkeypatch.setattr(presets_db, "get_preset", fake_get)
    monkeypatch.setattr(presets_db, "update_preset", fake_update)

    await presets_db.add_chemistry_record(None, "lead", "mio", {
        "id": "c1", "timestamp": 1.0, "summary_ja": "初回のみお",
        "tier": "acquaintance", "score": 0.1,
    })
    await presets_db.add_chemistry_record(None, "lead", "sumire", {
        "id": "c2", "timestamp": 2.0, "summary_ja": "すみれと",
        "tier": "close", "score": 0.5,
    })
    await presets_db.add_chemistry_record(None, "lead", "mio", {
        "id": "c3", "timestamp": 3.0, "summary_ja": "みおの前回",
        "tier": "close", "score": 0.6,
    })

    lead_chem = store["lead"]["chemistry"]
    mio_partners = [
        r["partner_character_id"] for r in lead_chem
    ]
    assert mio_partners.count("mio") == 1
    assert "sumire" in mio_partners
    mio_note = next(r for r in lead_chem if r["partner_character_id"] == "mio")
    assert mio_note["summary_ja"] == "みおの前回"
    assert mio_note["id"] == "c3"


@pytest.mark.asyncio
async def test_recent_notes_require_partner_and_return_that_pair_only(monkeypatch):
    store = {
        "lead": {
            "id": "lead",
            "chemistry": [
                {"partner_character_id": "mio", "summary_ja": "みお", "timestamp": 1},
                {"partner_character_id": "sumire", "summary_ja": "すみれ", "timestamp": 2},
            ],
        },
    }

    async def fake_get(_db, pid):
        return store.get(pid)

    monkeypatch.setattr(presets_db, "get_preset", fake_get)

    assert await presets_db.get_recent_chemistry_notes(None, "lead") == []
    assert await presets_db.get_recent_chemistry_notes(
        None, "lead", partner_id="",
    ) == []
    got = await presets_db.get_recent_chemistry_notes(
        None, "lead", limit=1, partner_id="mio",
    )
    assert got == ["みお"]
    got_b = await presets_db.get_recent_chemistry_notes(
        None, "lead", limit=1, partner_id="sumire",
    )
    assert got_b == ["すみれ"]
