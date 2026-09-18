"""**素通しの覆いが、素通しになっていること。**（2026-09-18）

アプリが握っているのは `OllamaClient` ではなく、その覆いの `LlmGateway`
（`main.py` の `app.state.ollama`）。覆いは引数をそのまま渡すだけなので、
**本体に引数を足したのに覆いに足し忘れる**と、呼んだ瞬間に `TypeError` になる。

実機で踏んだ（2026-09-18・`d532fd2`）:

    `with_done` を `OllamaClient` にだけ足した
    → 主演の段が `TypeError` で落ち、`actress_turn` の except が拾って
      **台詞が「……」だけ・段の時間 0.0 秒**（実機 `10d85603`）
    → 席と会議も同じ経路なので、班は丸ごと黙る

単体試験は全部通っていた —— 試験の模型は覆いを通らないから。だから
**覆いと本体の署名を突き合わせる**。ここが合っていれば、足し忘れは起きない。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.llm import LlmGateway
from app.ai.ollama import OllamaClient

#: 覆いが持っている素通しの口。
PASS_THROUGH = (
    "generate_text",
    "generate_text_stream",
    "generate_vlm",
    "generate_vlm_stream",
    "chat_text",
)


@pytest.mark.parametrize("name", PASS_THROUGH)
def test_the_gateway_accepts_every_argument_the_client_does(name: str):
    inner = set(inspect.signature(getattr(OllamaClient, name)).parameters)
    outer = set(inspect.signature(getattr(LlmGateway, name)).parameters)
    missing = sorted(inner - outer)
    assert not missing, (
        f"LlmGateway.{name} が受け取らない引数: {missing} —— "
        "本体に足したら覆いにも足す（呼ぶと TypeError で落ちる）"
    )


@pytest.mark.parametrize("name", PASS_THROUGH)
def test_the_gateway_hands_them_all_on(name: str):
    """受け取るだけで渡し忘れていないこと（本文に名前が出ているか）。"""
    body = inspect.getsource(getattr(LlmGateway, name))
    head, _, tail = body.partition("self._ollama.")
    for arg in inspect.signature(getattr(OllamaClient, name)).parameters:
        if arg == "self":
            continue
        assert arg in tail, f"LlmGateway.{name} が {arg!r} を渡していない"
