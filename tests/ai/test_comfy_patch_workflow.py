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


def _workflow_with_orphan_latent() -> dict:
    """Connected EmptyLatent (1) + unused EmptyLatent (99) that must NOT change."""
    wf = _minimal_workflow()
    wf["99"] = {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 2048, "height": 2048, "batch_size": 1},
    }
    return wf


def _workflow_with_primitive_size() -> dict:
    """EmptyLatent width/height wired from PrimitiveNode (common custom graphs)."""
    return {
        "10": {
            "class_type": "PrimitiveNode",
            "inputs": {"value": 1024},
        },
        "11": {
            "class_type": "PrimitiveNode",
            "inputs": {"value": 1536},
        },
        "1": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": ["10", 0],
                "height": ["11", 0],
                "batch_size": 1,
            },
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


def test_patch_workflow_only_connected_latent_not_orphan():
    """Width/height must follow KSampler.latent_image — unused EmptyLatent stays put."""
    client = ComfyUIClient()
    patched = client.patch_workflow(
        _workflow_with_orphan_latent(),
        positive="x", negative="",
        width=512, height=512, steps=10,
    )
    assert patched["1"]["inputs"]["width"] == 512
    assert patched["1"]["inputs"]["height"] == 512
    # orphan node 99 must remain untouched
    assert patched["99"]["inputs"]["width"] == 2048
    assert patched["99"]["inputs"]["height"] == 2048


def test_find_latent_nodes_via_ksampler():
    ids = ComfyUIClient._find_latent_nodes_via_ksampler(_minimal_workflow())
    assert ids == ["1"]
    ids2 = ComfyUIClient._find_latent_nodes_via_ksampler(_workflow_with_orphan_latent())
    assert ids2 == ["1"]
    assert "99" not in ids2


def test_patch_workflow_primitive_wired_width_height():
    client = ComfyUIClient()
    patched = client.patch_workflow(
        _workflow_with_primitive_size(),
        positive="x", negative="",
        width=512, height=768,
    )
    # Primitive values updated (wires kept)
    assert patched["10"]["inputs"]["value"] == 512
    assert patched["11"]["inputs"]["value"] == 768
    # EmptyLatent still points at those primitives
    assert patched["1"]["inputs"]["width"] == ["10", 0]
    assert patched["1"]["inputs"]["height"] == ["11", 0]


def test_patch_load_image_nodes():
    client = ComfyUIClient()
    wf = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "x"}},
        "3": {"class_type": "LoadImageMask", "inputs": {"image": "mask.png"}},
        "4": {"class_type": "LoadImageOutput", "inputs": {"image": "out.png"}},
        "5": {"class_type": "Image Load", "inputs": {"image": "custom.png"}},
    }
    patched, n = client.patch_load_image_nodes(wf, "ref_image.png")
    assert n == 4
    assert patched["1"]["inputs"]["image"] == "ref_image.png"
    assert patched["3"]["inputs"]["image"] == "ref_image.png"
    assert patched["4"]["inputs"]["image"] == "ref_image.png"
    assert patched["5"]["inputs"]["image"] == "ref_image.png"
    assert wf["1"]["inputs"]["image"] == "old.png"  # original untouched


def test_upload_image_subfolder_form():
    """Comfy upload returns subfolder/name for LoadImage.inputs.image."""
    import asyncio

    client = ComfyUIClient()

    class _Resp:
        content = b'{"name":"ref_image.png","subfolder":"input/chr","type":"input"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "name": "ref_image.png",
                "subfolder": "input/chr",
                "type": "input",
            }

    class _Http:
        async def post(self, *a, **k):
            return _Resp()

    client._http = _Http()
    out = asyncio.run(client.upload_image(b"png", "ref_image.png"))
    assert out == "input/chr/ref_image.png"


def test_append_negative_ignores_a_wired_input():
    """A linked negative used to be stringified into the prompt as "['99', 0]"."""
    client = ComfyUIClient.__new__(ComfyUIClient)
    wf = _minimal_workflow()
    # Negative text comes from another node instead of being a literal.
    wf["3"]["inputs"]["text"] = ["99", 0]

    out = client.patch_workflow(wf, "pos tags", "lowres, worst quality", append_negative=True)
    text = out["3"]["inputs"]["text"]

    assert isinstance(text, str)
    assert text == "lowres, worst quality"
    assert "99" not in text


def test_append_negative_still_extends_a_literal():
    client = ComfyUIClient.__new__(ComfyUIClient)
    out = client.patch_workflow(
        _minimal_workflow(), "pos", "lowres", append_negative=True,
    )
    assert out["3"]["inputs"]["text"] == "neg, lowres"
