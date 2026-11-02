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

**And they are not asked for at all once they exist** (2026-11-02). `wait=False`
stops the boot waiting on the *build*, but the ask itself is not free: measured
against the 17.9k-point collection, re-asking for an index that is already there
costs ~176 ms, and forty of those were 11 of the 15 seconds `db.start()` took.
Deleting the umap indexes that are never there after the first boot cost another
2 s apiece. Reading `payload_schema` once costs 9 ms and says what is already
done, which took the start-up from 15,099 ms to 52 ms.
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
    assert calls, "呼び出しが読めていない"
    for call in calls:
        waits = [kw.value for kw in call.keywords if kw.arg == "wait"]
        assert waits, f"{call.lineno} 行目の作成が wait を指定していない"
        assert waits[0].value is False, f"{call.lineno} 行目が wait=True で待っている"


def test_the_boot_asks_only_for_the_indexes_that_are_missing():
    """Every collection's indexes go through the one helper that looks first."""
    import asyncio

    asked: list[str] = []

    class _Stub:
        def __init__(self, present):
            self._present = present

        async def get_collection(self, collection_name):
            return _Info({name: object() for name in self._present})

        async def create_payload_index(self, *, collection_name, field_name, **kw):
            asked.append(field_name)

    all_fields = [field for field, _ in qc.IMAGE_PAYLOAD_INDEXES]
    db = qc.QdrantDBClient.__new__(qc.QdrantDBClient)

    # A settled collection: nothing is asked for at all.
    db._qc = _Stub(all_fields)
    asyncio.run(db._create_images_indexes())
    assert asked == [], f"既にあるものを作り直している: {asked}"

    # A half-built one: only what is missing.
    db._qc = _Stub(all_fields[:20])
    asyncio.run(db._create_images_indexes())
    assert sorted(asked) == sorted(all_fields[20:])


def test_the_umap_indexes_are_only_dropped_when_they_are_there():
    """Deleting one that is absent costs ~2 s, and after the first boot it is."""
    import asyncio

    dropped: list[str] = []

    class _Stub:
        def __init__(self, present):
            self._present = present

        async def get_collection(self, collection_name):
            return _Info({name: object() for name in self._present})

        async def delete_payload_index(self, *, collection_name, field_name):
            dropped.append(field_name)

    db = qc.QdrantDBClient.__new__(qc.QdrantDBClient)

    db._qc = _Stub(["star_rating"])          # the usual case: long gone
    asyncio.run(db._drop_umap_indexes())
    assert dropped == [], "無いものを消そうとしている"

    db._qc = _Stub(["star_rating", "umap_x", "umap_y"])   # the first boot
    asyncio.run(db._drop_umap_indexes())
    assert sorted(dropped) == ["umap_x", "umap_y"]


def test_the_table_is_the_one_source_for_the_images_indexes():
    """34 fields, each named once — the live collection carries exactly these."""
    fields = [field for field, _ in qc.IMAGE_PAYLOAD_INDEXES]
    assert len(fields) == len(set(fields)), "同じ欄が二度入っている"
    assert len(fields) == 34
    for expected in ("is_draft", "muse_session_id", "character_id",
                     "creation_record.method", "emotion_loneliness",
                     "dir", "dup_dirs"):
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
    assert out["total"] == 34
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
