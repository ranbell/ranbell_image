import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock


class FakeDb:
    def __init__(self):
        self._qc = MagicMock()
        self._qc.upsert = AsyncMock()
        self._qc.search = AsyncMock(return_value=[])
        self._qc.scroll = AsyncMock(return_value=([], None))
        self._qc.retrieve = AsyncMock(return_value=[])


@pytest.fixture
def api_client(tmp_path):
    # Import here, not at module scope: some sibling test directories purge
    # every "app.*" entry from sys.modules during collection (to force a
    # clean re-import of the real app for tests that need it, undoing the
    # stubs tests/api/conftest.py installs). A module-level import above
    # would bind this fixture's router to whichever "app.muse.api" object
    # existed at collection time — a different object than the one
    # monkeypatch.setattr("app.muse.api....", ...) resolves at test-run
    # time, so patches silently miss and the real implementation runs.
    from app.muse.api import router

    app = FastAPI()
    app.include_router(router)

    app.state.db_path = str(tmp_path / "test_muse.db")
    app.state.comfy = MagicMock()
    app.state.spooler = MagicMock()
    app.state.spooler.ollama = MagicMock()
    app.state.ollama = MagicMock()
    app.state.db = FakeDb()
    
    client = TestClient(app)
    return client


def test_api_catalog_and_static_routes(api_client, monkeypatch):
    """Test static metadata routes (catalog, roster, report, steps)."""
    async def mock_catalog(app):
        return {"models": ["v1"], "workflows": ["wf1"]}

    monkeypatch.setattr("app.muse.api.build_muse_catalog", mock_catalog)
    
    # 1. Catalog
    res = api_client.get("/api/muse/catalog")
    assert res.status_code == 200
    assert "models" in res.json()

    # 2. Roster
    res = api_client.get("/api/muse/roster")
    assert res.status_code == 200
    assert "roles" in res.json()

    # 3. Report
    def mock_report(sessions):
        return {"muses": []}
    monkeypatch.setattr("app.muse.report.aggregate", mock_report)
    res = api_client.get("/api/muse/report")
    assert res.status_code == 200

    # 4. Steps
    res = api_client.get("/api/muse/steps")
    assert res.status_code == 200
    assert "steps" in res.json()


def test_api_sessions_all_endpoints(api_client, monkeypatch):
    """Test all session operational endpoints (table, duet, chat, board, approve, finish, draft, refine, report)."""
    fake_session = {
        "id": "sess_api_test_002",
        "session_id": "sess_api_test_002",
        "character_preset": "c001",
        "inputs": {"theme": "cyberpunk", "partner_preset": "c002"},
        "character": {"name": "Minamo"},
        "partner_character": {"name": "Kaho"},
        "chat": [],
        "notes": [],
        "carried_out": [],
        "just_banned": [],
        "just_restored": [],
    }

    async def mock_load(db, sid):
        return fake_session

    async def mock_service_call(*args, **kwargs):
        return fake_session

    monkeypatch.setattr("app.muse.session_db.load", mock_load)
    monkeypatch.setattr("app.muse.service.start_table", mock_service_call)
    monkeypatch.setattr("app.muse.service.start_duet", mock_service_call)
    monkeypatch.setattr("app.muse.service.post_chat", mock_service_call)
    monkeypatch.setattr("app.muse.service.request_board", mock_service_call)
    monkeypatch.setattr("app.muse.service.approve_and_shoot", mock_service_call)
    monkeypatch.setattr("app.muse.service.finish_session", mock_service_call)
    monkeypatch.setattr("app.muse.service.cancel_board", mock_service_call)
    monkeypatch.setattr("app.muse.service.pick_character", mock_service_call)

    # 1. Post character pick
    res = api_client.post("/api/muse/sessions/sess_api_test_002/character", json={"character_id": "c001"})
    assert res.status_code == 200

    # 2. Post table
    res = api_client.post("/api/muse/sessions/sess_api_test_002/table")
    assert res.status_code == 200

    # 3. Post duet
    res = api_client.post("/api/muse/sessions/sess_api_test_002/duet")
    assert res.status_code == 200

    # 4. Post chat
    res = api_client.post("/api/muse/sessions/sess_api_test_002/chat", json={"text": "Hello"})
    assert res.status_code == 200

    # 5. Post board
    res = api_client.post("/api/muse/sessions/sess_api_test_002/board")
    assert res.status_code == 200

    # 6. Post approve
    res = api_client.post("/api/muse/sessions/sess_api_test_002/approve")
    assert res.status_code == 200

    # 7. Post finish
    res = api_client.post("/api/muse/sessions/sess_api_test_002/finish")
    assert res.status_code == 200

    # 8. Post board cancel
    res = api_client.post("/api/muse/sessions/sess_api_test_002/board/cancel")
    assert res.status_code == 200

    # 9. Get session report
    def mock_sess_report(session):
        return {"report": "ok"}
    monkeypatch.setattr("app.muse.report.session_report", mock_sess_report)
    res = api_client.get("/api/muse/sessions/sess_api_test_002/report")
    assert res.status_code == 200


def test_api_session_error_handling(api_client, monkeypatch):
    """Test 404 response on missing session."""
    async def mock_load(db, sid):
        return None

    monkeypatch.setattr("app.muse.session_db.load", mock_load)

    # 404 HTTP Exception for non-existent session
    res = api_client.get("/api/muse/sessions/non_existent")
    assert res.status_code == 404


def test_api_pins_and_releases_one_part_of_the_shot(api_client, monkeypatch):
    """A pinned part is never rewritten by any turn."""
    from app.muse import schema

    session = schema.new_session({
        "theme": "お題", "character_id": "c1", "workflow": "w.json",
        "model": "m", "mode": "duet",
    })
    session["session_id"] = "sess_facet_lock"

    async def mock_load(db, sid):
        return session
    async def mock_save(db, s, **kw):
        return s

    monkeypatch.setattr("app.muse.session_db.load", mock_load)
    monkeypatch.setattr("app.muse.session_db.save", mock_save)

    res = api_client.patch(
        "/api/muse/sessions/sess_facet_lock/facets/camera", json={"locked": True},
    )
    assert res.status_code == 200
    assert res.json()["facets"]["camera"]["locked"] is True

    res = api_client.patch(
        "/api/muse/sessions/sess_facet_lock/facets/camera", json={"locked": False},
    )
    assert res.json()["facets"]["camera"]["locked"] is False

    # A part that does not exist is a client error, not a new part.
    res = api_client.patch(
        "/api/muse/sessions/sess_facet_lock/facets/vibes", json={"locked": True},
    )
    assert res.status_code == 400


def test_lounge_like_toggles(api_client, monkeypatch):
    async def mock_like(_db, thread_id, liked=None):
        return {"id": thread_id, "kind": "pitch", "liked": True if liked is None else liked}

    monkeypatch.setattr("app.muse.lounge_db.set_thread_liked", mock_like)
    res = api_client.post("/api/muse/lounge/threads/p1/like", json={"liked": True})
    assert res.status_code == 200
    assert res.json()["liked"] is True

    res = api_client.post("/api/muse/lounge/threads/p1/like", json={})
    assert res.status_code == 200
