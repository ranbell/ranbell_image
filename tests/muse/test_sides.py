"""**With two people, fix where each one stands and write them apart.**
(2026-09-10)

The Showrunner: "best practice apparently says to say right and left. Let us decide
which of them is where and write them apart. Mio (left), Asahi (right), or maybe
top / bottom."

Writing each person's words together is already in (`identity.assemble_from_boxes`,
2026-09-02). What is missing is **which one is on which side**.

**Solo adds nothing.** And on a turn where the director already named a place, the
director wins.
"""
from __future__ import annotations

from app.muse import identity
from app.muse import assemble, ledger as L

#: 主演／相方がどちら側か。**直書きしない** —— `identity.LEAD_SIDE` を替えたら
#: 試験もそのまま追従する（総監督が左右を入れ替えたときに壊れないため）。
LEAD_EN = identity.side_of(lead=True)[0]
PART_EN = identity.side_of(lead=False)[0]

CHAR = {"character_id": "a", "name": "Mio", "name_ja": "各務 みお",
        "identity_tags": ["silver_hair", "bob_cut", "flat_chest"]}
PART = {"character_id": "b", "name": "Asahi", "name_ja": "倉田 あさひ",
        "identity_tags": ["light_green_hair", "hair_up", "medium_breasts"]}
BASE = {**L.blank(), "wearing": "maid outfit", "beat": "holding tray",
        "scene": "cafe", "wearing_b": "maid outfit", "beat_b": "holding menu"}


def _session(*, partner: bool):
    return {"session_id": "s", "character": dict(CHAR),
            "partner_character": dict(PART) if partner else {},
            "inputs": {"locale": "ja"}, "refine_ledger": dict(BASE), "banned": []}


def _line(prompt: str, who: str) -> str:
    for l in prompt.splitlines():
        if l.startswith(f"{who}:"):
            return l
    return ""


def test_two_in_frame_get_a_side_each():
    # 最終形は `anima.format_for_anima` が下線を空白に戻すので、絵に渡るのは
    # `on the right` のような自然文になる。
    got = assemble.assemble_prompt(_session(partner=True), BASE)
    assert LEAD_EN in _line(got, "Mio")
    assert PART_EN in _line(got, "Asahi")
    assert LEAD_EN != PART_EN, "二人が同じ側になっている"


def test_the_side_comes_first_in_her_run():
    """Position is priority. Appended at the end it bites less."""
    got = assemble.assemble_prompt(_session(partner=True), BASE)
    mio = _line(got, "Mio")
    assert mio.startswith(f"Mio: {LEAD_EN},"), mio


def test_a_solo_shoot_gets_no_side():
    """**The solo conversation and the solo picture do not break.**"""
    solo = {**L.blank(), "wearing": "cardigan", "beat": "standing", "scene": "room"}
    got = assemble.assemble_prompt(_session(partner=False), solo)
    assert "on the left" not in got and "on the right" not in got


def test_the_director_wins_when_he_named_a_side():
    """On a turn where the director named a placement, the default is not added for
    **either** of them."""
    led = {**BASE, "beat_b": "standing on the right, holding menu"}
    got = assemble.assemble_prompt(_session(partner=True), led)
    # 監督の言葉（`standing on the right`）はそのまま残る。既定が**足されない**
    # ことを見る —— 誰の行も、こちらが入れた立ち位置で始まっていないこと。
    for who in ("Mio", "Asahi"):
        line = _line(got, who)
        assert not line.startswith(f"{who}: {LEAD_EN}"), line
        assert not line.startswith(f"{who}: {PART_EN}"), line
    assert "on the right" in _line(got, "Asahi")   # 監督の言葉は生きている
    assert "left" not in got                        # 反対側は生えていない


def test_japanese_side_words_count_too():
    led = {**BASE, "beat": "奥に立つ"}
    assert assemble._sides_named(led) is True
    led2 = {**BASE, "frame": "右寄りの構図"}
    assert assemble._sides_named(led2) is True
    assert assemble._sides_named(BASE) is False


def test_the_prose_says_who_is_where():
    got = assemble.scene_prose(BASE, partner=True, name_a="Mio", name_b="Asahi")
    # 左→右の順で読ませるので、左にいるほうが先に出る。
    left_name, right_name = (
        ("Asahi", "Mio") if identity.LEAD_SIDE == "right" else ("Mio", "Asahi")
    )
    left_en, right_en = identity.SIDE_WORDS["left"][0], identity.SIDE_WORDS["right"][0]
    assert f"{left_name} stands {left_en} of the frame; {right_name} {right_en}." in got


def test_the_prose_stays_quiet_for_one_person():
    solo = {**L.blank(), "wearing": "cardigan", "beat": "standing", "scene": "room"}
    got = assemble.scene_prose(solo, partner=False, name_a="Mio", name_b="")
    assert "left" not in got and "right" not in got


def test_the_pair_lives_in_one_place():
    """**The picture and the diary read the same source of truth.** Held separately,
    changing one makes them disagree."""
    assert identity.LEAD_SIDE in identity.SIDE_WORDS
    # 主演と相方は必ず反対側
    assert identity.side_of(lead=True) != identity.side_of(lead=False)
    # 日記側（`_which_one_is_me`）も同じ関数を読んでいる
    import inspect

    from app.muse import shared as muse_service
    src = inspect.getsource(muse_service._which_one_is_me)
    assert "identity.side_of" in src


def test_the_lead_is_on_the_right_today():
    """The Showrunner's choice (2026-09-10). Change it and change this with it."""
    assert identity.LEAD_SIDE == "right"
