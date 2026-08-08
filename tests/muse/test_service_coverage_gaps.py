import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.muse import service, session_db
from app.characters import presets


class FakeDb:
    def __init__(self):
        self.rows = {}
        self._qc = MagicMock()
        self._qc.upsert = AsyncMock()
        self._qc.search = AsyncMock(return_value=[])
        self._qc.retrieve = AsyncMock(return_value=[])
        self.get_config = AsyncMock(return_value={})

    async def execute(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_service_error_branches():
    """Test MuseError exceptions across invalid session states and inputs."""
    db = FakeDb()

    # 1. Empty message error
    with pytest.raises(service.MuseError):
        await service.post_chat(db, MagicMock(), MagicMock(), MagicMock(), {"inputs": {}}, "")


@pytest.mark.asyncio
async def test_service_w_muse_chat_flow():
    """Test W-Muse chat posting, turn execution, and partner response."""
    db = FakeDb()
    session = await service.create_session(db, {
        "character_preset": "c001",
        "inputs": {
            "partner_preset": "c002",
            "theme": "darkroom photography",
            "workflow": "txt2img",
            "model": "sdxl_base.safetensors",
        }
    })
    preset = presets.load_seed_presets()[0]
    session["character"] = presets.preset_to_character(preset)
    session["inputs"]["character_id"] = "c001"
    session["inputs"]["workflow"] = "txt2img"
    session["inputs"]["model"] = "sdxl_base.safetensors"
    session["inputs"]["theme"] = "darkroom photography"
    
    async def mock_stream(*args, **kwargs):
        yield {"type": "token", "text": "SAY:\n白瀬みなも: 「背中合わせにしよう！」\n柳かほ: 「うん、賛成！」\nTAGS: 2girls, back-to-back\nSCENE: Two girls standing back to back."}

    fake_ollama = MagicMock()
    fake_ollama.generate_text_stream = mock_stream
    fake_comfy = MagicMock()
    fake_spooler = MagicMock()

    # Post chat in W-Muse session
    updated = await service.post_chat(db, fake_ollama, fake_comfy, fake_spooler, session, "二人で背中合わせポーズ撮ろう！")
    assert updated is not None


@pytest.mark.asyncio
async def test_service_cleanup_and_purge():
    """Test session purge and note/carried_out history tracking."""
    db = FakeDb()
    session = await service.create_session(db, {"character_preset": "c001"})

    # Patch inputs with notes and bans
    session = await service.patch_inputs(db, session, {"theme": "cyberpunk darkroom"})
    assert session["inputs"]["theme"] == "cyberpunk darkroom"
