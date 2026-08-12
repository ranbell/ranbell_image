"""OpenPose workflow inspect + direction-still injection helpers."""
from __future__ import annotations

import asyncio
import base64

from backend.app.ai.comfy import ComfyUIClient
from backend.app.muse import runner as muse_runner
from backend.app.muse.schema import public_view


def _openpose_graph() -> dict:
    """LoadImage → DWPreprocessor → ControlNetApplyAdvanced (minimal)."""
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
        "20": {
            "class_type": "DWPreprocessor",
            "inputs": {"image": ["10", 0], "detect_hand": "enable"},
        },
        "30": {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": "openpose.safetensors"},
        },
        "40": {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {
                "positive": ["1", 0],
                "negative": ["2", 0],
                "control_net": ["30", 0],
                "image": ["20", 0],
            },
        },
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "pos"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "neg"}},
    }


def _controlnet_only_graph() -> dict:
    """ControlNet without a pose preprocessor — must NOT auto-inject."""
    return {
        "10": {"class_type": "LoadImage", "inputs": {"image": "skeleton.png"}},
        "30": {
            "class_type": "ControlNetLoader",
            "inputs": {"control_net_name": "openpose.safetensors"},
        },
        "40": {
            "class_type": "ControlNetApply",
            "inputs": {
                "conditioning": ["1", 0],
                "control_net": ["30", 0],
                "image": ["10", 0],
            },
        },
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "pos"}},
    }


def test_inspect_workflow_openpose_injectable():
    client = ComfyUIClient.__new__(ComfyUIClient)
    info = client.inspect_workflow(_openpose_graph())
    assert info["has_openpose"] is True
    assert info["has_pose_preprocessor"] is True
    assert info["can_inject_image"] is True
    assert info["image_node_id"] == "10"


def test_inspect_workflow_controlnet_only_no_inject():
    client = ComfyUIClient.__new__(ComfyUIClient)
    info = client.inspect_workflow(_controlnet_only_graph())
    assert info["has_openpose"] is True
    assert info["has_pose_preprocessor"] is False
    assert info["can_inject_image"] is False
    assert info["image_node_id"] is None


def test_inspect_workflow_plain_txt2img():
    client = ComfyUIClient.__new__(ComfyUIClient)
    wf = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "a"}},
        "2": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512}},
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 1, "steps": 20, "cfg": 7,
                "positive": ["1", 0], "negative": ["1", 0],
                "latent_image": ["2", 0],
            },
        },
    }
    info = client.inspect_workflow(wf)
    assert info["has_openpose"] is False
    assert info["can_inject_image"] is False


def test_patch_load_image_node_targeted():
    client = ComfyUIClient.__new__(ComfyUIClient)
    wf = {
        "10": {"class_type": "LoadImage", "inputs": {"image": "a.png"}},
        "11": {"class_type": "LoadImage", "inputs": {"image": "b.png"}},
    }
    out = client.patch_load_image_node(wf, "10", "muse_direction.jpg")
    assert out["10"]["inputs"]["image"] == "muse_direction.jpg"
    assert out["11"]["inputs"]["image"] == "b.png"
    assert wf["10"]["inputs"]["image"] == "a.png"


def test_apply_openpose_reference_uploads_and_patches():
    client = ComfyUIClient.__new__(ComfyUIClient)

    class _Http:
        pass

    async def _upload(data, filename, **kw):
        assert data.startswith(b"\xff\xd8") or len(data) > 0
        return "input/muse_direction.jpg"

    client.upload_image = _upload  # type: ignore
    wf = _openpose_graph()
    patched, info = asyncio.run(
        client.apply_openpose_reference(wf, b"\xff\xd8fakejpeg", filename="x.jpg")
    )
    assert info["injected"] is True
    assert info["comfy_name"] == "input/muse_direction.jpg"
    assert patched["10"]["inputs"]["image"] == "input/muse_direction.jpg"


def test_store_and_public_view_redacts_jpeg():
    session = {
        "session_id": "s1",
        "inputs": {},
        "character": {},
        "status": "chat",
        "mode": "duet",
    }
    jpeg = base64.b64decode(
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
        "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
        "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
        "MjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAA"
        "AAAAAAAAAv/EABQBAQAAAAAAAAAAAAAAAAAAAAD/xAAUEQEAAAAAAAAAAAAAAAAA"
        "AAAA/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQMRAD8AfwD/2Q=="
    )
    # Use a real tiny jpeg from PIL if available
    try:
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="JPEG")
        jpeg = buf.getvalue()
    except Exception:
        pass
    muse_runner.store_direction_still(session, jpeg)
    assert muse_runner.direction_still_bytes(session) == jpeg
    view = public_view(session)
    assert view["direction_still"]["ready"] is True
    assert "jpeg_b64" not in (view.get("direction_still") or {})
    # raw session still has blob for GEN lane
    assert "jpeg_b64" in session["direction_still"]
