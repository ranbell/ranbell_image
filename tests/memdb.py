"""A real `QdrantDBClient` on an in-memory Qdrant — shared by tests/db and tests/api.

qdrant-client's local mode is the same query engine without a server, so tests
exercise the real filters, deletes and aliases rather than a stand-in. `start()`
runs as it does at boot, so every collection exists as it would. Payload indexes
are a no-op locally (it warns); nothing depends on them.

It is more lenient than a server in one known way: it does not refuse a vector
name the collection lacks. `tests/db/test_colour_is_read_where_it_lives.py`
shows how to give that refusal back where it matters.
"""
from __future__ import annotations

import sys
import warnings
from contextlib import asynccontextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qdrant_client import AsyncQdrantClient  # noqa: E402

from backend.app.db.qdrant_client import QdrantDBClient  # noqa: E402


@asynccontextmanager
async def memory_db():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        client = QdrantDBClient()
        mem = AsyncQdrantClient(location=":memory:")
        client._qc = client._qc_bulk = mem
        await client.start()
    try:
        yield client
    finally:
        await mem.close()


def sha(c: str) -> str:
    """A readable fake sha256: 'a' → 'aaaa…' (64). Must be a hex character."""
    return c * 64
