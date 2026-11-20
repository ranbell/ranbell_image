"""A real `QdrantDBClient` on an in-memory Qdrant.

qdrant-client's local mode is the same query engine without a server, so these
tests exercise the real filters, deletes and aliases rather than a stand-in.
`start()` runs as it does at boot, so every collection exists as it would.
Payload indexes are a no-op locally (it warns); nothing here depends on them.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import pytest_asyncio

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qdrant_client import AsyncQdrantClient  # noqa: E402

from backend.app.db.qdrant_client import QdrantDBClient  # noqa: E402


@pytest_asyncio.fixture()
async def db():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        client = QdrantDBClient()
        mem = AsyncQdrantClient(location=":memory:")
        client._qc = client._qc_bulk = mem
        await client.start()
    yield client
    await mem.close()


def sha(c: str) -> str:
    """A readable fake sha256: 'a' → 'aaaa…' (64)."""
    return c * 64
