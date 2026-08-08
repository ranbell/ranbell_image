import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.muse import runner, session_db


class FakeDb:
    def __init__(self):
        self._qc = MagicMock()
        self._qc.upsert = AsyncMock()
        self._qc.delete = AsyncMock()
        self._qc.search = AsyncMock(return_value=[])
        self._qc.scroll = AsyncMock(return_value=([], None))
        self._qc.retrieve = AsyncMock(return_value=[MagicMock(payload={"session_id": "sess_123", "inputs": {"workflow": "wf1", "draft_count": 2}, "board": {"prompt": "1girl", "still": True}, "shoot": {"prompt": "1girl"}})])


@pytest.mark.asyncio
async def test_session_db_operations():
    """Test session_db save, load, delete, attach, and finish helpers."""
    db = FakeDb()
    session = {"session_id": "sess_123", "character_preset": "c001"}

    # 1. Save
    saved = await session_db.save(db, session)
    assert saved["session_id"] == "sess_123"

    # 2. Load
    loaded = await session_db.load(db, "sess_123")
    assert loaded is not None

    # 3. Delete
    await session_db.delete(db, "sess_123")

    # 4. Attach board image
    await session_db.attach_board_image(db, "sess_123", "sha256_hash", {"meta": 1})

    # 5. Finish board
    await session_db.finish_board(db, "sess_123", error="")

    # 6. Attach shoot image
    await session_db.attach_shoot_image(db, "sess_123", "sha256_hash_2", {"meta": 2})

    # 7. Finish shoot
    await session_db.finish_shoot(db, "sess_123", error="")


@pytest.mark.asyncio
async def test_runner_jobs(monkeypatch):
    """Test runner run_board_job and run_shoot_job execution."""
    db = FakeDb()
    fake_comfy = MagicMock()

    mock_render_module = MagicMock()
    mock_render_module.run_render = AsyncMock(return_value={"shas": ["sha_abc123"]})
    sys.modules["app.jobs.render"] = mock_render_module

    # 1. Board job
    res = await runner.run_board_job(MagicMock(), MagicMock(), db=db, comfy=fake_comfy, session_id="sess_123")
    assert res["shas"] == ["sha_abc123"]

    # 2. Shoot job
    res = await runner.run_shoot_job(MagicMock(), MagicMock(), db=db, comfy=fake_comfy, session_id="sess_123")
    assert res["shas"] == ["sha_abc123"]

    # 3. Legacy draft job
    res = await runner.run_draft_job(MagicMock(), MagicMock(), db=db, comfy=fake_comfy, session_id="sess_123")
    assert res["shas"] == ["sha_abc123"]


def test_runner_preview_publisher_and_finished_image():
    """Test preview_publisher and finished_image helpers."""
    pub = runner.preview_publisher("sess_123", "test_label")
    assert callable(pub)
    
    sha = runner.finished_image(["sha_1", "sha_2", "sha_3"])
    assert sha == "sha_3"
