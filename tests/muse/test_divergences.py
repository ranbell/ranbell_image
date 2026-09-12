"""**「missing」が嘘をつかないこと。**（2026-09-10）

総監督「テスト中だけど、ずっと missing と出ているけど理由は？」。

値は絵にちゃんと入っていた。突き合わせ方が揃っていなかっただけ:

    台帳          casual_clothes      ← writer は danbooru 風に下線で書く
    craft.prompt  casual clothes      ← `anima.format_for_anima` が最後に
                                         下線を空白へ戻す
    重なり        （無し）→ 「in ledger, missing in craft.prompt」

もう一つ、会話のターンでは `craft.prompt` が**一手ぶん古い**（撮る時まで
組み直しを待つ `assemble.touch_craft`）。そこで比べると毎ターン嘘が並ぶ。
"""
from __future__ import annotations

from app.muse import ledger as L, pipeline_view as P


def _session(led: dict, prompt: str, *, stale: bool = False, board: str = ""):
    return {
        "session_id": "s",
        "refine_ledger": {**L.blank(), **led},
        "craft": {"prompt": prompt, "stale": stale},
        "board": {"prompt": board} if board else {},
    }


def test_an_underscored_value_counts_as_present():
    """台帳の `casual_clothes` と絵の `casual clothes` は同じもの。"""
    s = _session({"wearing": "casual_clothes"},
                 "2girls, Mio: casual clothes, big smile,")
    assert P._divergences(s) == []


def test_a_hyphenated_value_counts_too():
    s = _session({"light": "High-key_natural_light"},
                 "bowling alley, High-key natural light, bowling lane")
    assert P._divergences(s) == []


def test_an_exact_underscored_match_still_counts():
    """絵の側が下線のままでも拾う（両方の書き方を返している）。"""
    s = _session({"wearing": "maid_outfit"}, "1girl, maid_outfit, cafe")
    assert P._divergences(s) == []


def test_a_value_that_really_is_missing_is_still_reported():
    """**穴は開けない。** 本当に落ちている欄は今まで通り言う。"""
    s = _session({"wearing": "sailor uniform"}, "1girl, cardigan, rooftop")
    got = [d["field"] for d in P._divergences(s)]
    assert "wearing" in got


def test_a_conversation_turn_is_not_compared_at_all():
    """会話中は絵が一手ぶん古い。比べても意味がないので黙る。"""
    s = _session({"wearing": "sailor uniform"}, "1girl, cardigan, rooftop",
                 stale=True)
    assert P._divergences(s) == []


def test_the_board_is_still_compared_while_stale():
    """撮った板は古くならない —— そちらのずれは会話中でも言う。"""
    s = _session({"wearing": "sailor uniform"}, "1girl, cardigan",
                 stale=True, board="1girl, cardigan, rooftop")
    got = [(d["kind"], d["field"]) for d in P._divergences(s)]
    assert ("board_vs_ledger", "wearing") in got
    assert not any(k == "prompt_vs_ledger" for k, _ in got)


def test_the_partner_fields_are_checked_the_same_way():
    s = _session({"wearing_b": "maid_outfit, lace_apron"},
                 "Asahi: maid outfit, lace apron,")
    assert P._divergences(s) == []
