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


def test_a_new_session_starts_with_an_empty_shot_sheet():
    """There is no crew to pick any more — every seat is always seated, and the
    working state is a shot sheet rather than one blob everyone rewrites."""
    s = schema.new_session()
    assert s["status"] == "setup"
    assert s["chat"] == []
    assert s["shot"] == {}
    assert s["plan"] == {}
    assert s["probes"] == {}
    assert s["craft"]["prompt"] == ""
    for gone in ("crew_preset", "crew_ids", "banter_mode"):
        assert gone not in s["inputs"], gone


def test_public_roster_fills_the_lead_from_the_character():
    s = schema.new_session()
    s["character"] = {
        "name": "Sample Lead", "name_ja": "サンプル主演",
        "personality": {"summary_ja": "いつも本気で話す。"},
    }
    view = schema.public_view(s)
    seats = view["roster"]["roles"]
    assert [r["id"] for r in seats] == ["plan", "actress", "enrich", "reduce", "check"]
    lead = next(r for r in seats if r["id"] == "actress")
    assert lead["name_ja"] == "サンプル主演"


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
