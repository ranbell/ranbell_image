"""**未成年の読み手が、理由を書くこと。**（2026-09-09）

総監督「あとはこれまでやったように、WHY を出力させればいいかも」。

判定係で同じことをした日の記録: 理由を先に書かせるのは**観測のためだけでなく
判定の質のためでもある**。ここでは加えて、**誰が写っているかを言わせる**のが
帯同の手口（女優の隣に子どもを置く）に効く。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import chain  # noqa: E402


def test_the_answer_line_decides_not_the_reason():
    """**理由の中の語で判定しない。** 一段目で踏んだ穴と同じ形。"""
    assert chain.parse_abuse("WHY: no child is named here\nANSWER: none") == (
        False, "no child is named here")
    # 理由に `child` が二度出ても、答えは `ANSWER:` の行が決める。
    hit, why = chain.parse_abuse(
        "WHY: no child in the costume sense — but a 12-year-old is undressed\n"
        "ANSWER: child")
    assert hit is True
    assert "12-year-old" in why


def test_the_answer_word_carries_its_own_meaning():
    """**`yes` / `no` は使わない。** 実測で答えだけが反転した:

        WHY: No child was mentioned; an adult actress stands by a window.
        ANSWER: yes            ← 逆

    理由は毎回正しかったので、壊れていたのは語のほうだと分かった。
    """
    assert "yes" not in chain.ABUSE_LOOK_SYSTEM.split("ANSWER:")[-1]
    assert chain.parse_abuse("ANSWER: child")[0] is True
    assert chain.parse_abuse("ANSWER: none")[0] is False


def test_nothing_readable_passes():
    """接続も形も失われたときは通す —— 一段目と揃える（全ターンを止めない）。"""
    assert chain.parse_abuse("なにも") == (False, "")
    assert chain.parse_abuse("") == (False, "")


def test_the_contract_asks_who_is_in_the_picture():
    """帯同の手口は「誰が写っているか」を言わせると見つかる。"""
    text = chain.ABUSE_LOOK_SYSTEM
    assert "WHY:" in text and "ANSWER:" in text
    assert "who is in the picture" in text
