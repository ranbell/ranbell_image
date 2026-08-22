"""'記憶の消去' (erase memory) — clearing diary/chemistry/lounge data for every
character while leaving the character sheet, board, and gallery photos alone."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.characters import presets
from backend.app.db.qdrant_client import CHARACTER_PRESETS_COLLECTION
from backend.app.muse import handpost_db


class _FakeQC:
    """Enough of the qdrant client surface for presets.py's memory-erase path:
    scroll (list all), retrieve (get one), set_payload (update one)."""

    def __init__(self, rows: dict[str, dict]):
        self._rows = rows  # {point_id: payload}
        self.set_payload_calls: list[tuple[str, dict]] = []

    async def scroll(self, *, collection_name, limit=256, offset=None,
                      with_payload=True, with_vectors=False):
        assert collection_name == CHARACTER_PRESETS_COLLECTION
        points = [SimpleNamespace(id=pid, payload=payload) for pid, payload in self._rows.items()]
        return points, None

    async def retrieve(self, *, collection_name, ids, with_payload=True, with_vectors=False):
        payload = self._rows.get(ids[0])
        if payload is None:
            return []
        return [SimpleNamespace(id=ids[0], payload=payload)]

    async def set_payload(self, *, collection_name, payload, points):
        pid = points[0]
        self._rows[pid] = payload
        self.set_payload_calls.append((pid, payload))


class _FakeDB:
    def __init__(self, rows: dict[str, dict]):
        self._qc = _FakeQC(rows)


def _preset(**overrides):
    base = {
        "name": "Test", "name_ja": "テスト",
        "board": {"sheet": "sha_sheet", "portrait": "sha_portrait"},
        "gallery": {"sheet": [{"sha": "sha_sheet", "workflow": "wf", "at": 0.0}],
                    "portrait": [{"sha": "sha_portrait", "workflow": "wf", "at": 0.0}]},
        "diaries": [], "chemistry": [], "social_seeds": [],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_plan_memory_erase_counts_without_changing_anything():
    rows = {
        "c1": _preset(diaries=[{"id": "d1"}, {"id": "d2"}], chemistry=[{"id": "x"}]),
        "c2": _preset(social_seeds=[{"id": "s1"}]),
        "c3": _preset(),  # nothing to clear
    }
    db = _FakeDB(rows)

    plan = await presets.plan_memory_erase(db)

    # **消える欄が増えたら、ここも増える。** 数えている欄そのものが増えたので
    # あって、消え方が変わったわけではない ―― 撮影回数や好みも記憶のうち。
    assert plan == {
        "characters": 3, "affected": 2,
        "diaries": 2, "chemistry": 1, "social_seeds": 1,
        "bond": 0, "shoot_count": 0, "shoot_recaps": 0,
        "last_shoot_at": 0, "showrunner_taste": 0,
    }
    # Nothing was written.
    assert db._qc.set_payload_calls == []


@pytest.mark.asyncio
async def test_erase_all_memory_fields_clears_only_memory_and_keeps_the_sheet():
    rows = {
        "c1": _preset(diaries=[{"id": "d1"}], chemistry=[{"id": "x"}], social_seeds=[{"id": "s"}]),
        "c2": _preset(),  # already empty — should not be touched
    }
    db = _FakeDB(rows)

    touched = await presets.erase_all_memory_fields(db)

    assert touched == 1
    assert db._qc._rows["c1"]["diaries"] == []
    assert db._qc._rows["c1"]["chemistry"] == []
    assert db._qc._rows["c1"]["social_seeds"] == []
    # The sheet and board/gallery photos are untouched.
    assert db._qc._rows["c1"]["name_ja"] == "テスト"
    assert db._qc._rows["c1"]["board"] == {"sheet": "sha_sheet", "portrait": "sha_portrait"}
    assert db._qc._rows["c1"]["gallery"]["portrait"][0]["sha"] == "sha_portrait"
    # c2 had nothing to clear, so it was never written.
    assert "c2" not in [pid for pid, _ in db._qc.set_payload_calls]


def test_handpost_is_generated_keeps_directors_own_notices():
    hand_written = {"author": "director", "title": "撮影日は月曜"}
    habit = {"author": "system", "source_character_id": "c1", "title": "癖"}
    promoted_pitch = {"author": "director", "source_thread_id": "t1", "title": "採用"}

    assert handpost_db._is_generated(hand_written) is False
    assert handpost_db._is_generated(habit) is True
    assert handpost_db._is_generated(promoted_pitch) is True
