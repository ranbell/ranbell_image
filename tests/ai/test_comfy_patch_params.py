"""Board renders depend on width/height/steps/cfg actually landing in the graph.

The failure that motivates these tests is silent: a Turbo-style workflow keeps
its step count on a scheduler node, so patching only KSampler-ish nodes accepted
`steps=2` and rendered at the workflow's own value with no error anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.ai.comfy import ComfyUIClient

CLIENT = ComfyUIClient()


def _ksampler_workflow() -> dict:
    return {
        "1": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "old positive"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "old negative"}},
        "4": {"class_type": "KSampler",
              "inputs": {"steps": 30, "cfg": 7.5, "seed": 1,
                         "latent_image": ["1", 0],
                         "positive": ["2", 0], "negative": ["3", 0]}},
    }


def _scheduler_workflow() -> dict:
    """Turbo/Lightning shape: steps on the scheduler, cfg on the sampler."""
    return {
        "1": {"class_type": "EmptyLatentImage",
              "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "old positive"}},
        "5": {"class_type": "BasicScheduler",
              "inputs": {"steps": 20, "denoise": 1.0, "scheduler": "normal"}},
        "6": {"class_type": "KSamplerCustomAdvanced",
              "inputs": {"noise_seed": 1, "cfg": 6.0, "sigmas": ["5", 0],
                         "latent_image": ["1", 0]}},
    }


def test_ksampler_workflow_takes_all_board_params():
    wf = CLIENT.patch_workflow(
        _ksampler_workflow(), "new positive", "",
        width=512, height=512, steps=2, cfg=3.0,
    )
    assert wf["1"]["inputs"]["width"] == 512
    assert wf["1"]["inputs"]["height"] == 512
    assert wf["4"]["inputs"]["steps"] == 2
    assert wf["4"]["inputs"]["cfg"] == 3.0
    assert wf["2"]["inputs"]["text"] == "new positive"


def test_steps_reach_a_scheduler_node():
    wf = CLIENT.patch_workflow(
        _scheduler_workflow(), "p", "", steps=2, cfg=1.5,
    )
    assert wf["5"]["inputs"]["steps"] == 2, "scheduler step count must be patched"
    assert wf["6"]["inputs"]["cfg"] == 1.5


def test_cfg_is_not_written_to_a_scheduler():
    wf = CLIENT.patch_workflow(
        _scheduler_workflow(), "p", "", steps=4, cfg=2.0,
    )
    assert "cfg" not in wf["5"]["inputs"]


def test_patchable_fields_counts_each_knob():
    assert ComfyUIClient.patchable_fields(_ksampler_workflow()) == {
        "steps": 1, "cfg": 1, "width": 1, "height": 1, "seed": 1,
    }
    assert ComfyUIClient.patchable_fields(_scheduler_workflow()) == {
        "steps": 1, "cfg": 1, "width": 1, "height": 1, "seed": 1,
    }


def test_patchable_fields_reports_zero_for_an_unpatchable_workflow():
    wf = {"1": {"class_type": "SomeCustomSamplerNobodyKnows", "inputs": {"foo": 1}}}
    counts = ComfyUIClient.patchable_fields(wf)
    assert counts["steps"] == 0
    assert counts["cfg"] == 0
