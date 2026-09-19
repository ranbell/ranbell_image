"""**A graph that zeroes its negative out.** (2026-09-20)

The Showrunner placed a krea2 workflow on the server and it is shaped unlike
every graph this patcher had seen: **one `CLIPTextEncode`**, and `KSampler.negative`
fed by a `ConditioningZeroOut` whose conditioning comes from that same encoder.

Tracing the negative wire therefore lands on the node that has just been given
the positive. Measured on the real file before the guard went in, the positive
came back as:

    "1girl, park, smile, bad quality, border"

— the negative burned into the prompt. Muse sends no negative for this family at
all (`muse/family.py`), so the shoot path was safe; every other caller
(`jobs/runners.run_generation`, Invoke, the character board) still sends one.

The fixture is that graph with the model filenames redacted — the shape is what
matters, and the checkpoint names are not ours to ship.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.ai.comfy import ComfyUIClient  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "zeroed_negative_workflow.json"


def _wf() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _client() -> ComfyUIClient:
    return ComfyUIClient.__new__(ComfyUIClient)


def test_the_negative_never_lands_on_the_positive_node():
    """The whole point. The positive survives exactly as it was given."""
    out = _client().patch_workflow(
        _wf(), "1girl, park, smile", "bad quality, border", append_negative=True,
    )
    assert out["6"]["inputs"]["text"] == "1girl, park, smile"


def test_a_two_encoder_graph_still_gets_its_negative():
    """The guard is about one node serving both, not about negatives at large."""
    wf = _wf()
    # Give the graph a negative encoder of its own and wire the zero-out to it.
    wf["90"] = {"class_type": "CLIPTextEncode",
                "inputs": {"text": "worst quality", "clip": ["38", 0]}}
    wf["77"]["inputs"]["conditioning"] = ["90", 0]

    out = _client().patch_workflow(
        wf, "1girl", "bad quality", append_negative=True,
    )
    assert out["6"]["inputs"]["text"] == "1girl"
    assert out["90"]["inputs"]["text"] == "worst quality, bad quality"


def test_the_knobs_muse_sends_for_this_family_all_land():
    """What a krea2 shoot actually does to this graph.

    steps replaces a wire into an `Int Literal`; the canvas follows the
    `PrimitiveInt` nodes; the seed reaches the `Seed Generator`; and **cfg is not
    written at all**, so the workflow keeps the value it was saved with.
    """
    out = _client().patch_workflow(
        _wf(), "1girl, park", "",
        batch_count=1, seed=123, width=896, height=1152, steps=4, cfg=None,
        append_negative=True,
    )
    assert out["3"]["inputs"]["steps"] == 4
    assert out["80"]["inputs"]["value"] == 896
    assert out["81"]["inputs"]["value"] == 1152
    assert out["82"]["inputs"]["seed"] == 123
    # Untouched: the graph's own cfg literal and its sampler wiring.
    assert out["83"]["inputs"] == _wf()["83"]["inputs"]
    assert out["3"]["inputs"]["cfg"] == _wf()["3"]["inputs"]["cfg"]


def test_every_knob_muse_sends_has_somewhere_to_go():
    """`patchable_fields` is what `run_render` logs when a knob has no home."""
    fields = _client().patchable_fields(_wf())
    assert fields["steps"] >= 1 and fields["width"] >= 1 and fields["height"] >= 1
    assert fields["seed"] >= 1
