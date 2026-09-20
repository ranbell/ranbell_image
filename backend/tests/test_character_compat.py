"""Unit tests for backend.app.characters.compat.compat_matrix — the bulk,
in-memory scored version behind the Chemistry Vector Viewer."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.characters import compat
from backend.app.db.qdrant_client import (
    CHARACTER_COMPAT_COLLECTION, CHARACTER_PRESETS_COLLECTION, MUSE_SESSIONS_COLLECTION,
)


class _FakeQC:
    """One scroll per collection, no pagination — three characters is plenty
    to exercise pair-scoring without needing a second page."""

    def __init__(self, presets, vectors, sessions):
        self._data = {
            CHARACTER_PRESETS_COLLECTION: presets,
            CHARACTER_COMPAT_COLLECTION: vectors,
            MUSE_SESSIONS_COLLECTION: sessions,
        }

    async def scroll(self, *, collection_name, limit=256, offset=None,
                      with_payload=True, with_vectors=False):
        items = self._data.get(collection_name, [])
        if with_vectors:
            points = [SimpleNamespace(id=pid, vector=data) for pid, data in items]
        else:
            points = [SimpleNamespace(id=pid, payload=data) for pid, data in items]
        return points, None


class _FakeDB:
    def __init__(self, presets, vectors, sessions):
        self._qc = _FakeQC(presets, vectors, sessions)


@pytest.mark.asyncio
async def test_compat_matrix_scores_all_pairs_and_counts_co_appearances():
    presets = [
        ("c001", {"name_ja": "アリス", "name": "Alice"}),
        ("c002", {"name_ja": "ボブ", "name": "Bob"}),
        ("c003", {"name_ja": "キャロル", "name": "Carol"}),
    ]
    vectors = [
        ("c001", {"appearance": [1.0, 0.0], "personality": [1.0, 0.0]}),
        ("c002", {"appearance": [1.0, 0.0], "personality": [1.0, 0.0]}),
        # c003 has never been embedded — a fresh character, or backfill hasn't run.
    ]
    sessions = [
        ("s1", {"status": "finished",
                "inputs": {"character_id": "c001", "partner_preset": "c002"}}),
        ("s2", {"status": "finished",
                "inputs": {"character_id": "c002", "partner_preset": "c001"}}),
        # Not finished — must not count.
        ("s3", {"status": "shooting",
                "inputs": {"character_id": "c001", "partner_preset": "c002"}}),
        # Solo session — no partner, must not count.
        ("s4", {"status": "finished", "inputs": {"character_id": "c001"}}),
    ]
    db = _FakeDB(presets, vectors, sessions)

    result = await compat.compat_matrix(db)

    assert {c["id"] for c in result["characters"]} == {"c001", "c002", "c003"}
    pairs = {frozenset((p["a"], p["b"])): p for p in result["pairs"]}
    assert len(pairs) == 3  # 3 characters -> C(3,2) pairs, order-independent

    ab = pairs[frozenset(("c001", "c002"))]
    assert ab["co_appearances"] == 2
    assert ab["score"] == pytest.approx(1.0)
    assert ab["tier"] == "best_friend"

    ac = pairs[frozenset(("c001", "c003"))]
    assert ac["co_appearances"] == 0
    assert ac["score"] == 0.0
    assert ac["tier"] == "acquaintance"
