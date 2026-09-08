"""**服のロックと禁止。**（2026-09-09）

総監督「ロック機構に問題があるかも。**指示がないのに服の脱着が繰り返される**
現象があった」。

朝の調査で、モデルを使わずに四つ続けて再現した:

    ① 最後の一枚を脱ぐ   wearing = ''、banned = ['cardigan']
    ② 空いた欄を女優が埋める  fill-empty が通る       ← 指示がないのに着る
    ③ でも絵には出ない     禁止が残ったまま落とす    ← 台帳は着ている、絵は着ていない
    ④ 監督が言い直す      → ②③のくり返し           ← 脱着の反復

①②は**仕様として残す**（総監督「埋めてよいが、記録に残す」）。直したのは③で、
現行 Muse が同じ欠陥に対して持っている規則をそのまま写した
（`muse.service.banned_now`「台帳がいま名指ししているものは禁止ではない」）。

あわせて、照合を**語の境目**にした。部分一致だと `shirt` を脱いだときに
`skirt` が消え、`top` を禁止すると `rooftop` まで消えていた。
"""
from __future__ import annotations

from app.muse_refine import ledger as L, talk


# ── ①② 仕様として残す側 ───────────────────────────────────────────────
def test_dropping_the_last_garment_still_empties_the_slot():
    led = L.apply_patch({**L.blank(), "wearing": "cardigan"},
                        {"wearing_drop": "cardigan"})
    assert led["wearing"] == ""


def test_she_may_still_dress_an_empty_slot():
    """総監督の判断 —— 彼女が決めたことは反映する。記録は service 側で残す。"""
    led = {**L.blank(), "wearing": ""}
    got = L.guard_muse_propose({"wearing": "cardigan, white shirt"}, led,
                               director_keys=set())
    assert got == {"wearing": "cardigan, white shirt"}


def test_she_may_not_touch_a_settled_outfit():
    led = {**L.blank(), "wearing": "school uniform"}
    assert L.guard_muse_propose({"wearing": "swimsuit"}, led,
                                director_keys=set()) == {}


# ── ③ 直した側：台帳が着ているものは絵に出る ──────────────────────────
def test_a_garment_the_ledger_names_again_comes_back_to_the_picture():
    """**総監督の報告の後半。** 一度脱いだ服を着直しても絵に出なかった。"""
    session = {"banned": ["cardigan"]}
    led = {**L.blank(), "wearing": "cardigan, white shirt"}
    out = talk.filter_banned_tags(
        session, ["cardigan", "white_shirt", "skirt"], ledger=led,
    )
    assert out == ["cardigan", "white_shirt", "skirt"]
    # 禁止そのものは消えていない —— 台帳が黙れば、また効く。
    assert session["banned"] == ["cardigan"]
    assert talk.filter_banned_tags(session, ["cardigan"], ledger=L.blank()) == []


def test_underscores_and_spaces_are_the_same_garment():
    led = {**L.blank(), "wearing": "white shirt"}
    assert talk.filter_banned_tags(
        {"banned": ["white_shirt"]}, ["white_shirt"], ledger=led,
    ) == ["white_shirt"]


def test_without_a_ledger_the_ban_still_bites():
    assert talk.filter_banned_tags({"banned": ["hoodie"]}, ["hoodie", "jeans"]) == ["jeans"]


# ── 語の境目 ──────────────────────────────────────────────────────────
def test_dropping_a_shirt_does_not_take_the_skirt():
    led = L.apply_patch(
        {**L.blank(), "wearing": "white shirt, t-shirt, skirt, shirt dress"},
        {"wearing_drop": "shirt"},
    )
    assert led["wearing"] == "skirt"


def test_a_short_ban_does_not_swallow_longer_words():
    out = talk.filter_banned_tags(
        {"banned": ["top"]},
        ["tank_top", "rooftop", "laptop", "stopwatch", "skirt"],
    )
    assert out == ["rooftop", "laptop", "stopwatch", "skirt"]


def test_word_hit_reads_a_phrase_not_a_fragment():
    assert talk.word_hit("cardigan", "grey cardigan")
    assert talk.word_hit("white shirt", "white_shirt")
    assert not talk.word_hit("shirt", "skirt")
    assert not talk.word_hit("", "anything")
