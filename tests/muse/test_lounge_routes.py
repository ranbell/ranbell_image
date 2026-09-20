"""The green room's entrance still answers on the same URLs after classic retired.

`tests/muse/test_api_routes.py` was built on classic's `muse.api`, so it went back
into `private/muse_classic/tests/`. But the green room's six routes are **alive** —
`muse/lounge_api.py` serves them at the same URLs. So that a retirement taking them
down is noticed at once, the shortest of them is kept here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))


@pytest.fixture
def lounge_client(tmp_path):
    # The reason for importing inside the fixture is the same as in classic's api
    # tests: a sibling directory sweeps "app.*" out of sys.modules during collection,
    # so binding at module level makes monkeypatch hit a different object.
    from app.muse.lounge_api import router

    app = FastAPI()
    app.include_router(router)
    app.state.db = MagicMock()
    app.state.db_path = str(tmp_path / "test_muse.db")
    return TestClient(app)


def test_lounge_like_toggles(lounge_client, monkeypatch):
    async def mock_like(_db, thread_id, liked=None):
        return {"id": thread_id, "kind": "pitch",
                "liked": True if liked is None else liked}

    monkeypatch.setattr("app.muse.lounge_db.set_thread_liked", mock_like)

    res = lounge_client.post("/api/muse/lounge/threads/p1/like", json={"liked": True})
    assert res.status_code == 200
    assert res.json()["liked"] is True

    # No body means "toggle". The same behaviour as classic's api.
    res = lounge_client.post("/api/muse/lounge/threads/p1/like", json={})
    assert res.status_code == 200
