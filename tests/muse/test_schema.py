"""Step state for the chat studio, and the preview-frame decoder."""
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


def test_new_session_casts_gallery_crew_by_default():
    s = schema.new_session()
    assert s["inputs"]["crew_preset"] == "gallery"
    assert "beat" in s["inputs"]["crew_ids"]
    assert "finisher" not in s["inputs"]["crew_ids"]  # always appended at resolve
    assert s["status"] == "setup"
    assert s["chat"] == []
    assert s["craft"]["prompt"] == ""


def test_board_is_done_when_the_job_stops_not_when_a_count_is_reached():
    s = _session(status="boarding", board={"job_id": "j", "pending": True, "images": [
        {"index": 0, "image_id": "a"}, {"index": 1, "image_id": "b"},
        {"index": 2, "image_id": "c"}, {"index": 3, "image_id": "d"},
    ]})
    s["inputs"]["draft_count"] = 4
    assert schema.step_state(s)["board"]["done"] is False
    assert schema.step_state(s)["board"]["pending"] is True

    s["board"]["images"] += [{"index": 4, "image_id": "e"}]
    s["board"]["pending"] = False
    s["status"] = "awaiting_ok"
    state = schema.step_state(s)
    assert state["board"]["done"] is True
    assert state["board"]["detail"] == "5"


def test_a_board_job_that_produced_nothing_is_not_done():
    s = _session(status="boarding", board={"job_id": "j", "pending": False, "images": []})
    assert schema.step_state(s)["board"]["done"] is False


def test_shoot_is_pending_while_status_is_shooting():
    s = _session(status="shooting", shoot={"pending": True, "images": []})
    state = schema.step_state(s)
    assert state["shoot"]["pending"] is True
    assert state["shoot"]["done"] is False


def test_next_step_walks_setup_chat_board_shoot():
    s = _session(status="setup")
    assert schema.next_step(s) == "setup"
    s["status"] = "chat"
    assert schema.next_step(s) == "chat"
    s["status"] = "awaiting_ok"
    assert schema.next_step(s) == "board"
    s["status"] = "shooting"
    assert schema.next_step(s) == "shoot"
    s["status"] = "done"
    assert schema.next_step(s) == "done"


def test_preview_frames_are_found_behind_whatever_header_comfy_sends():
    jpeg = b"\xff\xd8\xff\xe0rest-of-image"
    assert _preview_image(b"\x00\x00\x00\x01\x00\x00\x00\x01" + jpeg) == jpeg
    assert _preview_image(b"\x00\x00\x00\x01\x00\x00\x00\x02xxxx" + jpeg) == jpeg
