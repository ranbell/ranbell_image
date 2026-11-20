"""**The admin's bulk rewrites touch what they name, and nothing beside it.** (2026-11-21)

These run over the whole collection from one button on the admin screen, and
what they touch is the Showrunner's archive: paths, embeddings, tags and the
records derived from them. None of them was tested. Each test here seeds a
neighbour that must come through untouched, because that is where a bulk
operation goes wrong — not on the row it was aimed at, but on the one next to it.

Two of them were wrong when these were written:

- `migrate_path_prefix("/mnt/a", "/new")` also moved `/mnt/ab/y.png`, to
  `/newb/y.png` — a picture in another folder pointed at a path that does not
  exist. A trailing slash on one prefix and not the other dropped the separator.
- `reset_scope` removed `embedding` and left `embedding_small`, so a reset
  picture went on taking a slot in the first phase of every search.
"""
from __future__ import annotations

import random

import pytest

from backend.app.config import settings
from backend.app.db.id_utils import sha256_to_point_id
from backend.app.db.qdrant_client import (
    ALIGNMENT_COLLECTION,
    IMAGES_COLLECTION,
    IMAGES_COLOR_COLLECTION,
)
from tests.db.conftest import sha

pytestmark = [pytest.mark.asyncio, pytest.mark.filterwarnings("ignore::UserWarning")]

A, B, C = sha("a"), sha("b"), sha("c")


async def _image(db, s: str, path: str, **extra) -> None:
    await db.upsert_new(s, {"sha256": s, "path": path, **extra})


async def _vectors(db, s: str) -> set[str]:
    pts = await db._qc.retrieve(IMAGES_COLLECTION, ids=[sha256_to_point_id(s)], with_vectors=True)
    return set((pts[0].vector or {}).keys()) if pts else set()


def _emb(dim: int) -> list[float]:
    return [random.random() for _ in range(dim)]


# ── migrate_path_prefix ──────────────────────────────────────────────────────

async def test_a_folder_with_a_longer_name_is_not_moved(db):
    await _image(db, A, "/mnt/a/x.png")
    await _image(db, B, "/mnt/ab/y.png")

    assert await db.migrate_path_prefix("/mnt/a", "/new") == 1

    assert (await db.get(A))["path"] == "/new/x.png"
    assert (await db.get(B))["path"] == "/mnt/ab/y.png"


@pytest.mark.parametrize("old,new", [
    ("/mnt/a", "/new"), ("/mnt/a/", "/new"), ("/mnt/a", "/new/"), ("/mnt/a/", "/new/"),
])
async def test_a_trailing_slash_on_either_side_gives_the_same_path(db, old, new):
    await _image(db, A, "/mnt/a/sub/x.png")
    await db.migrate_path_prefix(old, new)
    got = await db.get(A)
    assert got["path"] == "/new/sub/x.png"
    assert got["dir"] == "/new/sub"


async def test_duplicates_move_with_their_picture_and_stay_put_elsewhere(db):
    await _image(db, A, "/mnt/a/x.png", dup_paths=[
        {"path": "/mnt/a/copy/x.png", "size": 1},
        {"path": "/mnt/other/x.png", "size": 1},
    ])
    await db.migrate_path_prefix("/mnt/a", "/new")
    got = await db.get(A)
    assert [d["path"] for d in got["dup_paths"]] == ["/new/copy/x.png", "/mnt/other/x.png"]
    assert got["dup_dirs"] == ["/mnt/other", "/new/copy"]
    assert got["dup_paths"][0]["size"] == 1, "the rest of a duplicate's record is kept"


async def test_is_reference_follows_the_new_path(db, monkeypatch):
    monkeypatch.setattr(settings, "source_images_dir", "/library/source")
    await _image(db, A, "/old/source/x.png", is_reference=False)
    await db.migrate_path_prefix("/old/source", "/library/source")
    assert (await db.get(A))["is_reference"] is True


async def test_a_move_forgets_the_folder_listing_and_a_no_op_keeps_it(db):
    await _image(db, A, "/mnt/a/x.png")
    await db.put_scan_dirs([{"path": "/mnt/a", "mtime_ns": 1}])

    assert await db.migrate_path_prefix("/nowhere", "/new") == 0
    assert "/mnt/a" in (await db.load_scan_dirs())[0], "nothing moved, nothing forgotten"

    await db.migrate_path_prefix("/mnt/a", "/new")
    assert (await db.load_scan_dirs())[0] == {}, "the next heal starts from a clean listing"


async def test_the_rest_of_the_payload_survives_a_move(db):
    await _image(db, A, "/mnt/a/x.png", tags=["cat"], rating=5)
    await db.migrate_path_prefix("/mnt/a", "/new")
    got = await db.get(A)
    assert got["tags"] == ["cat"] and got["rating"] == 5


# ── reset_scope ──────────────────────────────────────────────────────────────

async def _embedded(db, s: str, path: str) -> None:
    await _image(db, s, path, embedding_status="done", wd14_tags=["1girl"])
    await db.set_embedding(s, _emb(db.embed_dim))


async def test_reset_done_clears_both_widths_and_marks_pending(db):
    await _embedded(db, A, "/x/a.png")
    assert await _vectors(db, A) == {"embedding", "embedding_small"}

    assert await db.reset_scope("done") == 1

    assert await _vectors(db, A) == set()
    got = await db.get(A)
    assert got["embedding_status"] == "pending"
    assert got["wd14_tags"] == []
    assert got["path"] == "/x/a.png", "the picture itself stays"


