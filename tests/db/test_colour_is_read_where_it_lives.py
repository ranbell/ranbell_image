"""**Colour is read from `images_color`, where it moved.** (2026-11-21)

Colour vectors moved out of `images` into their own collection, and two readers
were left asking `images` for a `color_vector`. A Qdrant server refuses that
("Not existing vector name"), so:

- the analyzer's 3D colour space answered HTTP 500 every time, and
- the colour backfill job failed at its last step, after doing all its work.

The in-memory engine does not refuse — it quietly returns no vector — and that
showed what the code would have done next: the "repair" took every real colour
for a zero and wrote over it with an upsert that emptied the row's payload.

`_ServerStrict` gives these tests the server's refusal back, so the mistake
cannot hide behind the in-memory engine's leniency again.
"""
from __future__ import annotations

import pytest

from backend.app.db.id_utils import sha256_to_point_id
from backend.app.db.qdrant_client import IMAGES_COLLECTION, IMAGES_COLOR_COLLECTION
from tests.db.conftest import sha

pytestmark = [pytest.mark.asyncio, pytest.mark.filterwarnings("ignore::UserWarning")]

A, B, C, GONE = sha("a"), sha("b"), sha("c"), sha("d")


class _ServerStrict:
    """The client, refusing a vector the collection does not have — as the server does."""

    def __init__(self, qc):
        self._qc = qc

    def __getattr__(self, name):
        return getattr(self._qc, name)

    async def scroll(self, collection_name, *a, with_vectors=False, **kw):
        if collection_name == IMAGES_COLLECTION and with_vectors and "color_vector" in with_vectors:
            raise RuntimeError("Wrong input: Not existing vector name error: color_vector")
        return await self._qc.scroll(collection_name, *a, with_vectors=with_vectors, **kw)


@pytest.fixture()
def strict(db):
    db._qc = db._qc_bulk = _ServerStrict(db._qc)
    return db


async def _picture(db, s: str, name: str, **payload) -> None:
    await db.upsert_new(s, {"sha256": s, "name": name, **payload})


async def _colour_row(db, s: str):
    pts = await db._qc.retrieve(IMAGES_COLOR_COLLECTION, ids=[sha256_to_point_id(s)],
                                with_payload=True, with_vectors=True)
    return pts[0] if pts else None


# ── the 3D colour space ──────────────────────────────────────────────────────

async def test_the_colour_space_reads_the_colour_collection(strict):
    await _picture(strict, A, "a.png")
    await strict.set_color_vector(A, [50.0, 10.0, -5.0], {"palette_hex": ["#ff0000"]})

    assert await strict.scroll_color_lab_points() == [
        {"sha256": A, "L": 50.0, "a": 10.0, "b": -5.0, "hex": "#ff0000", "name": "a.png"},
    ]


async def test_a_colour_row_whose_picture_is_gone_is_left_out(strict):
    await _picture(strict, A, "a.png")
    await strict.set_color_vector(A, [50.0, 0.0, 0.0])
    await strict.set_color_vector(GONE, [20.0, 0.0, 0.0])   # orphan: no picture

    assert [p["sha256"] for p in await strict.scroll_color_lab_points()] == [A]


async def test_a_picture_with_colour_only_on_its_own_payload_still_shows(strict):
    """Written before the colour collection existed."""
    await _picture(strict, B, "b.png", color_lab=[70.0, 1.0, 2.0], palette_hex=["#00ff00"])

    assert await strict.scroll_color_lab_points() == [
        {"sha256": B, "L": 70.0, "a": 1.0, "b": 2.0, "hex": "#00ff00", "name": "b.png"},
    ]


async def test_a_picture_in_both_places_is_counted_once_from_the_collection(strict):
    await _picture(strict, A, "a.png", color_lab=[1.0, 1.0, 1.0])     # stale payload copy
    await strict.set_color_vector(A, [50.0, 10.0, -5.0])

    got = await strict.scroll_color_lab_points()
    assert len(got) == 1 and got[0]["L"] == 50.0


async def test_the_limit_holds_across_both_sources(strict):
    await _picture(strict, A, "a.png")
    await strict.set_color_vector(A, [50.0, 0.0, 0.0])
    await _picture(strict, B, "b.png", color_lab=[70.0, 0.0, 0.0])
    await _picture(strict, C, "c.png", color_lab=[80.0, 0.0, 0.0])

    assert len(await strict.scroll_color_lab_points(limit=2)) == 2


# ── the repair at the end of the colour backfill ─────────────────────────────

async def test_a_real_colour_is_left_alone_with_its_payload(strict):
    await _picture(strict, A, "a.png")
    await strict.set_color_vector(A, [50.0, 10.0, -5.0],
                                  {"palette_hex": ["#ff0000"], "avg_saturation": 0.4})

    assert await strict.recover_missing_color_vectors() == 0

    row = await _colour_row(strict, A)
    assert row.vector["color_vector"] == [50.0, 10.0, -5.0]
    assert row.payload == {"palette_hex": ["#ff0000"], "avg_saturation": 0.4}


async def test_a_zero_colour_is_rebuilt_from_its_palette_and_keeps_its_payload(strict):
    await _picture(strict, A, "a.png")
    await strict.set_color_vector(A, [0.0, 0.0, 0.0],
                                  {"palette_hex": ["#ffffff"], "avg_saturation": 0.0})

    assert await strict.recover_missing_color_vectors() == 1

    row = await _colour_row(strict, A)
    L, a, b = row.vector["color_vector"]
    assert L == pytest.approx(100.0, abs=0.5) and abs(a) < 1 and abs(b) < 1, "white"
    assert row.payload == {"palette_hex": ["#ffffff"], "avg_saturation": 0.0}


async def test_a_zero_colour_with_no_palette_is_left_for_the_backfill(strict):
    await _picture(strict, A, "a.png")
    await strict.set_color_vector(A, [0.0, 0.0, 0.0])

    assert await strict.recover_missing_color_vectors() == 0
