"""**Thinking is switched off explicitly.** (2026-09-07)

The Showrunner: "there is a bug where no picture appears when streaming image
generation through ollama — please fix it", "it is furiously slow, so you are
probably hitting it".

Measured (26B, same prompt, n=2):

    think unspecified   14.1 / 15.3 s   67 / 91 chars
    think=False          1.1 /  1.6 s  141 / 146 chars

**Ten times slower, and thinner with it.** It is called several times a turn, so
the wait runs into minutes and never reaches the render. End to end, a write-up
that had not returned in two minutes came back in 10.4 seconds twice. This is the
first of CLAUDE.md's pitfalls exactly (non-streaming plus a default `num_predict`
lets thinking eat the output budget).

In Muse, `chain._call` sends `think=False` every time. What is pinned here is
**the shape of the call** — so that a new call site fails this test.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "backend" / "app" / "muse"


#: The mouths that hit the model. Not only direct `ollama.generate_*` calls but also
#: **calls through classic's `chain`** — a studio shoot (with a crew) goes through
#: `chain._call`, so watching only `generate_` lets it pass (a hole noticed on
#: 2026-09-12).
_VIA_CHAIN = {"_call", "_call_seeing"}


def _generate_calls():
    for path in sorted(PKG.glob("*.py")):
        if path.name == "chain.py":
            continue  # the gate's implementation: it receives and forwards, so it
                      # does not appear in the shape of a call
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute):
                continue
            if fn.attr.startswith("generate_") or fn.attr in _VIA_CHAIN:
                yield path.name, node


def test_every_llm_call_says_think_false():
    missing = []
    seen = 0
    for name, node in _generate_calls():
        seen += 1
        kw = {k.arg: k.value for k in node.keywords}
        val = kw.get("think")
        if not (isinstance(val, ast.Constant) and val.value is False):
            missing.append(f"{name}:{node.lineno}")
    assert seen >= 7, "呼び出しが見つからない —— 試験のほうが古い"
    assert not missing, (
        "think=False を送っていない呼び出し: " + ", ".join(missing)
    )


@pytest.mark.asyncio
async def test_the_flag_actually_reaches_the_client():
    """Not the contract — that it actually arrives."""
    from app.muse import writer

    seen = {}

    class _Ollama:
        async def generate_text(self, prompt, **kw):
            seen.update(kw)
            return '{"beat": "standing"}'

    await writer.write_patch(
        _Ollama(), model="m", user_line="立って", ledger={"beat": ""},
    )
    assert seen.get("think") is False


def test_the_door_itself_demands_both_knobs():
    """**`chain._call` is exempt, and its signature is pinned instead.**

    Inside the gate (`chain.py:_call`) `think` and `num_ctx` are **received and
    forwarded**, so a scan looking for `think=False` / `options=` at call sites
    does not see them. That is why it looks like a pass-through, and it is the one
    exemption (noticed when folding the package into `muse` on 2026-09-12 widened
    the scan's range to the gate's own implementation).

    In exchange for the exemption, it is pinned to **carry no defaults** — with a
    default, a new call would quietly go through with thinking on and no context
    length.
    """
    import ast
    import inspect

    from app.muse import chain

    sig = inspect.signature(chain._call)
    for knob in ("think", "num_ctx"):
        assert knob in sig.parameters, knob
        p = sig.parameters[knob]
        assert p.kind is p.KEYWORD_ONLY, f"{knob} は名前で渡させる"
        if knob == "think":
            assert p.default is inspect.Parameter.empty, "think に既定を持たせない"
    # What it receives is handed straight on (never quietly dropped)
    src = ast.parse(inspect.getsource(chain._call))
    text = ast.unparse(src)
    assert "think=think" in text
    assert "num_ctx=num_ctx" in text
