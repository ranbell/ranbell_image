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




# ── 欄名の尻尾（2026-09-16）─────────────────────────────────────────────

def test_a_label_in_the_middle_of_a_line_still_starts_its_block():
    """**行の途中から始まる欄も、欄の切れ目として読む。**（2026-09-16）

    総監督「SAY などの Tag が漏れる」。実機（`f8961eaa`）で内心の吹き出しに

        恥ずかしいけど、猫ちゃんはふわふわしてて気持ちいい……。 CARD

    が出ていた。行頭の欄名しか見ていなかったので `CARD` が内心に残り、
    **CARD の中身（着ているもの）は行の続きごと捨てられていた。**
    """
    raw = (
        "SAY: こんばんは、総監督。\n"
        "ASIDE: 恥ずかしいけど、猫ちゃんはふわふわしてて気持ちいい……。 CARD\n"
        "WEARING: negligee\n"
        "PITCH: 1) もっと寄る"
    )
    blocks = identity.parse_talk_blocks(raw)
    assert blocks["aside"] == "恥ずかしいけど、猫ちゃんはふわふわしてて気持ちいい……。"
    assert "CARD" not in blocks["aside"]
    assert blocks["card"] == "WEARING: negligee", "捨てずに CARD へ渡す"
    assert blocks["pitch"] == "1) もっと寄る"


def test_two_blocks_on_one_line_are_split():
    blocks = identity.parse_talk_blocks("SAY: ふふ。 ASIDE: 心の声。 CARD: WEARING: apron")
    assert blocks["say"] == "ふふ。"
    assert blocks["aside"] == "心の声。"
    assert blocks["card"] == "WEARING: apron"


def test_an_ordinary_word_is_not_a_label():
    """**落としすぎない。** 裸の欄名は大文字のときだけ（英単語を切らない）。"""
    blocks = identity.parse_talk_blocks("SAY: 誕生日の card を渡すところ。")
    assert blocks["say"] == "誕生日の card を渡すところ。"
    assert blocks["card"] == ""


def test_an_unlabelled_reply_is_still_her_line():
    assert identity.parse_talk_blocks("ラベルのない返事です。")["say"] == "ラベルのない返事です。"


def test_the_wardrobe_costume_block_never_reaches_the_bubble():
    """**機械が読む八行は、吹き出しに出さない。**（2026-09-18）

    実機（`0239133f`）で衣装の席が

        砂糖袋なんてそんな小道具、布が台無しになっちゃうわよ。……
        SILHOUETTE: loose_top / structured_bottom
        LAYERS: knit_cardigan / professional_blouse
        GARMENTS: top=knit_cardigan / bottom=tailored_trousers …

    と 479字の吹き出しを出した。`COSTUME` の尻尾は classic の prep 用の条文で、
    Refine の席には宛先（SCENE ブロック）が無い。出どころは直したが、
    **届く手前でも落とす。**
    """
    raw = (
        "砂糖袋なんてそんな小道具、布が台無しになっちゃうわよ。\n\n"
        "SILHOUETTE: loose_top / structured_bottom\n"
        "LAYERS: knit_cardigan / professional_blouse\n"
        "GARMENTS: top=knit_cardigan / bottom=tailored_trousers"
    )
    out = identity.sanitize_muse_say(raw)
    assert out == "砂糖袋なんてそんな小道具、布が台無しになっちゃうわよ。"


def test_the_refine_seat_is_not_asked_for_a_costume_block():
    """出どころ側 —— 席の条文から尻尾を外す（classic の prep では現役）。"""
    from app.muse import crew

    seat = [m for m in crew.MUSES if crew.role_of(m) == "wardrobe"][0]
    assert "SILHOUETTE" not in crew.system_prompt_for(seat)
    prep = crew.actress_duet_prompt({"name": "x"}, mode="prep", locale="ja", seed="s")
    assert "SILHOUETTE" in prep, "classic の prep からは外さない"
