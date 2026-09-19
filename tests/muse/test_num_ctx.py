"""**The context length is kept the same as the clerk's.** (2026-09-10)

The Showrunner: "I think it is purely inference taking time. A Muse conversation
turn seems to take 20-30 sec." What it really was: **the model being reloaded**.

Ollama reloads as a separate instance when the context length differs. Measured
(26B, live):

    same length repeated     1st 21.8s (load 20.4s) -> 2nd 0.2s (load 0.0s)
    lengths alternating      12.8s every time (load 11.3s)

Refine passed no `num_ctx` at all while the clerk (`muse.chain._call`) passes
`ollama_num_ctx`. They alternated within a single turn, carrying **at least two
loads**. Generation itself was fine at 35-48 tps.
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.muse import shared as muse_service
from app.muse.ctx import refine_num_ctx

PKG = Path(__file__).resolve().parents[2] / "backend" / "app" / "muse"


def test_the_same_number_as_the_clerk():
    """**Only the same expression counts.** One different number and it reloads."""
    session = {"inputs": {}, "_runtime_cfg": {"ollama_num_ctx": 16384}}
    assert refine_num_ctx(session) == 16384
    assert muse_service._num_ctx({}, {"ollama_num_ctx": 16384}) == 16384
    # セッション側の指定が勝つのも同じ
    assert refine_num_ctx({"inputs": {"num_ctx": 8192},
                           "_runtime_cfg": {"ollama_num_ctx": 16384}}) == 8192
    assert muse_service._num_ctx({"num_ctx": 8192},
                                 {"ollama_num_ctx": 16384}) == 8192


def test_no_number_means_no_option():
    """When the setting cannot be read, nothing is passed (leave it to the default)."""
    assert refine_num_ctx({}) is None
    assert refine_num_ctx(None) is None


def test_every_llm_call_carries_a_context_length():
    """**A new seat fails this.** One call without it and the model reloads there."""
    missing = []
    seen = 0
    for path in sorted(PKG.glob("*.py")):
        if path.name == "chain.py":
            continue  # 門の実装。受けて渡す側なので呼び出しの形には写らない
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not isinstance(fn, ast.Attribute):
                continue
            # **classic の `chain` 経由も数える。** スタジオ撮り（班）は
            # `chain._call` / `chain.run_banter` を通るので、`generate_` だけ
            # 見ていると素通りする（2026-09-12 に気づいた穴）。あちらは
            # `options` ではなく `num_ctx=` という名前で受ける。
            via_chain = fn.attr in ("_call", "_call_seeing", "run_banter")
            if not (fn.attr.startswith("generate_") or via_chain):
                continue
            seen += 1
            kw = {k.arg for k in node.keywords}
            wanted = "num_ctx" if via_chain else "options"
            if wanted not in kw:
                missing.append(f"{path.name}:{node.lineno}")
    assert seen >= 8, "呼び出しが見つからない —— 試験のほうが古い"
    assert not missing, f"文脈長を渡していない呼び出し: {missing}"
