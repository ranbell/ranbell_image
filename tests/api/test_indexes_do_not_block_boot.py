"""**A first boot after an update must not look like a hang.** (2026-09-21)

The `images` collection carries 32 payload indexes, and building one scans every
point in the collection. They were created inside the startup path with
`wait=True`, one after another, before uvicorn began listening — so on a live
collection the app was unreachable for minutes (measured on the Showrunner's
machine: 17–25 s per index) and the client's own 30 s timeout sat right behind
it: one slow index and `db.start()` raises, the container exits, and
`restart: unless-stopped` turns that into a loop.

They are asked for with `wait=False` now. Qdrant builds them in the background,
the app boots at once, and `/api/health` says how many are still coming so the
screen can show it.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import qdrant_client as qc

SOURCE = (ROOT / "backend/app/db/qdrant_client.py").read_text(encoding="utf-8")


def test_no_index_is_created_with_a_wait():
    """Read from the calls themselves — every one names `wait=False`."""
    tree = ast.parse(SOURCE)
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_payload_index"
    ]
    assert len(calls) >= 8, f"呼び出しが読めていない（{len(calls)}件）"
    for call in calls:
        waits = [kw.value for kw in call.keywords if kw.arg == "wait"]
        assert waits, f"{call.lineno} 行目の作成が wait を指定していない"
        assert waits[0].value is False, f"{call.lineno} 行目が wait=True で待っている"


def test_the_table_is_the_one_source_for_the_images_indexes():
    """32 fields, each named once — the live collection carries exactly these."""
    fields = [field for field, _ in qc.IMAGE_PAYLOAD_INDEXES]
    assert len(fields) == len(set(fields)), "同じ欄が二度入っている"
    assert len(fields) == 32
    for expected in ("is_draft", "muse_session_id", "character_id",
                     "creation_record.method", "emotion_loneliness"):
        assert expected in fields
    # umap_x / umap_y must stay out: an index on them rebuilds on every UMAP save.
    assert "umap_x" not in fields and "umap_y" not in fields


class _Info:
    def __init__(self, schema):
        self.payload_schema = schema


class _Stub:
    """Only what `index_progress` reaches for."""

    def __init__(self, present):
        self._present = present

    async def get_collection(self, collection_name):
        return _Info({name: object() for name in self._present})


def _client(present):
    db = qc.QdrantDBClient.__new__(qc.QdrantDBClient)
    db._qc = _Stub(present)
    return db


async def _progress(present):
    return await _client(present).index_progress()


def test_progress_counts_what_is_still_missing():
    import asyncio

    all_fields = [field for field, _ in qc.IMAGE_PAYLOAD_INDEXES]
    out = asyncio.run(_progress(all_fields[:20]))

    assert out["ok"] is True
    assert out["total"] == 32
    assert out["ready"] == 20
    assert out["building"], "何を作っているかが出ていない"
    assert all(name in all_fields for name in out["building"])


def test_a_settled_collection_reports_nothing_left():
    import asyncio

    out = asyncio.run(_progress([field for field, _ in qc.IMAGE_PAYLOAD_INDEXES]))

    assert out["ready"] == out["total"]
    assert out["building"] == []


def test_a_database_that_will_not_answer_is_not_an_error():
    """The probe must not fail because the count could not be read."""
    import asyncio

    class _Broken:
        async def get_collection(self, collection_name):
            raise RuntimeError("no")

    db = qc.QdrantDBClient.__new__(qc.QdrantDBClient)
    db._qc = _Broken()
    out = asyncio.run(db.index_progress())

    assert out["ok"] is False
    assert out["ready"] == 0
