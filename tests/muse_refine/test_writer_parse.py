"""Writer / verify parsers."""
from __future__ import annotations

from app.muse_refine.writer import parse_actress, parse_verify


def test_parse_actress_say_only():
    say, aside, prop = parse_actress("SAY: 了解、屋上で撮ろう。")
    assert "屋上" in say
    assert aside == ""
    assert prop == {}


def test_parse_actress_with_aside_and_propose():
    raw = (
        'SAY: 白いシャツにして屋上はどう？\n'
        'ASIDE: ドキドキする…ちゃんと似合うかな。\n'
        'PROPOSE: {"wearing": "white shirt", "scene": "rooftop"}'
    )
    say, aside, prop = parse_actress(raw)
    assert "シャツ" in say or "屋上" in say
    assert "ドキドキ" in aside
    assert prop["wearing"] == "white shirt"
    assert prop["scene"] == "rooftop"


def test_parse_verify_ok():
    ok, comment, repair = parse_verify(
        "OK: yes\nCOMMENT: うん、白いシャツで屋上になってる。"
    )
    assert ok is True
    assert "シャツ" in comment or "屋上" in comment
    assert repair == {}


def test_parse_verify_repair():
    raw = (
        'OK: no\n'
        'COMMENT: ずれてる。自分で直すね。\n'
        'REPAIR: {"wearing": "white shirt", "scene": "rooftop"}'
    )
    ok, comment, repair = parse_verify(raw)
    assert ok is False
    assert "直す" in comment
    assert repair["wearing"] == "white shirt"
    assert repair["scene"] == "rooftop"
