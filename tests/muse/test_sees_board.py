"""**試し撮りのあとは、彼女に絵を見せる。**（2026-09-10）

総監督「試し撮りしたあとは Muse が画像見るようにしよう」。

台帳は「こう撮ってほしい」で、絵は「こう撮れた」。台帳しか見えないと、撮れた絵
そのものについて話せない。classic は試し撮り以降、毎ターン板を渡している。

**絵を読めないモデルは、断らずに空を返す。** そのままだと「今日は口数が少ない
な」にしか見えないので、一度だけ絵抜きで撮り直し、口に出して言う。
"""
from __future__ import annotations

import asyncio

from app.muse import ledger as L, service, writer


class _Ollama:
    """絵つきで呼ばれたら空を返す（＝絵を読めないモデル）ようにも作れる。"""

    def __init__(self, *, can_see: bool = True, raw: str = "SAY: 見えてます。\n"):
        self.can_see, self.raw = can_see, raw
        self.calls: list[str] = []

    async def generate_text(self, prompt, **kw):
        self.calls.append("text")
        return self.raw

    async def generate_vlm(self, prompt, images, **kw):
        self.calls.append(f"vlm:{len(images)}")
        return self.raw if self.can_see else ""

    async def generate_text_stream(self, prompt, **kw):
        self.calls.append("text_stream")
        for ch in self.raw:
            yield {"type": "token", "text": ch}

    async def generate_vlm_stream(self, prompt, images, **kw):
        self.calls.append(f"vlm_stream:{len(images)}")
        for ch in (self.raw if self.can_see else ""):
            yield {"type": "token", "text": ch}


def _turn(o, **kw):
    return asyncio.run(writer.actress_turn(
        o, model="m", locale="ja", name="澪", now="NOW", ledger=L.blank(),
        identity_blurb="", user_line="これでいい？", director_tail="", **kw,
    ))


def test_no_board_means_no_picture_call():
    o = _Ollama()
    out = _turn(o)
    assert o.calls == ["text"]
    assert out["blind"] is False


def test_a_board_is_handed_over():
    o = _Ollama()
    out = _turn(o, images=[b"jpeg-bytes"])
    assert o.calls == ["vlm:1"]
    assert "見えてます" in out["say"]
    assert out["blind"] is False


def test_a_blind_model_retries_without_the_picture():
    """空が返ったら、黙って諦めず絵抜きでもう一度。"""
    o = _Ollama(can_see=False)
    out = _turn(o, images=[b"jpeg-bytes"])
    assert o.calls == ["vlm:1", "text"]
    assert out["blind"] is True
    assert "見えてます" in out["say"]      # 二度目は返ってくる


def test_streaming_also_carries_the_picture():
    o = _Ollama()
    seen: list[str] = []
    out = _turn(o, images=[b"jpeg-bytes"], on_token=seen.append)
    assert o.calls == ["vlm_stream:1"]
    assert "見えてます" in out["say"]
    assert seen


def test_the_studio_says_so_once_when_she_cannot_see():
    session: dict = {"chat": [], "inputs": {"locale": "ja"}}
    service._note_blind(session, locale="ja")
    service._note_blind(session, locale="ja")   # 二度目は黙る
    rows = [r for r in session["chat"] if r.get("role") == "system"]
    assert len(rows) == 1
    assert "vision_model" in rows[0]["text"]


def test_the_vision_model_is_only_swapped_in_for_picture_turns():
    import inspect
    src = inspect.getsource(service.chat)
    assert 'vision_model' in src
    # 板が無い回は今まで通り model のまま
    assert 'if board_shots else model' in src
