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


# ── 流れている表示が巻き戻らないこと（2026-09-12）───────────────────────
def test_a_finished_seat_is_kept_on_screen():
    """総監督「役が話す毎にリセット処理が入るのか、毎回巻き戻されてしまいます」。

    確定した行が画面に出るのは POST が返ってから（ターンの途中では `refresh` が
    `busy` で止まる）。だから流し終えた席を畳んでおかないと、次の席が始まった
    瞬間に前の席の言葉が消える —— 18席ぶんそれが起きる。
    """
    handler = SRC[SRC.index("if (data.type === 'muse_speaking')"):]
    handler = handler[: handler.index("return\n    }")]
    # 空にする前に畳む
    assert handler.index("liveDone.value = [") < handler.index("liveSay.value = ''")
    assert "if (liveSay.value.trim())" in handler


def test_the_kept_lines_go_away_when_the_real_ones_arrive():
    fn = SRC[SRC.index("function stopSpeaking()"):]
    fn = fn[: fn.index("\n}\n") + 2]
    assert "liveDone.value = []" in fn


@pytest.mark.parametrize("fn", ["sendChat", "runStage", "openSession"])
def test_the_swap_has_no_double_showing_window(fn):
    """本物の行が入った直後に畳む。finally まで待つと一瞬だけ二度出る。"""
    body = _body(fn)
    post = body.index("await api(")
    tail = body[post:]
    # 区切りは実際の `} finally {` だけ。説明文の「finally」に当たらないように。
    cut = tail.index("} finally {") if "} finally {" in tail else len(tail)
    assert "stopSpeaking()" in tail[:cut], f"{fn}: POST の直後に畳んでいない"


def test_the_kept_lines_look_like_the_real_ones():
    """差し替わったときに動いて見えないよう、確定した行と同じ見た目にする。"""
    block = SRC[SRC.index('v-for="(done, di) in liveDone"'):]
    block = block[: block.index("<div v-if=\"liveSay\"")]
    for cls in ("border-amber-800/30", "border-pink-500/30"):
        assert cls in block, cls
    assert "done.lead ? '🌸' : '🎬'" in block
