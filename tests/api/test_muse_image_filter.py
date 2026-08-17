""""Every photo of her" as a gallery query.

Every Muse render has carried `character_id`, `partner_character_id`,
`muse_stage` and `muse_session_id` since `muse/runner.py`'s
`_character_payload_extra` — verified on a live image off the server. They were
simply never indexed and never reachable from `/api/images`, so the one thing
you would most want to ask of a studio's gallery was the one thing it could not
answer.

These tests hold the filter shape rather than the transport: the conditions the
query builder emits, and the fact that a Muse filter counts as a filter at all
(miss that and the request falls through to the unfiltered scroll and quietly
returns the whole library).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from qdrant_client import models as qm

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.api.images import list_images  # noqa: E402
from backend.app.db.qdrant_client import (  # noqa: E402
    GALLERY_PAYLOAD_FIELDS, QdrantDBClient,
)

MIO = "38814c43-824f-5a42-96ca-e3afc00f76cf"


def _db() -> QdrantDBClient:
    """No connection — only the pure filter builder is under test."""
    return object.__new__(QdrantDBClient)


def _keys(conditions) -> list[str]:
    return [getattr(c, "key", "") for c in (conditions or [])]


def test_asking_for_one_muse_filters_on_her_id():
    f = _db()._make_filter(character_id=MIO)
    assert "character_id" in _keys(f.must)
    cond = next(c for c in f.must if getattr(c, "key", "") == "character_id")
    assert cond.match.value == MIO


def test_the_partner_seat_is_only_included_when_asked():
    """A two-Muse take files the second girl under her own key.

    Default off: 「みおの写真」 usually means the ones she led. Asked for, it
    has to be an OR — an AND of the two ids matches nothing at all.
    """
    solo = _db()._make_filter(character_id=MIO)
    assert "partner_character_id" not in _keys(solo.must)

    both = _db()._make_filter(character_id=MIO, include_partner=True)
    nested = [c for c in both.must if isinstance(c, qm.Filter)]
    assert nested, "an OR needs a nested filter, not two musts"
    assert _keys(nested[0].should) == ["character_id", "partner_character_id"]
    assert not nested[0].must, "a partner match must not also require the lead"


def test_stage_and_session_narrow_without_disturbing_the_rest():
    f = _db()._make_filter(
        character_id=MIO, muse_stage="shoot", muse_session_id="s1",
        tags_include=["coat"], star_min=4,
    )
    keys = _keys(f.must)
    assert {"character_id", "muse_stage", "muse_session_id",
            "wd14_tags", "star_rating"} <= set(keys)


def test_a_muse_filter_composes_with_the_searches_already_there():
    """The whole point of reusing `_make_filter`: nothing is a special case."""
    f = _db()._make_filter(
        character_id=MIO, keyword="rooftop", models=["nyaIris.safetensors"],
        tags_exclude=["monochrome"], category="AI",
    )
    assert {"character_id", "positive_prompt", "model_name",
            "batch_category"} <= set(_keys(f.must))
    assert "wd14_tags" in _keys(f.must_not)


def test_no_muse_filter_leaves_the_query_exactly_as_it_was():
    plain = _db()._make_filter(tags_include=["coat"])
    assert "character_id" not in _keys(plain.must)
    assert "muse_stage" not in _keys(plain.must)


def test_drafts_stay_excluded_by_default_for_a_muse_query():
    """Board sketches are `is_draft`. Her gallery is finals unless asked."""
    f = _db()._make_filter(character_id=MIO)
    assert "is_draft" in _keys(f.must_not)


def test_the_grid_is_handed_enough_to_name_who_it_is_showing():
    assert "character_id" in GALLERY_PAYLOAD_FIELDS
    assert "character_name" in GALLERY_PAYLOAD_FIELDS
    assert "muse_stage" in GALLERY_PAYLOAD_FIELDS


# ── the endpoint has to treat it AS a filter ────────────────────────────────

class _RecordingDb:
    """Records which scroll the endpoint chose, and with what."""

    def __init__(self):
        self.filtered_kwargs: dict | None = None
        self.unfiltered = False

    async def scroll_filtered_page(self, **kw):
        self.filtered_kwargs = kw
        return [], None, 0

    async def scroll_images(self, **kw):
        self.unfiltered = True
        return [], None

    async def total_count(self, **kw):
        return 0


def _request(db):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=db)))


@pytest.mark.asyncio
async def test_a_character_query_does_not_fall_through_to_the_whole_library():
    """`is_filter` decides the branch. Miss it and 「みおの写真」 returns everything."""
    db = _RecordingDb()
    await list_images(_request(db), character_id=MIO)

    assert not db.unfiltered, "a Muse query took the unfiltered path"
    assert db.filtered_kwargs is not None
    assert db.filtered_kwargs["character_id"] == MIO


@pytest.mark.asyncio
async def test_stage_alone_is_a_filter_too():
    db = _RecordingDb()
    await list_images(_request(db), muse_stage="shoot")
    assert not db.unfiltered
    assert db.filtered_kwargs["muse_stage"] == "shoot"


@pytest.mark.asyncio
async def test_an_unknown_stage_is_dropped_rather_than_matching_nothing():
    db = _RecordingDb()
    await list_images(_request(db), muse_stage="nonsense")
    assert db.unfiltered, "a meaningless stage must not silently empty the gallery"


@pytest.mark.asyncio
async def test_a_plain_gallery_request_is_untouched():
    db = _RecordingDb()
    await list_images(_request(db))
    assert db.unfiltered
    assert db.filtered_kwargs is None


# ── the count above the grid has to be the real one ─────────────────────────

class _CountingQC:
    """Records how the count was asked for; scroll returns nothing."""

    def __init__(self):
        self.exact = None

    async def count(self, **kw):
        self.exact = kw.get("exact")
        return SimpleNamespace(count=0)

    async def scroll(self, **kw):
        return [], None


# ── the chip row ────────────────────────────────────────────────────────────

class _FacetQC:
    def __init__(self, payloads):
        self.payloads = payloads
        self.scroll_filter = None

    async def scroll(self, **kw):
        self.scroll_filter = kw.get("scroll_filter")
        return [SimpleNamespace(payload=p) for p in self.payloads], None


def _img(cid, name, stage):
    return {"character_id": cid, "character_name": name, "muse_stage": stage}


@pytest.mark.asyncio
async def test_each_muse_is_counted_with_her_finals_kept_apart():
    """The chip has to say the number the click will actually produce.

    With 「試し撮りも」 off the grid shows finals only, so a chip advertising
    the combined total sends you to a grid a fraction of its size.
    """
    db = _db()
    db._qc = _FacetQC([
        _img(MIO, "各務 みお", "shoot"),
        _img(MIO, "各務 みお", "shoot"),
        _img(MIO, "各務 みお", "board"),
        _img(MIO, "各務 みお", "still"),
        _img("asahi", "倉田 あさひ", "board"),
    ])

    rows = await db.scroll_character_facets()

    mio = next(r for r in rows if r["character_id"] == MIO)
    assert (mio["name"], mio["shoot"], mio["board"], mio["count"]) == (
        "各務 みお", 2, 2, 4,
    ), "a still is a test frame, not a final"
    assert rows[0]["character_id"] == MIO, "busiest first"


@pytest.mark.asyncio
async def test_an_image_with_no_cast_stamped_on_it_is_not_guessed_at():
    db = _db()
    db._qc = _FacetQC([
        _img(MIO, "各務 みお", "shoot"),
        {"muse_stage": "shoot"},          # rendered before the cast was stamped
        {"character_id": "", "muse_stage": "board"},
    ])

    rows = await db.scroll_character_facets()

    assert len(rows) == 1 and rows[0]["count"] == 1


@pytest.mark.asyncio
async def test_the_facet_scroll_only_walks_the_studios_own_renders():
    """10,630 images, 551 of them ours. Walking the rest is wasted work."""
    db = _db()
    db._qc = _FacetQC([])

    await db.scroll_character_facets()

    keys = [getattr(c, "key", "") for c in (db._qc.scroll_filter.must or [])]
    assert keys == ["muse_stage"]


@pytest.mark.asyncio
async def test_a_filtered_total_is_counted_exactly():
    """Sampling is worst exactly where a filter is most useful.

    Measured on the live collection (10,630 images): `star_min=4` reported a
    total of 0 for a real 1, and one Muse's photos reported 2 for a real 176.
    A grid that says "2" over 176 rows is worse than no number.
    """
    db = _db()
    db._qc = _CountingQC()

    await db.scroll_filtered_page(character_id=MIO, limit=10)

    assert db._qc.exact is True
