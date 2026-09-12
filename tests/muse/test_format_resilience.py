"""LLM output format breakage — parse, sanitize, salvage, dirty flags."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import identity, notebook, shared


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(shared, "get_runtime_config", _cfg)


def test_sanitize_strips_truncated_tags_leak():
    raw = "はっ…外した。風が来る。\nTAGS: straw_hat, from_below\nSCENE: incomplete"
    out = identity.sanitize_muse_say(raw)
    assert "TAGS" not in out
    assert "SCENE" not in out
    assert "外した" in out


def test_sanitize_strips_english_rule_headings():
    raw = "うん、その感じ。\nCRITICAL RULES FOR W-MUSE SAY:\nもっと近づく？"
    out = identity.sanitize_muse_say(raw)
    assert "CRITICAL" not in out
    assert "近づく" in out


def test_sanitize_strips_required_output_language_label():
    raw = (
        "SAY (required output language): 静かな場所は好き。\n"
        "LANGUAGE: Instructions are in English.\n"
        "窓辺がいいかな。"
    )
    out = identity.sanitize_muse_say(raw)
    assert "required output language" not in out.lower()
    assert "LANGUAGE:" not in out
    assert "静かな場所" in out
    assert "窓辺" in out


def test_parse_talk_blocks_accepts_parenthetical_say_label():
    raw = "SAY (required; output language): 図書館、いいね。\nASIDE: 少し緊張してる。\n"
    blocks = identity.parse_talk_blocks(raw)
    say = identity.sanitize_muse_say(blocks["say"])
    assert "図書館" in say
    assert "required" not in say.lower()
    assert "緊張" in blocks["aside"]


def test_sanitize_strips_english_stage_parens_in_ja():
    raw = "うん…ちょっと待って。(She lowers her head, fingers tightening on the hem.)"
    out = identity.sanitize_muse_say(raw, locale="ja")
    assert "lowers her head" not in out
    assert "うん" in out
    kept = identity.sanitize_muse_say("少し緊張してる（少し緊張）", locale="ja")
    assert "少し緊張" in kept
    short = identity.sanitize_muse_say("了解 (OK)", locale="ja")
    assert "(OK)" in short or "OK" in short


def test_sanitize_keeps_english_parens_in_en_locale():
    raw = "Wait a second. (She lowers her head.)"
    out = identity.sanitize_muse_say(raw, locale="en")
    assert "lowers her head" in out
    turns = identity.parse_duet_speakers(
        "A: Wait. (She lowers her head.)\nB: Okay.",
        locale="en",
    )
    assert turns is not None
    assert "lowers her head" in turns[0]["text"]


def test_parse_table_read_truncated_say_tags():
    raw = "SAY: 帽子、外したよ\nTAGS: straw_hat\n"
    say, tags, scene = identity.parse_table_read(raw)
    assert "帽子" in say
    assert "TAGS" not in say
    assert tags == "" and scene == ""


def test_parse_duet_speakers_name_fallback():
    raw = "あさひ: 先に行くね\nみなも: ちょっと待って"
    turns = identity.parse_duet_speakers(raw, name_a="あさひ", name_b="みなも")
    assert turns is not None
    assert turns[0]["speaker"] == "A"
    assert turns[1]["speaker"] == "B"
    assert "先に" in turns[0]["text"]


def test_parse_duet_speakers_rejects_unknown_names():
    raw = "System A: hello\nMuse B: hi"
    assert identity.parse_duet_speakers(raw) is None


def test_scripter_json_salvages_trailing_comma():
    raw = """{
      "intent": "shot",
      "wearing": "straw hat",
      "beat": "leaning",
      "frame": "eye level",
      "tags": "straw_hat, leaning",
      "craft_scene": "Rooftop lean.",
    }"""
    out = notebook.parse_scripter(raw)
    assert out["intent"] == "shot"
    assert "straw" in out["patch"].get("wearing", "") or "straw_hat" in out["tags"]


def test_scripter_json_salvages_truncated_object():
    raw = (
        '{"intent":"shot","wearing":"jacket","beat":"standing",'
        '"frame":"eye level","tags":"jacket, standing","craft_scene":"She stands'
    )
    out = notebook.parse_scripter(raw)
    # Either repaired JSON or labelled blank — must not crash.
    assert "intent" in out
    assert out.get("raw") == raw or out.get("valid") in (True, False)


