"""The `db` fixture: a real `QdrantDBClient` on an in-memory Qdrant (see tests/memdb.py)."""
from __future__ import annotations

import pytest_asyncio

from tests.memdb import memory_db, sha  # noqa: F401 — sha is imported from here by the tests


@pytest_asyncio.fixture()
async def db():
    async with memory_db() as client:
        yield client
