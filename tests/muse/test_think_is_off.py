"""**thinking は明示して切る。**（2026-09-07）

総監督「ollama での画像生成時の streaming で絵が出ないバグがあるので修正して」
「猛烈に遅いので、踏んでそうですね」。

実測（26B・同じプロンプト・n=2）:

    think 未指定   14.1 / 15.3 秒   67 / 91 字
    think=False     1.1 /  1.6 秒  141 / 146 字

**10倍遅く、しかも薄い。** 1ターンに数回叩くので分単位の待ちになり、描画まで
届かない。通しの実測でも、書き上げが 2分で返らなかったのが 2回で 10.4秒に
なった。CLAUDE.md の一つ目の踏み抜きどころ（非ストリーミング＋既定の
`num_predict` で thinking が出力枠を食う）そのもの。

Muse は `chain._call` が毎回 `think=False` を送っている。ここはそれを
**呼び出しの形として**守る —— 新しい呼び出しが増えたときに落ちるように。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2] / "backend" / "app" / "muse"


#: 模型を叩く口。`ollama.generate_*` の直呼びだけでなく、**classic の
#: `chain` 経由**も数える —— スタジオ撮り（班）は `chain._call` を通るので、
#: `generate_` だけ見ていると素通りする（2026-09-12 に気づいた穴）。
_VIA_CHAIN = {"_call", "_call_seeing"}


def _generate_calls():
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
    """条文ではなく、実際に渡っていること。"""
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
    """**`chain._call` は免除するが、代わりに署名を縛る。**

    門の内側（`chain.py:_call`）は `think` と `num_ctx` を**受けて渡す**側なので、
    呼び出しの形で `think=False` / `options=` を探す走査には写らない。素通しに
    見えるのはそのためで、ここだけは免除する（パッケージを `muse` に畳んだ
    2026-09-12 に、走査範囲が門の実装まで広がって気づいた）。

    免除するぶん、**既定値を持たせない**ことを縛る —— 既定があると、新しい
    呼び出しが黙って thinking 付き・文脈長なしで通ってしまう。
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
    # 受けたものをそのまま下へ渡していること（勝手に捨てていない）
    src = ast.parse(inspect.getsource(chain._call))
    text = ast.unparse(src)
    assert "think=think" in text
    assert "num_ctx=num_ctx" in text
