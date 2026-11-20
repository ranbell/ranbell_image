"""**A search answers what was asked, nearest first, inside its filter.** (2026-11-21)

Search is where a mistake is silent: a wrong result is still a screen full of
pictures. These pin the promises the gallery makes — the nearest picture comes
first, a tag filter is a hard limit even when an untagged picture is nearer,
"similar to this" never offers the picture itself, and a picture with no
embedding is never an answer — on both the two-phase (MRL) path and the
single-phase one.
"""
from __future__ import annotations

import pytest

from tests.db.conftest import sha

pytestmark = [pytest.mark.asyncio, pytest.mark.filterwarnings("ignore::UserWarning")]

A, B, C, D = sha("a"), sha("b"), sha("c"), sha("d")


def _vec(dim: int, *weights: float) -> list[float]:
    """A vector in the first few axes; the rest zero. Cosine reads the angle."""
    return [*weights, *([0.0] * (dim - len(weights)))]


async def _picture(db, s: str, vec: list[float] | None, tags: list[str] = ()) -> None:
    await db.upsert_new(s, {"sha256": s, "path": f"/x/{s[0]}.png", "wd14_tags": list(tags)})
    if vec is not None:
        await db.set_embedding(s, vec)


@pytest.fixture(params=[True, False], ids=["two-phase", "single-phase"])
def mrl(request, db):
    db.has_mrl = request.param
    return db


async def _library(db) -> list[float]:
    """A is the query's twin, B close, C far; only B and C are tagged `cat`."""
    d = db.embed_dim
    await _picture(db, A, _vec(d, 1.0, 0.0), tags=["dog"])
    await _picture(db, B, _vec(d, 0.9, 0.3), tags=["cat"])
    await _picture(db, C, _vec(d, 0.0, 1.0), tags=["cat", "night"])
    await _picture(db, D, None, tags=["cat"])           # not embedded yet
    return _vec(d, 1.0, 0.0)


def _order(results: list[dict]) -> list[str]:
    return [r["sha256"] for r in results]


async def test_nearest_comes_first(mrl):
    q = await _library(mrl)
    got = await mrl.search_vector(q, 10)
    assert _order(got) == [A, B, C]
    assert got[0]["_score"] >= got[1]["_score"] >= got[2]["_score"]


async def test_a_picture_with_no_embedding_is_never_an_answer(mrl):
    q = await _library(mrl)
    assert D not in _order(await mrl.search_vector(q, 10))
    assert D not in _order(await mrl.search_vector_tag_or(q, ["cat"]))


async def test_a_tag_filter_holds_even_when_an_untagged_picture_is_nearer(mrl):
    q = await _library(mrl)
    assert _order(await mrl.search_vector(q, 10, tag="cat")) == [B, C]


async def test_the_result_count_is_respected(mrl):
    q = await _library(mrl)
    assert _order(await mrl.search_vector(q, 1)) == [A]


async def test_tag_or_takes_any_listed_tag_and_an_excluded_tag_wins(mrl):
    q = await _library(mrl)
    assert _order(await mrl.search_vector_tag_or(q, ["dog", "cat"])) == [A, B, C]
    assert _order(await mrl.search_vector_tag_or(q, ["cat"], ["night"])) == [B]


async def test_similar_never_offers_the_picture_itself(mrl):
    await _library(mrl)
    got = _order(await mrl.search_similar(A, 10))
    assert A not in got
    assert got == [B, C]


async def test_similar_to_an_unembedded_or_unknown_picture_is_empty(mrl):
    await _library(mrl)
    assert await mrl.search_similar(D) == []
    assert await mrl.search_similar(sha("e")) == []   # never registered


async def test_a_wrong_width_embedding_is_refused_not_stored(db):
    await db.upsert_new(A, {"sha256": A})
    with pytest.raises(ValueError, match="dimension mismatch"):
        await db.set_embedding(A, [0.1] * (db.embed_dim - 1))
