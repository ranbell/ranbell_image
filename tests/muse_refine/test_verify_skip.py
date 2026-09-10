"""**絵が動いていない回は、再判定を走らせない。**（2026-09-10）

verify の仕事は「台帳が監督の意図と合っているか」。台帳が一つも動かず、監督の
一行も絵の話に見えないターン（「今日はありがとう」）では、比べる相手がいない。
それでも毎回 2,797字を読ませ、彼女の声で一言書かせていた —— 入力だけで約4秒。

**穴は開けない。** 絵の指示に見えるのに writer が何も書かなかった回（`missed`）
は、まさに verify に拾ってほしい回なので走らせる。
"""
from __future__ import annotations

import inspect

from app.muse_refine import service


def test_the_gate_reads_both_conditions():
    src = inspect.getsource(service.chat)
    gate = src[src.index("moved_now = "):src.index("ok, comment, repair")]
    # 台帳が動いていない **かつ** 取りこぼしでもない、の両方が要る
    assert "if not moved_now and not missed:" in gate
    assert "verify_skipped" in gate


def test_a_missed_picture_line_still_gets_checked():
    """`missed` は「絵の指示に見えるのに writer が空だった」回。

    ここを飛ばすと、writer の取りこぼしを拾う唯一の段が消える。
    """
    src = inspect.getsource(service.chat)
    assert "force_repair_hint=missed" in src
    gate = src[src.index("moved_now = "):src.index("ok, comment, repair")]
    assert "missed" in gate


def test_changed_fields_is_what_decides():
    """判定は台帳の前後比較で行う（推測ではなく）。"""
    from app.muse_refine import ledger as L

    before = {**L.blank(), "beat": "standing"}
    same = {**L.blank(), "beat": "standing"}
    moved = {**L.blank(), "beat": "sitting"}
    assert L.changed_fields(before, same) == []
    assert L.changed_fields(before, moved) == ["beat"]
