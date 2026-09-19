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
    # fixture の中で import する理由は classic の api テストと同じ: 兄弟の
    # ディレクトリが収集中に "app.*" を sys.modules から掃除するので、
    # module 直下で束ねると monkeypatch が別オブジェクトに当たる。
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

    # 本文なしは「反転」。classic の api と同じ振る舞い。
    res = lounge_client.post("/api/muse/lounge/threads/p1/like", json={})
    assert res.status_code == 200
