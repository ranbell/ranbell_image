"""The read caches in front of full-collection Qdrant scrolls.

Written against a real failure: while a heal scan was writing, `GET /api/tags`
timed out mid-scroll and answered 500 — with a perfectly good tag histogram
sitting in memory the whole time.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.api.cache import STALE_RETRY, Cached


async def _boom():
    raise TimeoutError("qdrant read timeout")


@pytest.mark.asyncio
async def test_a_timeout_serves_the_old_value_instead_of_failing():
    cache = Cached(900.0, what="tag histogram")
    cache.data = ["a good histogram"]
    cache.built_at = time.monotonic() - 1200
    cache.due = time.monotonic() - 300  # expired

    assert await cache.get(_boom) == ["a good histogram"]


@pytest.mark.asyncio
async def test_a_failing_rebuild_backs_off_instead_of_retrying_every_request():
    """Otherwise a struggling Qdrant gets a fresh full scroll per request, and
    each one waits out the whole client timeout before answering."""
    cache = Cached(900.0)
    cache.data = ["stale"]
    cache.due = time.monotonic() - 1

    attempts = 0

    async def _counting():
        nonlocal attempts
        attempts += 1
        raise TimeoutError()

    await cache.get(_counting)
    assert attempts == 1
    for _ in range(5):
        assert await cache.get(_counting) == ["stale"]
    assert attempts == 1
    assert 0 < cache.due - time.monotonic() <= STALE_RETRY


@pytest.mark.asyncio
async def test_a_cold_cache_still_raises_rather_than_inventing_an_empty_page():
    with pytest.raises(TimeoutError):
        await Cached(60.0).get(_boom)


@pytest.mark.asyncio
async def test_concurrent_requests_on_a_cold_cache_do_one_scroll_between_them():
    """The old code had no lock, so a lapsed TTL meant every in-flight request
    started its own scroll of the entire collection at the same moment."""
    builds = 0

    async def _slow():
        nonlocal builds
        builds += 1
        await asyncio.sleep(0.05)
        return ["result"]

    cache = Cached(60.0)
    out = await asyncio.gather(*[cache.get(_slow) for _ in range(20)])

    assert builds == 1
    assert all(o == ["result"] for o in out)


@pytest.mark.asyncio
async def test_a_fresh_value_never_touches_the_database():
    builds = 0

    async def _build():
        nonlocal builds
        builds += 1
        return ["v"]

    cache = Cached(60.0)
    for _ in range(3):
        await cache.get(_build)
    assert builds == 1

    # Invalidation after a scan is what forces the next rebuild.
    cache.clear()
    assert cache.data is None
    await cache.get(_build)
    assert builds == 2


@pytest.mark.asyncio
async def test_a_recovered_rebuild_replaces_the_stale_value():
    cache = Cached(60.0)
    cache.data = ["stale"]
    cache.due = time.monotonic() - 1

    async def _ok():
        return ["fresh"]

    assert await cache.get(_ok) == ["fresh"]
    assert cache.due > time.monotonic()
