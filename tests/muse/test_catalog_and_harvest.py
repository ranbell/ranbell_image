import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest
from unittest.mock import MagicMock, AsyncMock

from app.muse import catalog, harvest


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


@pytest.mark.asyncio
async def test_harvest_read_tags(monkeypatch):
    """Test harvest.read_tags tags extraction from image bytes."""
    async def mock_tags_scored(img_bytes, threshold, model_dir=None):
        return [
            ("1girl", 0.95, 0),
            ("solo", 0.90, 0),
            ("silver_hair", 0.85, 0),
            ("explicit_rating", 0.99, 4), # Rating tag
        ]

    monkeypatch.setattr("app.muse.harvest.tags_scored_from_bytes", mock_tags_scored)

    res = await harvest.read_tags(
        b"fake_image_bytes",
        threshold=0.3,
        drop_rating_tags=True,
    )
    assert isinstance(res, str)
    assert "1girl" in res
    assert "silver_hair" in res
