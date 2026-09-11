"""**入力が二度と戻らない、という壊れ方を作らない。**（2026-09-12）

総監督「会話終了処理がよくなくて、busy になり続けるのでその後一切の入力が
できなくなる」。

原因は `busy` ではなく `speaking` だった。画面の錠は

    chatLocked = busy || renderLocked || speaking

で、SSE の `muse_speaking` が立てた旗を下ろすのは `chat` / `session_updated` の
分岐だけ、しかも **`!busy` のときだけ**。POST の最中に届いた合図はそこで捨て
られるので、POST が終わったときには誰も下ろす人が居ない。

スタジオ撮りで踏み抜いた —— 班を開くと席が3〜6回 `muse_speaking` を出し、
`runStage` は `busy` しか下ろさないので、そのまま入力が死んだ。

画面のコードは Python から動かせないので、**書かれ方**を読んで固定する。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parents[2] / "frontend/src/components/MuseRefinePanel.vue"
SRC = PANEL.read_text(encoding="utf-8")


def _body(name: str) -> str:
    """`async function name(...) { … }` の中身をざっくり取る。"""
    m = re.search(rf"(?m)^async function {name}\([^)]*\) \{{(.*?)^\}}", SRC, re.S)
    assert m, f"{name} が見つからない"
    return m.group(1)


@pytest.mark.parametrize("fn", ["sendChat", "runStage", "finishSession", "openSession"])
def test_every_post_puts_the_flags_back(fn):
    """POST を出す関数はすべて、finally で喋りの旗を下ろすこと。"""
    body = _body(fn)
    assert "finally" in body, f"{fn} に finally が無い"
    tail = body[body.rindex("finally"):]
    assert "busy.value = false" in tail, f"{fn} が busy を下ろしていない"
    assert "stopSpeaking()" in tail, f"{fn} が speaking を下ろしていない"


def test_there_is_one_place_that_puts_them_back():
    """後始末は一箇所。各所で書くと、次に足した関数で必ず忘れる。"""
    assert "function stopSpeaking()" in SRC
    fn = SRC[SRC.index("function stopSpeaking()"):]
    fn = fn[: fn.index("\n}\n") + 2]
    for flag in ("speaking.value = false", "liveSay.value = ''", "liveName.value = ''"):
        assert flag in fn, flag


def test_the_flag_cannot_outlive_busy():
    """**保険。** どこかで書き忘れても、busy が下りた時点で必ず下ろす。"""
    assert re.search(r"watch\(busy,\s*\(now\)\s*=>\s*\{\s*\n\s*if \(!now\) stopSpeaking\(\)", SRC)


def test_the_lock_is_still_the_three_flags():
    """錠の条件が増えたら、この試験ごと見直す。"""
    m = re.search(r"const chatLocked = computed\(\(\) => (.+?)\)\n", SRC)
    assert m, "chatLocked が見つからない"
    assert m.group(1).strip() == "busy.value || renderLocked.value || speaking.value"
