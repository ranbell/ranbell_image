"""**A deleted picture must not leave its derived rows behind.** (2026-11-02)

The Showrunner: "deleting is weak after a file is removed from outside, so I
think orphans are left behind."
Measured on the live library: `images` held 17,949 points and `images_color`
18,323 — **398 colour rows for pictures that no longer existed**, and
`alignment` had the same hole waiting, empty only by luck.

`delete()` and `delete_batch()` only ever touched `IMAGES_COLLECTION`. Nothing
anywhere deleted from `images_color`, so every file removed outside Ranbell —
the path `scanner.run_heal` takes through `delete_batch` — leaked one for good.

The derived collections key by the same point id, so the cascade is the same
ids again. Best effort: losing the picture's own row is what matters, and a
derived row that will not go is logged for the sweep rather than failing the
delete.
"""
import asyncio
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.db.qdrant_client import (  # noqa: E402
    ALIGNMENT_COLLECTION,
    IMAGES_COLLECTION,
    IMAGES_COLOR_COLLECTION,
    QdrantDBClient,
    sha256_to_point_id,
)


class _Recorder:
    """Records deletes; optionally refuses for one collection."""

    def __init__(self, refuse: str = ""):
        self.deleted: list[tuple[str, list]] = []
        self._refuse = refuse

    async def delete(self, *, collection_name, points_selector, wait=True):
        if collection_name == self._refuse:
            raise RuntimeError("qdrant said no")
        self.deleted.append((collection_name, list(points_selector.points)))


def _db(refuse: str = "") -> tuple[QdrantDBClient, _Recorder]:
    db = QdrantDBClient.__new__(QdrantDBClient)
    rec = _Recorder(refuse)
    db._qc = rec
    db._qc_bulk = rec
    return db, rec


def test_deleting_one_picture_clears_its_colour_and_alignment():
    db, rec = _db()
    sha = "a" * 64
    asyncio.run(db.delete(sha))

    touched = [name for name, _ in rec.deleted]
    assert touched == [IMAGES_COLLECTION, IMAGES_COLOR_COLLECTION, ALIGNMENT_COLLECTION]
    point_id = sha256_to_point_id(sha)
    for _, ids in rec.deleted:
        assert ids == [point_id], "派生先が同じ点 id で消されていない"


def test_a_batch_delete_clears_them_too():
    """This is the path a file deleted outside Ranbell takes."""
    db, rec = _db()
    shas = [f"{i:064x}" for i in range(3)]
    asyncio.run(db.delete_batch(shas, wait=False))

    by_collection = {}
    for name, ids in rec.deleted:
        by_collection.setdefault(name, []).extend(ids)
    want = [sha256_to_point_id(s) for s in shas]
    assert by_collection[IMAGES_COLLECTION] == want
    assert by_collection[IMAGES_COLOR_COLLECTION] == want
    assert by_collection[ALIGNMENT_COLLECTION] == want


def test_a_derived_collection_that_will_not_answer_does_not_fail_the_delete():
    """The picture's own row going is what matters."""
    db, rec = _db(refuse=IMAGES_COLOR_COLLECTION)
    asyncio.run(db.delete("b" * 64))      # must not raise

    touched = [name for name, _ in rec.deleted]
    assert IMAGES_COLLECTION in touched
    assert ALIGNMENT_COLLECTION in touched, "一つ失敗したら残りも止まっている"


def test_nothing_is_deleted_for_an_empty_batch():
    db, rec = _db()
    asyncio.run(db._delete_derived([]))
    assert rec.deleted == []


def test_the_derived_list_is_the_one_place_they_are_named():
    assert QdrantDBClient.DERIVED_OF_IMAGES == (
        IMAGES_COLOR_COLLECTION, ALIGNMENT_COLLECTION,
    )
