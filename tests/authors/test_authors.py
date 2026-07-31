"""Author preset seed / reset helpers."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.authors.seeds import AUTHOR_SEEDS
from app.authors import authors as authors_db


def test_author_seeds_are_usable():
    assert len(AUTHOR_SEEDS) >= 5
    names = [s["name"] for s in AUTHOR_SEEDS]
    assert len(names) == len(set(names))
    for seed in AUTHOR_SEEDS:
        assert seed["name"].strip()
        assert seed["style_description"].strip()


class _FakeQC:
    def __init__(self):
        self.points: dict[str, dict] = {}

    async def scroll(self, *, collection_name, limit=200, offset=None, with_payload=True, scroll_filter=None):
        items = list(self.points.items())
        if scroll_filter is not None:
            # Only support name MatchValue used by find_author_by_name
            want = None
            try:
                cond = scroll_filter.must[0]
                want = cond.match.value
            except Exception:
                want = None
            if want is not None:
                items = [(i, p) for i, p in items if p.get("name") == want]
        start = 0
        if offset is not None:
            start = int(offset)
        chunk = items[start : start + limit]
        next_off = start + limit if start + limit < len(items) else None
        out = []
        for pid, payload in chunk:
            out.append(SimpleNamespace(id=pid, payload=payload if with_payload else None))
        return out, next_off

    async def retrieve(self, *, collection_name, ids, with_payload=True):
        out = []
        for i in ids:
            if i in self.points:
                out.append(SimpleNamespace(id=i, payload=self.points[i]))
        return out

    async def upsert(self, *, collection_name, points):
        for p in points:
            self.points[str(p.id)] = dict(p.payload or {})

    async def set_payload(self, *, collection_name, payload, points):
        for pid in points.points:
            self.points[str(pid)].update(payload)

    async def delete(self, *, collection_name, points_selector):
        for pid in points_selector.points:
            self.points.pop(str(pid), None)


class _FakeDB:
    def __init__(self):
        self._qc = _FakeQC()


def test_seed_if_empty_then_noop_then_reset():
    db = _FakeDB()

    async def run():
        n1 = await authors_db.seed_authors_if_empty(db, vector_dim=8)
        assert n1 == len(AUTHOR_SEEDS)
        assert len(db._qc.points) == len(AUTHOR_SEEDS)

        n2 = await authors_db.seed_authors_if_empty(db, vector_dim=8)
        assert n2 == 0
        assert len(db._qc.points) == len(AUTHOR_SEEDS)

        # custom add
        await authors_db.create_author(
            db,
            name="カスタム作家",
            style_description="独自の文体",
            genre_tag="custom",
            vector_dim=8,
        )
        assert len(db._qc.points) == len(AUTHOR_SEEDS) + 1

        result = await authors_db.reset_authors_to_defaults(db, vector_dim=8)
        assert result["deleted"] == len(AUTHOR_SEEDS) + 1
        assert result["inserted"] == len(AUTHOR_SEEDS)
        assert len(db._qc.points) == len(AUTHOR_SEEDS)
        names = {p["name"] for p in db._qc.points.values()}
        assert "カスタム作家" not in names
        assert AUTHOR_SEEDS[0]["name"] in names

    asyncio.run(run())
