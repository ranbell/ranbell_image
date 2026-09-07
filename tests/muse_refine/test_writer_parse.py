"""Writer / verify parsers."""
from __future__ import annotations

from app.muse_refine import persona
from app.muse_refine.writer import parse_actress, parse_verify


def test_parse_actress_say_only():
    out = parse_actress("SAY: 了解、屋上で撮ろう。")
    assert "屋上" in out["say"]
    assert out["aside"] == ""
    assert out["propose"] == {}


def test_parse_actress_with_aside_and_propose():
    raw = (
        'SAY: 白いシャツにして屋上はどう？\n'
        'ASIDE: ドキドキする…ちゃんと似合うかな。\n'
        'PROPOSE: {"wearing": "white shirt", "scene": "rooftop"}'
    )
    out = parse_actress(raw)
    assert "シャツ" in out["say"] or "屋上" in out["say"]
    assert "ドキドキ" in out["aside"]
    assert out["propose"]["wearing"] == "white shirt"
    assert out["propose"]["scene"] == "rooftop"


def test_parse_actress_my_feel_card_pitch():
    raw = (
        "MY_FEEL: 緊張\n"
        "SAY: うん、白いシャツでいこう。\n"
        "ASIDE: ちゃんと似合ってるかな…\n"
        "CARD:\n"
        "PLACE: rooftop at dusk\n"
        "WEARING: white shirt, blue skirt\n"
        "BEAT: standing, hands on railing\n"
        "EXPRESSION: soft smile\n"
        "FRAME: medium shot, looking at viewer\n"
        "PITCH: もっと寄る | 引きでシルエット"
    )
    out = parse_actress(raw)
    assert out["my_feel"] == "緊張"
    assert "シャツ" in out["say"]
    assert "似合" in out["aside"]
    assert out["propose"]["wearing"] == "white shirt, blue skirt"
    assert out["propose"]["scene"] == "rooftop at dusk"
    assert out["propose"]["beat"].startswith("standing")
    assert out["propose"]["expression"] == "soft smile"
    assert "寄る" in out["pitch"]
    opts = persona.parse_pitch_options(out["pitch"])
    assert len(opts) == 2


def test_card_to_patch_maps_fields():
    card = (
        "PLACE: cafe window\n"
        "WEARING: hoodie\n"
        "BEAT: sitting\n"
        "WEARING_B: sundress\n"
        "BEAT_B: leaning in\n"
    )
    patch = persona.card_to_patch(card)
    assert patch["scene"] == "cafe window"
    assert patch["wearing"] == "hoodie"
    assert patch["beat"] == "sitting"
    assert patch["wearing_b"] == "sundress"
    assert patch["beat_b"] == "leaning in"


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


def test_note_standing_and_commit_pitch():
    session: dict = {"standing": []}
    assert persona.note_standing(session, "常設: 顔はいつも柔らかく") == "顔はいつも柔らかく"
    assert session["standing"] == ["顔はいつも柔らかく"]
    assert persona.is_commit_pitch("「もっと寄る」がいいな")
    assert not persona.is_commit_pitch("もっと寄ってみて")


def test_entertainment_craft_in_system():
    assert "Gap-moe" in persona.ENTERTAINMENT_CRAFT
    assert "Soft-miss" in persona.ENTERTAINMENT_CRAFT
    assert "MY_FEEL" in persona.REFINE_OUTPUT
