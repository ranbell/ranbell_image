"""Ledger helpers: NOW coverage, picture cues, chips."""
from __future__ import annotations

from app.muse import ledger


def test_now_line_includes_expression_and_frame():
    led = ledger.apply_patch(ledger.blank(), {
        "wearing": "hoodie",
        "beat": "standing",
        "expression": "slight smile",
        "scene": "rooftop",
        "frame": "upper body",
        "light": "golden hour",
    })
    now = ledger.now_line(led, locale="ja")
    assert "服装" in now and "hoodie" in now
    assert "表情" in now and "smile" in now
    assert "構図" in now
    assert "場所" in now


def test_looks_like_picture_line():
    assert ledger.looks_like_picture_line("白いシャツで屋上に立って")
    assert ledger.looks_like_picture_line("change into a red dress")
    assert not ledger.looks_like_picture_line("うん、ありがとう")


def test_chips_for_fields():
    chips = ledger.chips_for(["wearing", "scene"], locale="ja")
    assert [c["key"] for c in chips] == ["wearing", "scene"]
    assert chips[0]["label"] == "服"
    assert chips[1]["label"] == "場所"
    assert chips[0]["icon"]


def test_changed_fields():
    before = ledger.apply_patch(ledger.blank(), {"wearing": "a", "scene": "park"})
    after = ledger.apply_patch(before, {"wearing": "b"})
    assert ledger.changed_fields(before, after) == ["wearing"]
