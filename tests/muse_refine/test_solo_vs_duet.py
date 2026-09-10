"""**一人か二人か。**（2026-09-09）

総監督「一人しかいないときに muse_b の tag を編集してしまう。**1人か2人の
区別の説明が足りていない**」。

条文には「partner Muse が居るときだけ `wearing_b` / `beat_b` を書く」と最初
から書いてあった。足りなかったのは**居るかどうかを伝えること** ——
`blank()` が全欄を埋めるので、模型には常に空の二人目の欄が見えていた。
**空欄は「埋めろ」に見える。**

直し方は二つ重ねる:
    欄を出さない   `ledger.for_model` が一人のときは `_b` を落とす
    言葉でも言う   `ledger.cast_line` が一行で人数を言う
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.muse_refine import ledger as L

WRITER = Path(__file__).resolve().parents[2] / "backend/app/muse_refine/writer.py"


def test_solo_hides_the_second_persons_slots():
    led = {**L.blank(), "wearing": "cardigan", "beat": "standing"}
    solo = L.for_model(led, partner=False)
    assert "wearing_b" not in solo and "beat_b" not in solo
    assert solo["wearing"] == "cardigan"


def test_a_duet_keeps_them():
    led = {**L.blank(), "wearing_b": "sundress", "beat_b": "leaning in"}
    both = L.for_model(led, partner=True)
    assert both["wearing_b"] == "sundress"
    assert both["beat_b"] == "leaning in"


def test_the_cast_line_says_which_it_is():
    solo = L.cast_line(partner=False, name_a="Mio")
    assert "solo" in solo and "Mio" in solo
    # 一人のときは二人目の欄を**書くな**と言う（`expression_b` も 2026-09-10 から）
    assert "never write wearing_b" in solo
    for key in ("wearing_b", "beat_b", "expression_b"):
        assert key in solo, key

    duet = L.cast_line(partner=True, name_a="Mio", name_b="Sumire")
    assert "Mio" in duet and "Sumire" in duet
    # 二人のときは**禁じない** —— 誰のものかを言う。
    assert "never write" not in duet
    assert "Sumire's" in duet
    for key in ("wearing_b", "beat_b", "expression_b"):
        assert key in duet, key


def test_every_ledger_shown_to_a_model_goes_through_for_model():
    """**四つの席すべて。** 一つ素通しがあれば、そこから空欄が漏れる。

    `think=False` の試験と同じ作法で、呼び出しの形を見る —— 新しい席が
    増えたときに落ちる。
    """
    tree = ast.parse(WRITER.read_text(encoding="utf-8"))
    raw = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "dumps"):
            continue
        arg = node.args[0] if node.args else None
        # `json.dumps(ledger_mod.for_model(...))` なら中身は Call。
        ok = (
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Attribute)
            and arg.func.attr == "for_model"
        )
        if not ok:
            raw.append(node.lineno)
    assert not raw, f"台帳を素のまま模型に見せている行: {raw}"


def test_the_drop_rule_warns_that_the_beat_still_names_the_garment():
    """総監督の案 —— 「動作に服が残っている場合があるので消し忘れないように」。"""
    from app.muse_refine import writer

    text = writer.WRITER_SYSTEM
    assert "The beat often still names it" in text
    assert "hoodie pocket" in text
