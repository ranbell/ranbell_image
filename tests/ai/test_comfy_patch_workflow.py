"""Unit tests for ComfyUIClient.patch_workflow size/steps injection."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.ai.comfy import ComfyUIClient


def _minimal_workflow() -> dict:
    return {
        "1": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "pos", "clip": ["0", 0]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "neg", "clip": ["0", 0]},
        },
        "4": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 1,
                "steps": 28,
                "cfg": 7.0,
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["1", 0],
            },
        },
    }


def test_patch_workflow_width_height_steps():
    client = ComfyUIClient()
    patched = client.patch_workflow(
        _minimal_workflow(),
        positive="a girl",
        negative="bad",
        seed=42,
        width=512,
        height=768,
        steps=12,
        cfg=5.5,
    )
    latent = patched["1"]["inputs"]
    assert latent["width"] == 512
    assert latent["height"] == 768
    sampler = patched["4"]["inputs"]
    assert sampler["steps"] == 12
    assert sampler["cfg"] == 5.5
    assert sampler["seed"] == 42
    assert patched["2"]["inputs"]["text"] == "a girl"
    assert patched["3"]["inputs"]["text"] == "bad"


def test_patch_workflow_without_size_keeps_defaults():
    client = ComfyUIClient()
    patched = client.patch_workflow(
        _minimal_workflow(), positive="x", negative="", seed=7,
    )
    assert patched["1"]["inputs"]["width"] == 1024
    assert patched["1"]["inputs"]["height"] == 1024
    assert patched["4"]["inputs"]["steps"] == 28
