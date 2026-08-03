"""Step state and the preview-frame decoder.

`step_state` is what the panel renders from, so "half the grid has landed" must
not read as done — a run three-quarters finished is in progress.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.ai.comfy import _preview_image
from app.muse import schema


def _session(**over):
    s = schema.new_session({"theme": "t", "character_id": "c",
                            "workflow": "w.json", "model": "m"})
    s.update(over)
    return s


def test_a_new_session_needs_nothing_once_the_four_inputs_are_set():
    assert schema.missing_inputs(_session()) == []
    assert schema.missing_inputs(schema.new_session()) == [
        "theme", "character", "workflow", "model",
    ]


def test_draft_is_not_done_until_every_variation_has_landed():
    s = _session(draft={"job_id": "j", "images": [
        {"index": 0, "image_id": "a"}, {"index": 1, "image_id": ""},
    ]})
    assert schema.step_state(s)["draft"]["done"] is False
    s["draft"]["images"][1]["image_id"] = "b"
    assert schema.step_state(s)["draft"]["done"] is True


def test_refine_is_not_done_while_any_stage_is_still_blank():
    s = _session(chains=[{"draft_index": 0, "stages": [
        {"stage": "reinforce", "image_id": "a"},
        {"stage": "cinematic", "image_id": ""},
    ]}])
    state = schema.step_state(s)
    assert state["refine"]["done"] is False
    assert state["refine"]["pending"] is True
    assert state["refine"]["detail"] == "1/2"


def test_next_step_walks_draft_then_refine():
    s = _session()
    assert schema.next_step(s) == "draft"
    s["draft"] = {"job_id": "j", "images": [{"index": 0, "image_id": "a"}]}
    assert schema.next_step(s) == "refine"
    s["chains"] = [{"stages": [{"stage": "reinforce", "image_id": "x"}]}]
    assert schema.next_step(s) == "done"


def test_preview_frames_are_found_behind_whatever_header_comfy_sends():
    jpeg = b"\xff\xd8\xff\xe0rest-of-image"
    # Documented layout: 4-byte event type, 4-byte image format, then the image.
    assert _preview_image(b"\x00\x00\x00\x01\x00\x00\x00\x01" + jpeg) == jpeg
    # Newer builds can put a metadata blob in between, so the offset moves.
    assert _preview_image(b"\x00\x00\x00\x04" + b'{"node":"7"}' + jpeg) == jpeg
    assert _preview_image(b"\x00\x00\x00\x01\x00\x00\x00\x02\x89PNGdata") == b"\x89PNGdata"
    # Anything that is not an image must not be published as one.
    assert _preview_image(b"\x00\x00\x00\x03no image here") is None
