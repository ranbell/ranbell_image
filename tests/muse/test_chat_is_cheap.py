"""**会話だけの回に、絵を組み直さない。**（2026-09-10）

総監督「撮影に入らないときの会話のみの回答はもっと早くしてほしい」。

実機の `stage_ms` を読むと、会話だけの一手にこれだけ乗っていた:

    writer                4.45s   台帳を書く（要る）
    quality_enrich        4.42s   ┐ `rebuild_craft` の中身。どちらも模型
    prose_densify         6.93s   ┘
    actress              21.93s   彼女が喋る（要る）
    assemble_after_propose 9.55s  彼女が表情を足したので、また組み直し
    verify                6.81s
    ──────────────────────────── 合計 ≈54秒

組み上げた散文とタグを使うのは試し撮りと本番だけで、そちらは自前で
`rebuild_craft` を呼ぶ。**会話の途中で組む理由がない。**

ここで見るのは呼び出しの形だけ（模型は叩かない）。
"""
from __future__ import annotations

import inspect

from app.muse import assemble, service


def _code(fn) -> str:
    """コメントと文字列を落とした、実際に走る行だけ。

    **自分が書いた説明に引っかからないため。** ここの説明文には
    `rebuild_craft` も `quality_enrich` も出てくるので、素の
    `inspect.getsource` を検索すると必ず当たってしまう。
    """
    import io, tokenize

    out: list[str] = []
    src = inspect.getsource(fn)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


def test_chat_never_rebuilds_the_craft():
    """`chat` の中に `rebuild_craft` が一つも残っていないこと。"""
    code = _code(service.chat)
    assert "rebuild_craft" not in code
    assert "touch_craft" in code


def test_the_three_seams_all_use_the_cheap_one():
    """女優の前・propose の後・自己修復の後 —— 三箇所とも。"""
    code = _code(service.chat)
    assert code.count("assemble . touch_craft ( session )") == 3


def test_taking_a_picture_still_rebuilds():
    """試し撮りと本番は、今まで通り組み直してから積む。"""
    for fn in (service.start_board, service.start_shoot):
        assert "rebuild_craft" in _code(fn), fn.__name__


def test_the_rebuild_button_still_rebuilds():
    """「プロンプト再生成」は本物の組み直しのまま。"""
    assert "rebuild_craft" in _code(service.rebuild)


def test_touch_craft_uses_no_model():
    """純関数だけ。`ollama` を受け取らない —— 受け取れないので叩けない。"""
    params = list(inspect.signature(assemble.touch_craft).parameters)
    assert params == ["session"]
    code = _code(assemble.touch_craft)
    for forbidden in ("await", "generate_text", "quality_enrich", "densify"):
        assert forbidden not in code, forbidden


def test_touch_craft_moves_now_and_flags_stale():
    session = {
        "inputs": {"locale": "ja"},
        "refine_ledger": {"wearing": "white shirt", "scene": "rooftop"},
        "craft": {"prompt": "（前の回に組んだもの）", "stale": False},
    }
    assemble.touch_craft(session)
    craft = session["craft"]
    # 正本の一行とタグは動く
    assert craft["now"]
    assert "white_shirt" in craft["tags"] or "white shirt" in craft["tags"]
    # 組み上げたプロンプトはそのまま残る（消さない）
    assert craft["prompt"] == "（前の回に組んだもの）"
    # 古いという旗が立つ
    assert craft["stale"] is True


def test_rebuild_lowers_the_flag():
    """撮る直前に組み直したら、もう古くない。

    実際に組み直して旗を見る（`ollama=None` なので模型は一度も動かない）。
    """
    import asyncio

    session = {
        "inputs": {"locale": "ja"},
        "refine_ledger": {"wearing": "white shirt", "scene": "rooftop"},
        "craft": {},
    }
    assemble.touch_craft(session)
    assert session["craft"]["stale"] is True

    asyncio.run(assemble.rebuild_craft(None, None, session))
    assert session["craft"]["stale"] is False
    assert session["craft"]["prompt"]
