"""**A copy that is still moving is allowed to take as long as it takes.** (2026-11-02)

The Showrunner, after an adoption failed twice on 17,985 points: "with anything
bigger than this, would the current timeout mechanism not fail again? Watch
whether it is making progress, and wait while it is. Better to end it once the
stall has gone on for a set time, no?"

Right. A fixed deadline cannot tell a slow copy from a stuck one, so it fails the
bigger library for being big, and raising the number only moves where the next
one dies. Progress decides instead: every call that returns resets the clock.

Retrying is safe because every wrapped call is addressed by point id — sending
the same batch again lands the same points, including when the first attempt did
land and only the answer was lost.

And only the connection giving up is retried. Retrying anything else would spend
the whole stall window on a failure that was never going to clear, and bury the
real error five minutes deep.
"""
import asyncio
import sys
from pathlib import Path

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.db.qdrant_client import (  # noqa: E402
    IMAGES_COLLECTION,
    QdrantDBClient,
)


def _db() -> QdrantDBClient:
    return QdrantDBClient.__new__(QdrantDBClient)


# ── Waiting while it moves ───────────────────────────────────────────────────


def test_a_connection_that_gives_up_is_tried_again():
    db = _db()
    tries = {"n": 0}

    async def flaky():
        tries["n"] += 1
        if tries["n"] < 3:
            raise ConnectionError("qdrant was busy optimizing")
        return "landed"

    out = asyncio.run(db._retrying(flaky, what="writing", stall_seconds=30))
    assert out == "landed"
    assert tries["n"] == 3, "再試行していない"


def test_every_call_that_returns_resets_the_clock():
    """Ten slow-but-moving batches must not add up to a failure."""
    db = _db()
    done = {"n": 0}

    async def slow_but_moving():
        done["n"] += 1
        if done["n"] % 2:                       # every other attempt stalls once
            raise TimeoutError("read timed out")
        return done["n"]

    async def _many():
        # A window far shorter than the ten batches would need in total: if the
        # clock did not reset per call, this could not finish.
        for _ in range(10):
            await db._retrying(slow_but_moving, what="writing", stall_seconds=1)

    asyncio.run(_many())
    assert done["n"] == 20, "1バッチごとに時計が戻っていない"


def test_a_copy_that_has_stopped_moving_gives_up_and_says_so():
    db = _db()
    tries = {"n": 0}

    async def never():
        tries["n"] += 1
        raise TimeoutError("read timed out")

    with pytest.raises(RuntimeError, match="stopped moving"):
        asyncio.run(db._retrying(never, what="writing into images_v2",
                                 stall_seconds=0.25))
    assert tries["n"] >= 1
    # The window is about the stall, not a fixed number of attempts.
    with pytest.raises(RuntimeError, match="images_v2"):
        asyncio.run(db._retrying(never, what="writing into images_v2",
                                 stall_seconds=0.25))


def test_a_failure_that_will_never_clear_is_not_retried():
    """A collection that is not there will not appear; fail at once."""
    db = _db()
    tries = {"n": 0}

    class Boom(Exception):
        pass

    async def broken():
        tries["n"] += 1
        raise Boom("no such collection")

    with pytest.raises(Boom):
        asyncio.run(db._retrying(broken, what="writing", stall_seconds=30))
    assert tries["n"] == 1, "恒久的な失敗まで再試行している"


def test_cancelling_during_a_retry_stops_it():
    db = _db()

    class _Cancel:
        def raise_if_set(self):
            raise KeyboardInterrupt("cancelled")

    async def never():
        raise TimeoutError("read timed out")

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(db._retrying(never, what="writing", cancel=_Cancel(),
                                 stall_seconds=30))


# ── Naming leftovers for deletion, without ever naming the library ───────────


def _stub_db(*, physical, names, alias=None, counts=None):
    db = _db()
    counts = counts or {}

    async def _resolve():
        return (physical, physical != IMAGES_COLLECTION) if physical else (None, False)

    async def _names():
        return list(names)

    async def _count(name):
        return counts.get(name, 0)

    async def _alias(_name):
        return alias

    db._resolve_images = _resolve
    db._collection_names = _names
    db._point_count = _count
    db._alias_target = _alias
    return db


def test_the_live_collection_is_never_listed_as_stale():
    db = _stub_db(physical="images_v2", alias="images_v2",
                  names=["images_v1", "images_v2"], counts={"images_v1": 5})
    out = asyncio.run(db.stale_rebuild_targets())
    assert [r["collection"] for r in out] == ["images_v1"]
    assert out[0]["points"] == 5, "判断材料の点数が出ていない"


def test_nothing_is_listed_when_images_resolves_nowhere():
    """The newest version IS the library then — the boot restores the alias."""
    db = _stub_db(physical=None, names=["images_v1", "images_v2"])
    assert asyncio.run(db.stale_rebuild_targets()) == []


def test_dropping_refuses_the_collection_images_resolves_to():
    db = _stub_db(physical="images_v2", alias="images_v2",
                  names=["images_v1", "images_v2"])
    with pytest.raises(RuntimeError, match="resolves to"):
        asyncio.run(db.drop_stale_rebuild_target("images_v2"))


def test_dropping_refuses_while_images_resolves_nowhere():
    db = _stub_db(physical=None, names=["images_v1"])
    with pytest.raises(RuntimeError, match="resolves nowhere"):
        asyncio.run(db.drop_stale_rebuild_target("images_v1"))


def test_dropping_refuses_anything_that_is_not_a_versioned_collection():
    db = _stub_db(physical="images_v2", alias="images_v2", names=["images_v2"])
    for name in (IMAGES_COLLECTION, "images_color", "app_config"):
        with pytest.raises(RuntimeError, match="not a versioned"):
            asyncio.run(db.drop_stale_rebuild_target(name))


def test_dropping_a_real_leftover_reports_what_it_took():
    db = _stub_db(physical="images_v2", alias="images_v2",
                  names=["images_v1", "images_v2"], counts={"images_v1": 7})
    dropped = []

    class _QC:
        async def delete_collection(self, name):
            dropped.append(name)

    db._qc = _QC()
    out = asyncio.run(db.drop_stale_rebuild_target("images_v1"))
    assert dropped == ["images_v1"]
    assert out == {"collection": "images_v1", "points": 7}
