"""**二人のとき、立ち位置を決めて書き分ける。**（2026-09-10）

総監督「best practice で右と左って指示するといいらしい。それぞれがどっちにいるかを
決めて、かき分けてみよう。Mio (left), Asahi (right) もしくは top / bottom かな」。

人ごとにまとめて書くこと自体は既に入っている（`identity.assemble_from_boxes`・
2026-09-02）。足りないのは**どちらがどちら側か**。

**一人のときは何も足さない。** 監督が既に場所を言っている回も、そちらが勝つ。
"""
from __future__ import annotations

from app.muse import identity
from app.muse_refine import assemble, ledger as L

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
    """位置＝優先度。後ろに付けると効きが落ちる。"""
    got = assemble.assemble_prompt(_session(partner=True), BASE)
    mio = _line(got, "Mio")
    assert mio.startswith(f"Mio: {LEAD_EN},"), mio


def test_a_solo_shoot_gets_no_side():
    """**一人の会話・一人の絵を壊さない。**"""
    solo = {**L.blank(), "wearing": "cardigan", "beat": "standing", "scene": "room"}
    got = assemble.assemble_prompt(_session(partner=False), solo)
    assert "on the left" not in got and "on the right" not in got


def test_the_director_wins_when_he_named_a_side():
    """監督が置き場所を言った回は、既定を**片方も**足さない。"""
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
    assert f"Mio stands {LEAD_EN} of the frame; Asahi {PART_EN}." in got


def test_the_prose_stays_quiet_for_one_person():
    solo = {**L.blank(), "wearing": "cardigan", "beat": "standing", "scene": "room"}
    got = assemble.scene_prose(solo, partner=False, name_a="Mio", name_b="")
    assert "left" not in got and "right" not in got


def test_the_pair_lives_in_one_place():
    """**絵と日記が同じ正本を読む。** 別々に持つと片方を替えたとき食い違う。"""
    assert identity.LEAD_SIDE in identity.SIDE_WORDS
    # 主演と相方は必ず反対側
    assert identity.side_of(lead=True) != identity.side_of(lead=False)
    # 日記側（`_which_one_is_me`）も同じ関数を読んでいる
    import inspect

    from app.muse import service as muse_service
    src = inspect.getsource(muse_service._which_one_is_me)
    assert "identity.side_of" in src


def test_the_lead_is_on_the_right_today():
    """総監督のご指定（2026-09-10）。替えるときはここも一緒に替える。"""
    assert identity.LEAD_SIDE == "right"
