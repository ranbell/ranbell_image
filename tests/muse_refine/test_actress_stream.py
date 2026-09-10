"""**彼女の台詞を流す。**（2026-09-10）

総監督「会話がストリーミングされないので、待ち時間をやっぱり感じてしまう」。

この段は実測 20.3秒で、そのうち 18.1秒はプロンプトを読む時間（16,272字＝
5,424tok＠300tok/s）。総時間は変わらないが、無言で終わりを待つのと途中から
文字が出るのとでは待たされ方が違う。

**流していいのは `SAY:` の中だけ。** 欄の名前も `MY_FEEL:` も画面に出しては
いけない。classic の `_say_only` がその仕事をしていて、Refine の欄名は
そちらの `_SAY_SHUT_RE` に全部入っている。
"""
from __future__ import annotations

import asyncio

from app.muse import service as muse_service
from app.muse_refine import ledger as L, writer


class _Ollama:
    """`generate_text` と `generate_text_stream` のどちらが呼ばれたか数える。"""

    def __init__(self, raw: str):
        self.raw = raw
        self.plain = 0
        self.streamed = 0

    async def generate_text(self, prompt, **kw):
        self.plain += 1
        return self.raw

    async def generate_text_stream(self, prompt, **kw):
        self.streamed += 1
        for ch in self.raw:
            yield {"type": "token", "text": ch}


_RAW = "SAY: おかえりなさい、総監督。\nASIDE: すこし緊張してる\nMY_FEEL: 期待\n"


def _turn(ollama, on_token=None):
    return asyncio.run(writer.actress_turn(
        ollama, model="m", locale="ja", name="澪", now="NOW",
        ledger=L.blank(), identity_blurb="", user_line="ただいま",
        director_tail="", on_token=on_token,
    ))


def test_without_a_listener_it_does_not_stream():
    o = _Ollama(_RAW)
    out = _turn(o)
    assert (o.plain, o.streamed) == (1, 0)
    assert "おかえりなさい" in out["say"]


def test_with_a_listener_it_streams_and_still_parses():
    o = _Ollama(_RAW)
    seen: list[str] = []
    out = _turn(o, on_token=seen.append)
    assert (o.plain, o.streamed) == (0, 1)
    # 流したうえで、いつも通り欄に分かれること
    assert "おかえりなさい" in out["say"]
    assert "緊張" in out["aside"]
    assert seen


def test_only_the_spoken_part_reaches_the_screen():
    """`_say_only` を通すと、欄の名前も内心も漏れない。"""
    o = _Ollama(_RAW)
    seen: list[str] = []
    _turn(o, on_token=muse_service._say_only(seen.append))
    live = "".join(seen)
    assert "おかえりなさい" in live
    for leak in ("SAY:", "ASIDE:", "MY_FEEL:", "緊張", "期待"):
        assert leak not in live, leak


def test_a_broken_listener_does_not_break_the_turn():
    """画面が落ちても撮影は続く。"""
    o = _Ollama(_RAW)

    def _boom(_text):
        raise RuntimeError("SSE went away")

    out = _turn(o, on_token=_boom)
    assert "おかえりなさい" in out["say"]