async def test_reset_done_leaves_pending_pictures_alone(db):
    await _embedded(db, A, "/x/a.png")
    await _image(db, B, "/x/b.png", embedding_status="pending", wd14_tags=["keep"])

    assert await db.reset_scope("done") == 1
    assert (await db.get(B))["wd14_tags"] == ["keep"]


async def test_reset_pending_keeps_embeddings(db):
    await _embedded(db, A, "/x/a.png")
    await _image(db, B, "/x/b.png", embedding_status="pending", wd14_tags=["t"])

    assert await db.reset_scope("pending") == 1

    assert await _vectors(db, A) == {"embedding", "embedding_small"}
    assert (await db.get(A))["wd14_tags"] == ["1girl"]
    assert (await db.get(B))["wd14_tags"] == []


async def test_reset_all_clears_every_picture_and_keeps_every_picture(db):
    await _embedded(db, A, "/x/a.png")
    await _image(db, B, "/x/b.png", embedding_status="pending")

    assert await db.reset_scope("all") == 2
    assert await db.total_count() == 2
    assert await _vectors(db, A) == set()


async def test_a_reset_picture_no_longer_answers_a_search(db):
    v = _emb(db.embed_dim)
    await _image(db, A, "/x/a.png", embedding_status="done")
    await db.set_embedding(A, v)
    assert [r["sha256"] for r in await db.search_vector(v, 5)] == [A]

    await db.reset_scope("done")
    assert await db.search_vector(v, 5) == []


# ── delete_all_images ────────────────────────────────────────────────────────

async def test_delete_all_counts_and_forgets_the_folder_listing(db):
    await _image(db, A, "/x/a.png")
    await _image(db, B, "/x/b.png")
    await db.put_scan_dirs([{"path": "/x", "mtime_ns": 1}])
    await db.put_config({"keep": 1})

    assert await db.delete_all_images() == 2

    assert await db.total_count() == 0
    assert (await db.load_scan_dirs())[0] == {}, "else an unchanged folder is never re-registered"
    assert (await db.get_config())["keep"] == 1, "settings are not pictures"


# ── sweep_orphans / cleanup_orphan_alignments ────────────────────────────────

async def _derived(db, s: str) -> None:
    await db.set_color_vector(s, [50.0, 0.0, 0.0], {"palette_hex": ["#000000"]})
    await db.upsert_alignment(s, {"image_id": s, "score": 0.5})


async def _ids(db, collection: str) -> set:
    pts, _ = await db._qc.scroll(collection, limit=100, with_payload=False)
    return {str(p.id) for p in pts}


async def _orphaned(db) -> None:
    """A lives; B's picture is gone but its colour and alignment rows are not."""
    await _image(db, A, "/x/a.png")
    await _derived(db, A)
    await _image(db, B, "/x/b.png")
    await _derived(db, B)
    await db._qc.delete(IMAGES_COLLECTION, points_selector=[sha256_to_point_id(B)])


async def test_the_stock_take_counts_and_touches_nothing(db):
    await _orphaned(db)
    before = {c: await _ids(db, c) for c in (IMAGES_COLOR_COLLECTION, ALIGNMENT_COLLECTION)}

    out = await db.sweep_orphans(delete=False)

    assert out == {IMAGES_COLOR_COLLECTION: 1, ALIGNMENT_COLLECTION: 1}
    for c, ids in before.items():
        assert await _ids(db, c) == ids


async def test_the_sweep_clears_only_rows_whose_picture_is_gone(db):
    await _orphaned(db)
    await db.sweep_orphans(delete=True)
    live = {str(sha256_to_point_id(A))}
    assert await _ids(db, IMAGES_COLOR_COLLECTION) == live
    assert await _ids(db, ALIGNMENT_COLLECTION) == live
    assert await db.total_count() == 1, "the sweep never touches images"


async def test_a_cancelled_sweep_deletes_nothing(db):
    await _orphaned(db)
    out = await db.sweep_orphans(delete=True, should_stop=lambda: True)
    assert out == {"cancelled": 1}
    assert len(await _ids(db, ALIGNMENT_COLLECTION)) == 2


async def test_alignment_cleanup_removes_only_orphans(db):
    await _orphaned(db)
    assert await db.cleanup_orphan_alignments() == 1
    assert await db.get_alignment(A) is not None
    assert await db.get_alignment(B) is None
    assert await db.cleanup_orphan_alignments() == 0, "a second run finds nothing"


# ── config: merges, and removes only what is named ───────────────────────────

async def test_config_writes_merge_and_deletes_are_by_name(db):
    await db.put_config({"a": 1, "b": 2})
    await db.put_config({"b": 3, "c": 4})
    assert {k: v for k, v in (await db.get_config()).items() if k in "abc"} == {"a": 1, "b": 3, "c": 4}

    await db.delete_config_keys(["b"])
    got = await db.get_config()
    assert "b" not in got and got["a"] == 1 and got["c"] == 4


async def test_payload_keys_are_removed_by_name_from_the_named_pictures_only(db):
    await _image(db, A, "/x/a.png", color_lab=[1, 2, 3], tags=["t"])
    await _image(db, B, "/x/b.png", color_lab=[4, 5, 6])

    await db.delete_payload_keys_batch([A], ["color_lab"])

    a, b = await db.get(A), await db.get(B)
    assert "color_lab" not in a and a["tags"] == ["t"]
    assert b["color_lab"] == [4, 5, 6]
