"""Malformed LLM JSON must not throw away a whole generation.

Reported from the field: `storywright failed: Expecting ',' delimiter:
line 68 column 6` — a local model dropped one comma between two panel keys.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.ai.json_util import parse_json_object

GOOD = '{"title": "t", "world": {"setting": "beach"}, "panels": [{"key": "panel_1"}]}'


def test_plain_and_fenced_json():
    assert parse_json_object(GOOD)["title"] == "t"
    assert parse_json_object(f"```json\n{GOOD}\n```")["title"] == "t"
    assert parse_json_object(f"Here you go:\n{GOOD}\nhope that helps")["title"] == "t"


def test_missing_comma_between_members():
    broken = '''{
  "title": "t",
  "panels": [
    {
      "key": "panel_1",
      "state_tags": ["wind", "spray"]
      "must_show": ["throughline_prop"]
    }
  ]
}'''
    data = parse_json_object(broken)
    assert data["panels"][0]["state_tags"] == ["wind", "spray"]
    assert data["panels"][0]["must_show"] == ["throughline_prop"]


def test_trailing_commas():
    broken = '{"title": "t", "panels": [{"key": "panel_1",},],}'
    assert parse_json_object(broken)["panels"][0]["key"] == "panel_1"


def test_truncated_output_keeps_what_arrived():
    broken = '{"title": "t", "world": {"setting": "beach", "core_conflict": "c"'
    data = parse_json_object(broken)
    assert data["title"] == "t"
    assert data["world"]["core_conflict"] == "c"


def test_truncated_mid_string_drops_only_the_partial_value():
    broken = '{"title": "t", "panels": [{"key": "panel_1", "narrative_en": "she wal'
    data = parse_json_object(broken)
    assert data["title"] == "t"
    assert data["panels"][0]["key"] == "panel_1"


def test_prose_with_commas_and_colons_is_untouched():
    good = (
        '{"panels": [{"narrative_en": "she waits, then leaves: the door closes",\n'
        '  "emotion": "light_smile"}]}'
    )
    data = parse_json_object(good)
    assert data["panels"][0]["narrative_en"] == "she waits, then leaves: the door closes"


def test_hopeless_input_still_raises():
    with pytest.raises(ValueError):
        parse_json_object("")
    with pytest.raises(Exception):
        parse_json_object("no json here at all")
