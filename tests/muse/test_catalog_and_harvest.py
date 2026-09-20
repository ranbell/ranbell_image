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




@pytest.mark.asyncio
async def test_the_catalog_says_which_family_each_workflow_is():
    """**The panel has to be able to say so before the first frame.**
    (2026-09-20)

    The loop already reads every graph for the pose caps, so the family costs no
    extra read — and a graph that fails to parse still gets the filename answer.
    """
    fake_app = MagicMock()
    fake_app.state.comfy = MagicMock()
    fake_app.state.spooler = MagicMock()
    fake_app.state.comfy.list_workflows = MagicMock(
        return_value=["krea2_flux.json", "API_Anima_Hakushi_Fast.json", "odd.json"],
    )
    fake_app.state.comfy.load_workflow = MagicMock(side_effect=[
        {},                                                     # krea2 by name
        {},                                                     # anima by name
        {"3": {"class_type": "KSampler",
               "_meta": {"title": "muse:family=krea2"}}},       # marker wins
    ])
    fake_app.state.comfy.inspect_workflow = MagicMock(return_value={})
    fake_app.state.comfy.is_available = AsyncMock(return_value=True)
    fake_app.state.comfy.list_checkpoints = AsyncMock(return_value=[])
    fake_app.state.spooler.ollama = MagicMock()
    fake_app.state.spooler.ollama.list_models = AsyncMock(return_value=[])
    fake_app.state.spooler.ollama.list_vision_models = AsyncMock(return_value=[])

    data = await catalog.build_muse_catalog(fake_app)
    caps = {c["name"]: c["family"] for c in data["comfyui"]["workflow_caps"]}
    assert caps == {
        "krea2_flux.json": "krea2",
        "API_Anima_Hakushi_Fast.json": "anima",
        "odd.json": "krea2",
    }

    # The numbers the panel prints come from the table, never hardcoded there.
    families = data["image_families"]
    assert families["anima"]["negative"] is True
    assert families["krea2"]["negative"] is False
    assert (families["krea2"]["draft_steps"], families["krea2"]["final_steps"]) == (4, 8)
    assert families["krea2"]["draft_cfg"] is None     # the workflow's own
