"""Gallery cursor pagination must terminate, and must not drop or repeat rows.

Qdrant refuses `offset` together with `order_by`:

    Cannot use an `offset` when using `order_by`. The alternative for paging is
    to use `order_by.start_from` and a filter to exclude the IDs that you've
    already seen for the `order_by.start_from` value

`start_from` is inclusive, so every point sharing the boundary sort value comes
back on the next page. Excluding only the single last-seen id is enough when a
value is unique, and wrong the moment a value repeats: a batch that writes more
than `limit` images with one mtime pins the cursor to that value and the scroll
never advances.

_FakeQC below implements the ordering contract that Qdrant documents, so these
tests fail against a one-id cursor and pass against an accumulating one.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.db.id_utils import sha256_to_point_id
from backend.app.db.qdrant_client import IMAGES_COLLECTION, QdrantDBClient


def _order_value(v):
    """Datetime payloads and datetime start_from values must compare as one type.

    `mtime` is stored as an ISO string and indexed as datetime; qdrant's OrderBy
    model parses a string `start_from` into a datetime, and the server compares
    both as microseconds. Normalising here keeps the fake on the same footing.
    """
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return v
    return v


class _FakeQC:
    """Qdrant's scroll contract for the parts scroll_images() relies on.

    Honours order_by.direction, the inclusive order_by.start_from, and the
    must_not HasIdCondition used to skip already-seen boundary rows. Raises on
    `offset` + `order_by` exactly as the server does, so a regression back to
    offset paging surfaces here instead of in production.
    """

    def __init__(self, docs: list[dict]):
        self.docs = docs
        self.calls = 0

    async def scroll(self, *, collection_name, scroll_filter=None, order_by=None,
                     limit=10, offset=None, with_payload=True, with_vectors=False):
        assert collection_name == IMAGES_COLLECTION
        if offset is not None and order_by is not None:
            raise ValueError("Cannot use an `offset` when using `order_by`")
        self.calls += 1

        rows = list(self.docs)

        excluded_ids: set[str] = set()
        exclude_drafts = False
        for cond in getattr(scroll_filter, "must_not", None) or []:
            has_id = getattr(cond, "has_id", None)
            if has_id:
                excluded_ids.update(str(i) for i in has_id)
            elif getattr(cond, "key", None) == "is_draft":
                exclude_drafts = True
        if exclude_drafts:
            rows = [d for d in rows if not d.get("is_draft")]
        if excluded_ids:
            rows = [d for d in rows
                    if sha256_to_point_id(d["sha256"]) not in excluded_ids]

        key = order_by.key
        descending = str(getattr(order_by.direction, "value", order_by.direction)) == "desc"
        # Ties broken by sha256 so a page boundary is reproducible, matching the
        # stable order a real scroll returns for equal order values.
        rows.sort(key=lambda d: (_order_value(d[key]), d["sha256"]), reverse=descending)

        start_from = _order_value(order_by.start_from)
        if start_from is not None:
            rows = [d for d in rows
                    if (_order_value(d[key]) <= start_from if descending
                        else _order_value(d[key]) >= start_from)]

        return [SimpleNamespace(id=sha256_to_point_id(d["sha256"]), payload=d)
                for d in rows[:limit]], None


def _db(docs: list[dict]) -> QdrantDBClient:
    """A client wired to the fake — __init__ would open a real connection."""
    db = object.__new__(QdrantDBClient)
    db._qc = _FakeQC(docs)
    return db


def _doc(i: int, mtime: str) -> dict:
    # sha256_to_point_id() derives the uuid from the *first* 32 hex chars, so the
    # counter has to vary there — a zero-padded tail would give every doc one id.
    return {"sha256": f"{i:032x}{'a' * 32}", "mtime": mtime, "name": f"img{i:04d}.png",
            "size": 1000 + i, "is_draft": False}


async def _drain(db: QdrantDBClient, *, limit: int, max_pages: int = 200) -> list[dict]:
    """Page until exhausted. Stops early rather than hanging on a stuck cursor."""
    seen: list[dict] = []
    cursor = None
    for _ in range(max_pages):
        docs, cursor = await db.scroll_images(cursor=cursor, limit=limit)
        seen.extend(docs)
        if not cursor:
            return seen
    pytest.fail(
        f"cursor never terminated after {max_pages} pages "
        f"({len(seen)} rows, last cursor={cursor!r})"
    )


@pytest.mark.asyncio
async def test_scroll_images_paginates_through_a_single_shared_mtime():
    """250 images written by one batch share an mtime — 2.5 pages of pure tie."""
    docs = [_doc(i, "2026-08-01T00:00:00+00:00") for i in range(250)]

    got = await _drain(_db(docs), limit=100)

    shas = [d["sha256"] for d in got]
    assert len(shas) == len(set(shas)), "rows repeated across pages"
    assert sorted(shas) == sorted(d["sha256"] for d in docs)


@pytest.mark.asyncio
async def test_scroll_images_accumulates_seen_across_pages_of_one_value():
    """A tie longer than two pages: page 3 must still exclude pages 1 and 2.

    Carrying only the previous page's ids re-serves page 1's rows here.
    """
    docs = [_doc(i, "2026-08-01T00:00:00+00:00") for i in range(90)]

    got = await _drain(_db(docs), limit=30)

    shas = [d["sha256"] for d in got]
    assert len(shas) == len(set(shas)), "rows repeated across pages"
    assert sorted(shas) == sorted(d["sha256"] for d in docs)


@pytest.mark.asyncio
async def test_scroll_images_walks_mixed_ties_and_unique_values():
    """Ties interleaved with unique mtimes, newest-first, across page edges."""
    docs = (
        [_doc(i, "2026-08-03T00:00:00+00:00") for i in range(0, 40)]
        + [_doc(i, f"2026-08-02T00:00:{i % 60:02d}+00:00") for i in range(40, 75)]
        + [_doc(i, "2026-08-01T00:00:00+00:00") for i in range(75, 130)]
    )

    got = await _drain(_db(docs), limit=25)

    shas = [d["sha256"] for d in got]
    assert len(shas) == len(set(shas)), "rows repeated across pages"
    assert sorted(shas) == sorted(d["sha256"] for d in docs)
    mtimes = [d["mtime"] for d in got]
    assert mtimes == sorted(mtimes, reverse=True), "newest-first order broken"


@pytest.mark.asyncio
async def test_scroll_images_excludes_drafts_while_paging():
    docs = [_doc(i, "2026-08-01T00:00:00+00:00") for i in range(60)]
    for d in docs[::4]:
        d["is_draft"] = True

    got = await _drain(_db(docs), limit=25)

    assert sorted(d["sha256"] for d in got) == sorted(
        d["sha256"] for d in docs if not d["is_draft"]
    )


@pytest.mark.asyncio
async def test_scroll_images_last_page_reports_no_cursor():
    docs = [_doc(i, f"2026-08-01T00:00:{i:02d}+00:00") for i in range(10)]

    _, cursor = await _db(docs).scroll_images(cursor=None, limit=50)

    assert cursor is None
