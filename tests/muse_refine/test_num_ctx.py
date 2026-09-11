"""**文脈長を判定係と揃える。**（2026-09-10）

総監督「純粋に推論に時間がかかっていると思う。Muse の会話ターンで 20-30sec
かかるようです」。実体は**モデルの読み直し**だった。

Ollama は文脈長が違うと別インスタンスとして読み直す。実測（26B・実機）:

    同じ長さを続ける    1回目 21.8s（読込 20.4s）→ 2回目 0.2s（読込 0.0s）
    長さを交互に変える   毎回 12.8s（読込 11.3s）

Refine は `num_ctx` を一つも渡しておらず、判定係（`muse.chain._call`）は
`ollama_num_ctx` を渡す。1ターンの中で交互になり、**最低2回の読み込み**が
乗っていた。生成そのものは 35〜48tps で正常だった。
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.muse import service as muse_service
from app.muse_refine.ctx import refine_num_ctx

PKG = Path(__file__).resolve().parents[2] / "backend" / "app" / "muse_refine"


def test_the_same_number_as_the_clerk():
    """**同じ式でなければ意味がない。** 数字が一つでも違えば読み直しになる。"""
    session = {"inputs": {}, "_runtime_cfg": {"ollama_num_ctx": 16384}}
    assert refine_num_ctx(session) == 16384
    assert muse_service._num_ctx({}, {"ollama_num_ctx": 16384}) == 16384
    # セッション側の指定が勝つのも同じ
    assert refine_num_ctx({"inputs": {"num_ctx": 8192},
                           "_runtime_cfg": {"ollama_num_ctx": 16384}}) == 8192
    assert muse_service._num_ctx({"num_ctx": 8192},
                                 {"ollama_num_ctx": 16384}) == 8192


def test_no_number_means_no_option():
    """設定が読めないときは何も渡さない（既定に任せる）。"""
    assert refine_num_ctx({}) is None
    assert refine_num_ctx(None) is None


def test_every_llm_call_carries_a_context_length():
    """**席が増えたら落ちる。** 一つ素通しがあれば、そこで読み直しになる。"""
    missing = []
    seen = 0
    for path in sorted(PKG.glob("*.py")):
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
