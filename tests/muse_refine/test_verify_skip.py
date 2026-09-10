"""**絵が動いていない回は、再判定を走らせない。**（2026-09-10）

verify の仕事は「台帳が監督の意図と合っているか」。台帳が一つも動かず、監督の
一行も絵の話に見えないターン（「今日はありがとう」）では、比べる相手がいない。
それでも毎回 2,797字を読ませ、彼女の声で一言書かせていた —— 入力だけで約4秒。

**穴は開けない。** 絵の指示に見えるのに writer が何も書かなかった回（`missed`）
は、まさに verify に拾ってほしい回なので走らせる。

**測るのは「監督が動かしたぶん」だけ（2026-09-10 追記）。** ターンの頭と今の
台帳を比べると、彼女が表情を一語足しただけの回まで「動いた」になり、実測
5.9〜6.8秒の再判定が毎回走っていた。比べる相手は `after_director` —— 監督の
パッチを当て終えた地点。
"""
from __future__ import annotations

import inspect

from app.muse_refine import service


def test_the_gate_reads_both_conditions():
    src = inspect.getsource(service.chat)
    gate = src[src.index("moved_by_director = "):src.index("ok, comment, repair")]
    # 台帳が動いていない **かつ** 取りこぼしでもない、の両方が要る
    assert "if not moved_by_director and not missed:" in gate
    assert "verify_skipped" in gate


def test_a_missed_picture_line_still_gets_checked():
    """`missed` は「絵の指示に見えるのに writer が空だった」回。

    ここを飛ばすと、writer の取りこぼしを拾う唯一の段が消える。
    """
    src = inspect.getsource(service.chat)
    assert "force_repair_hint=missed" in src
    gate = src[src.index("moved_by_director = "):src.index("ok, comment, repair")]
    assert "missed" in gate


def test_changed_fields_is_what_decides():
    """判定は台帳の前後比較で行う（推測ではなく）。"""
    from app.muse_refine import ledger as L

    before = {**L.blank(), "beat": "standing"}
    same = {**L.blank(), "beat": "standing"}
    moved = {**L.blank(), "beat": "sitting"}
    assert L.changed_fields(before, same) == []
    assert L.changed_fields(before, moved) == ["beat"]


def test_the_gate_measures_the_director_not_her():
    """比べる相手は「監督のパッチを当て終えた地点」。

    彼女の propose は `guard_muse_propose` が空欄埋めに限っているので、監督の
    指示を上書きすることがない。その一語のために再判定を走らせる理由はない。
    """
    src = inspect.getsource(service.chat)
    # 監督のパッチを当てた直後に控えていること（女優の段より前）
    assert "after_director = dict(led)" in src
    assert src.index("after_director = dict(led)") < src.index("actress = await writer.actress_turn")
    gate = src[src.index("moved_by_director = "):src.index("ok, comment, repair")]
    assert "changed_fields(before, after_director)" in gate
