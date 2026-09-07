"""Writer parse helpers."""
from __future__ import annotations

from app.muse_refine.writer import parse_actress


def test_parse_actress_say_only():
    say, prop = parse_actress("SAY: 了解、屋上で撮ろう。")
    assert "屋上" in say
    assert prop == {}


def test_parse_actress_with_propose():
    raw = (
        'SAY: 白いシャツにして屋上はどう？\n'
        'PROPOSE: {"wearing": "white shirt", "scene": "rooftop"}'
    )
    say, prop = parse_actress(raw)
    assert "シャツ" in say or "屋上" in say
    assert prop["wearing"] == "white shirt"
    assert prop["scene"] == "rooftop"
