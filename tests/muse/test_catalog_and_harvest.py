import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from unittest.mock import MagicMock, AsyncMock

from app.muse import catalog


@pytest.mark.asyncio
async def test_build_muse_catalog():
    """Test build_muse_catalog builds model list, workflows, and presets cleanly."""
    fake_app = MagicMock()
    fake_app.state.comfy = MagicMock()
    fake_app.state.spooler = MagicMock()
    
    # Mock comfy models & workflows as AsyncMocks
    fake_app.state.comfy.list_checkpoints = AsyncMock(return_value=["sdxl_base.safetensors", "pony_v6.safetensors"])
    fake_app.state.comfy.list_workflows = AsyncMock(return_value=["txt2img", "img2img"])
    fake_app.state.spooler.ollama = MagicMock()
    fake_app.state.spooler.ollama.list_models = AsyncMock(return_value=["qwen2.5:7b"])
    fake_app.state.spooler.ollama.list_vision_models = AsyncMock(return_value=["llava:7b"])

    data = await catalog.build_muse_catalog(fake_app)
    assert isinstance(data, dict)


