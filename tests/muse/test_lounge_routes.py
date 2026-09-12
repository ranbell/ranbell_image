"""楽屋の入口が、classic 退役のあとも同じ URL で答えること。

`tests/muse/test_api_routes.py` は classic の `muse.api` を土台にしていたので
`private/muse_classic/tests/` へ退いた。けれど楽屋の6本は**生きている** ——
`muse/lounge_api.py` が同じ URL で出している（[[project-muse-circle-must-stay]]）。
退役で落ちたらすぐ分かるように、いちばん短い一本だけ残す。
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
