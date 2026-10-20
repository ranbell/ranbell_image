"""Floating nodes are ignored when a graph is queued.

ComfyUI checks that every node's type is installed, even nodes nothing reads.
A node no output depends on changes nothing in the render, so a missing type
there must not stop it. A missing node that *is* read still fails, with
ComfyUI's own reason.
"""
import json
import sys
from pathlib import Path

import httpx
import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.ai.comfy import ComfyUIClient


def _fake_comfy(installed_missing: set[str], *, old_format: bool = False):
    """A /prompt that refuses the first node whose type is not installed,
    the way ComfyUI's validate_prompt does, and records every graph sent."""
    sent: list[dict] = []

    def handler(request):
        graph = json.loads(request.content)["prompt"]
        sent.append(graph)
        for nid, node in graph.items():
            kind = node.get("class_type")
            if kind in installed_missing:
                if old_format:
                    err = {"type": "invalid_prompt",
                           "message": f"Cannot execute because node {kind} does not exist.",
                           "details": f"Node ID '#{nid}'", "extra_info": {}}
                else:
                    err = {"type": "missing_node_type",
                           "message": f"Node '{kind}' not found. The custom node may not be installed.",
                           "details": f"Node ID '#{nid}'",
                           "extra_info": {"node_id": nid, "class_type": kind, "node_title": kind}}
                return httpx.Response(400, json={"error": err, "node_errors": {}})
        return httpx.Response(200, json={"prompt_id": f"p{len(sent)}"})

    c = ComfyUIClient()
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c, sent


def _krea2_after_patch() -> dict:
    """The shape of API_Krea2_JANK2.json once the prompt is written into node 6:
    its WAS `Text Multiline` (66) is no longer read by anything."""
    return {
        "3": {"class_type": "KSampler", "inputs": {"positive": ["6", 0], "negative": ["77", 0], "seed": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "1girl, park", "clip": ["38", 0]}},
        "38": {"class_type": "CLIPLoader", "inputs": {"clip_name": "x", "type": "krea2"}},
        "66": {"class_type": "Text Multiline", "inputs": {"text": ""}},
        "77": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["6", 0]}},
        "87": {"class_type": "SaveImage", "inputs": {"images": ["3", 0]}},
    }


@pytest.mark.asyncio
async def test_a_floating_uninstalled_node_is_dropped_and_the_render_goes_ahead():
    comfy, sent = _fake_comfy({"Text Multiline"})
    wf = _krea2_after_patch()
    assert await comfy.queue_prompt(wf) == "p2"
    assert "66" in sent[0] and "66" not in sent[1]
    assert set(sent[1]) == set(wf) - {"66"}
    assert "66" in wf  # the caller's graph is left as it was


@pytest.mark.asyncio
async def test_the_older_does_not_exist_wording_is_understood_too():
    comfy, sent = _fake_comfy({"Text Multiline"}, old_format=True)
    assert await comfy.queue_prompt(_krea2_after_patch()) == "p2"
    assert "66" not in sent[1]


@pytest.mark.asyncio
async def test_a_chain_of_floating_uninstalled_nodes_is_dropped_link_by_link():
    comfy, sent = _fake_comfy({"Text Multiline", "Text Concatenate"})
    wf = _krea2_after_patch()
    wf["66"]["inputs"]["text"] = ["65", 0]
    wf["65"] = {"class_type": "Text Concatenate", "inputs": {"text_a": "a", "text_b": "b"}}
    assert await comfy.queue_prompt(wf) == "p3"
    # 65 is read by 66, so it stays until 66 is gone.
    assert "65" in sent[1] and "66" not in sent[1]
    assert "65" not in sent[2]


@pytest.mark.asyncio
async def test_an_uninstalled_node_that_is_read_still_fails_with_comfys_reason():
    comfy, sent = _fake_comfy({"Text Multiline"})
    wf = _krea2_after_patch()
    wf["6"]["inputs"]["text"] = ["66", 0]
    with pytest.raises(RuntimeError, match="Text Multiline"):
        await comfy.queue_prompt(wf)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_other_refusals_are_not_retried():
    sent = []

    def handler(request):
        sent.append(1)
        return httpx.Response(400, json={
            "error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation"},
            "node_errors": {"37": {"class_type": "UNETLoader", "errors": [
                {"message": "Value not in list", "details": "unet_name: 'gone.safetensors' not in []"}]}},
        })

    comfy = ComfyUIClient()
    comfy._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match="gone.safetensors"):
        await comfy.queue_prompt(_krea2_after_patch())
    assert len(sent) == 1
