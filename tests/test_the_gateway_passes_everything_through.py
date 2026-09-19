"""**The pass-through facade actually passes everything through.** (2026-09-18)

What the app holds is not `OllamaClient` but its facade `LlmGateway` (`main.py`'s
`app.state.ollama`). The facade only hands arguments on, so **adding an argument to
the client and forgetting the facade** is a `TypeError` the moment it is called.

Hit live (2026-09-18, `d532fd2`):

    `with_done` was added to `OllamaClient` alone
    -> the actress stage fell over with `TypeError`, `actress_turn`'s except
       caught it and **her line was just "……" with a stage time of 0.0 s**
       (live `10d85603`)
    -> the seats and the corners take the same road, so the whole crew goes quiet

Every unit test passed — the test doubles do not go through the facade. So **the
facade's signature is matched against the client's**. Keep these aligned and the
omission cannot happen.
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

#: The pass-through mouths the facade holds.
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
    """Not merely accepted and then forgotten (the name appears in the body)."""
    body = inspect.getsource(getattr(LlmGateway, name))
    head, _, tail = body.partition("self._ollama.")
    for arg in inspect.signature(getattr(OllamaClient, name)).parameters:
        if arg == "self":
            continue
        assert arg in tail, f"LlmGateway.{name} が {arg!r} を渡していない"
