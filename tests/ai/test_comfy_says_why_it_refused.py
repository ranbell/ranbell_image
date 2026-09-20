"""**A 400 from ComfyUI has to say what was wrong.** (2026-09-21)

Live, a board render came back as `Client error '400 Bad Request' for url
'http://…:8188/prompt'` and nothing else — `raise_for_status()` throws the body
away, and the body is the whole answer. ComfyUI validates the graph before it
queues anything and names the node, the input and the reason (a checkpoint that
is not installed, a value out of range).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.comfy import rejection_message


class _Resp:
    def __init__(self, payload=None, text="", status=400):
        self._payload = payload
        self.text = text
        self.status_code = status

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def test_a_missing_checkpoint_is_named():
    said = rejection_message(_Resp({
        "error": {"type": "prompt_outputs_failed_validation",
                  "message": "Prompt outputs failed validation"},
        "node_errors": {
            "4": {"class_type": "CheckpointLoaderSimple",
                  "errors": [{"type": "value_not_in_list",
                              "message": "Value not in list",
                              "details": "ckpt_name: 'anima_v3.safetensors' not in ['other.safetensors']"}]},
        },
    }))

    assert "Prompt outputs failed validation" in said
    assert "node 4" in said and "CheckpointLoaderSimple" in said
    assert "anima_v3.safetensors" in said


def test_more_than_one_node_is_summarised():
    said = rejection_message(_Resp({
        "error": {"message": "Prompt outputs failed validation"},
        "node_errors": {
            "4": {"class_type": "A", "errors": [{"details": "first"}]},
            "9": {"class_type": "B", "errors": [{"details": "second"}]},
        },
    }))

    assert "first" in said and "second" in said
    assert len(said) <= 600, "画面に出すので長さは抑える"


def test_an_error_with_details_but_no_nodes_still_reads():
    said = rejection_message(_Resp({
        "error": {"message": "Invalid prompt", "details": "missing node 12"},
    }))

    assert "Invalid prompt" in said
    assert "missing node 12" in said


def test_a_body_that_is_not_json_is_passed_through():
    said = rejection_message(_Resp(None, text="<html>Bad Request</html>", status=400))

    assert "400" in said
    assert "Bad Request" in said


def test_an_empty_body_still_says_something():
    said = rejection_message(_Resp(None, text="", status=500))

    assert said.strip()
    assert "500" in said


def test_the_status_is_kept_when_there_is_no_reason():
    said = rejection_message(_Resp({}, status=422))

    assert "422" in said
    assert "no reason given" in said
