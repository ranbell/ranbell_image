"""**The wardrobe lock and the ban list.** (2026-09-09)

The Showrunner: "the lock mechanism may have a problem. There was a phenomenon
where **clothes come off and go on again with no instruction**".

In the morning's investigation it was reproduced four steps in a row without a
model:

    1. the last garment comes off   wearing = '', banned = ['cardigan']
    2. the actress fills the empty field   fill-empty passes    <- dressed with no
                                                                   instruction
    3. but it does not reach the picture   the ban still drops it
                                           <- the ledger is dressed, the picture is not
    4. the director says it again   -> 2 and 3 again            <- the on-off loop

Steps 1 and 2 **stay as designed** (the Showrunner: "she may fill it, but keep a
record"). What was fixed is 3, copying the rule the current Muse already has for
the same defect (`muse.service.banned_now`: "what the ledger names right now is
not banned").

Matching was also moved to **word boundaries**. On a substring match, taking off
a `shirt` erased a `skirt`, and banning `top` erased `rooftop` as well.
"""
from __future__ import annotations

from app.muse import ledger as L, talk


# ── ①② 仕様として残す側 ───────────────────────────────────────────────
def test_dropping_the_last_garment_still_empties_the_slot():
    led = L.apply_patch({**L.blank(), "wearing": "cardigan"},
                        {"wearing_drop": "cardigan"})
    assert led["wearing"] == ""


def test_she_may_still_dress_an_empty_slot():
    """The Showrunner's call — what she decides is reflected. The record is kept on the
    service side."""
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
    """**The second half of the Showrunner's report.** A garment taken off and put back
    on never reached the picture."""
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
