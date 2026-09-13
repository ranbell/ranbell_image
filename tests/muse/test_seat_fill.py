"""**席の言葉が着地する道。**（2026-09-14）

総監督「美術や色彩などでいい提案しているのに、それらがプロンプトに乗ってこないのは
やっぱりもったいない。乗せる方向で検討して」。

実機（`1b78ac2b`「公園でランニング」・14席）で起きていたこと:

    美術「芝生に転がったままの砂混じりのサンダル」   → `bg` に無い
    特殊効果「この公園に漂う陽炎を混ぜ込んで」        → `atmosphere` は空
    色彩設計「この画面に色の芯を置いておかないと」    → `look` は空

席は台帳に触れず、材料を台本係へ渡す。その台本係は**監督の一行にある欄しか直さない**
ので、名指しされなかった欄の craft は構造的にどこにも着地しなかった。台帳25本の
実測で `look` は 88%、`atmosphere` は 76% のセッションが空のまま。

ここで守るのは**着地の道と、その節度**。監督の言葉を押しのけない、既にある語を
消さない、際限なく増やさない。
"""
from __future__ import annotations

from app.muse import crew_room as C
from app.muse import ledger as L


def _session(**kw):
    s = {"inputs": {"locale": "ja"}, "banned": [], "refine_ledger": L.blank()}
    s.update(kw)
    return s


def _floor(*pairs):
    """(欄, CRAFT) の並びを、`run_table` が返す形にする。"""
    return [{"muse_id": f"seat{i}", "role": "", "name": f"席{i}",
             "field": field, "say": "…", "craft": craft, "kind": "seat"}
            for i, (field, craft) in enumerate(pairs)]


# ── 乗ること ────────────────────────────────────────────────────────────

def test_the_prop_seat_gets_its_sandal_into_the_background():
    """実機で落ちた一言。**埋まっている欄にも、まだ無いものは足せる。**"""
    led = {**L.blank(), "bg": "green grass, plastic_bottle, sports_drink"}
    patch, landed = C.seat_fill(
        _session(), _floor(("bg", "sandy_sandal, forgotten_towel | 芝生の忘れ物")),
        ledger=led, taken=set(),
    )
    assert "sandy_sandal" in patch["bg"]
    assert patch["bg"].startswith("green grass, plastic_bottle, sports_drink"), \
        "既にある語は順番ごと残す"
    assert landed["bg"] == ["sandy_sandal", "forgotten_towel"]


def test_the_empty_sticky_fields_finally_get_filled():
    """`look` 88% / `atmosphere` 76% が空だった —— 空なら席の語で埋める。"""
    patch, _ = C.seat_fill(
        _session(),
        _floor(("look", "amber_theme, clean_lineart, cel_shading | 色の芯"),
               ("atmosphere", "heat_haze, shimmering_air | 陽炎")),
        ledger=L.blank(), taken=set(),
    )
    assert patch["look"] == "amber_theme, clean_lineart, cel_shading"
    assert patch["atmosphere"] == "heat_haze, shimmering_air"


def test_two_seats_on_one_field_land_in_seat_order():
    """`look` は色彩・線画・調整の三席が持つ。**席順で先着**、`craft_block` と同じ。"""
    patch, _ = C.seat_fill(
        _session(),
        _floor(("look", "amber_theme | 色"), ("look", "clean_lineart | 線")),
        ledger=L.blank(), taken=set(),
    )
    assert patch["look"] == "amber_theme, clean_lineart"


# ── 乗らないこと ────────────────────────────────────────────────────────

def test_the_field_the_director_named_is_left_alone():
    """**監督の言葉が勝つ。** その回に書かれた欄に、席は口を出さない。"""
    led = {**L.blank(), "light": "backlighting"}
    patch, landed = C.seat_fill(
        _session(), _floor(("light", "hard_shadow | 硬く")),
        ledger=led, taken={"light"},
    )
    assert patch == {} and landed == {}


def test_the_body_fields_are_never_touched():
    """姿勢・表情・服は入れない —— 一つの体の掃除を壊さないため。"""
    patch, _ = C.seat_fill(
        _session(),
        _floor(("beat", "standing, hand_on_hip | 姿勢"),
               ("expression", "soft_smile | 顔"),
               ("wearing", "apron | 服")),
        ledger=L.blank(), taken=set(),
    )
    assert patch == {}


def test_what_the_showrunner_refused_does_not_come_back():
    """総監督が拒否した語は、席の口からも戻らない。"""
    s = _session(banned=["towel"])
    patch, _ = C.seat_fill(
        s, _floor(("bg", "blue_towel, small_stone | 忘れ物")),
        ledger=L.blank(), taken=set(),
    )
    assert "towel" not in patch.get("bg", "")
    assert "small_stone" in patch["bg"]


def test_the_same_thing_is_not_added_twice():
    """語の境目で見る（`talk.word_hit`）—— `shirt` が `skirt` に当たらないのと同じ一本。"""
    led = {**L.blank(), "bg": "coffee cup, cheesecake"}
    patch, landed = C.seat_fill(
        _session(), _floor(("bg", "coffee cup, silver_spoon | 同じカップ")),
        ledger=led, taken=set(),
    )
    assert landed["bg"] == ["silver_spoon"]


# ── 節度 ────────────────────────────────────────────────────────────────

def test_a_settled_field_grows_by_two_words_a_turn():
    led = {**L.blank(), "bg": "green grass"}
    patch, landed = C.seat_fill(
        _session(), _floor(("bg", "a_one, b_two, c_three, d_four | 四つ出した")),
        ledger=led, taken=set(),
    )
    assert len(landed["bg"]) == C.SEAT_FILL_PER_TURN == 2


def test_a_full_field_stops_growing_without_losing_anything():
    """**上限は「増やさない」約束で、「削る」約束ではない。**

    監督の言葉が押し出されては困るので、いっぱいなら足すのをやめるだけ。
    """
    full = ", ".join(f"thing_{i}" for i in range(C.SEAT_FILL_CAP))
    led = {**L.blank(), "bg": full}
    patch, landed = C.seat_fill(
        _session(), _floor(("bg", "late_arrival | もう入らない")),
        ledger=led, taken=set(),
    )
    assert patch == {} and landed == {}

    # 一つだけ空きがあるなら、一つだけ入る
    led2 = {**L.blank(), "bg": ", ".join(f"thing_{i}" for i in range(C.SEAT_FILL_CAP - 1))}
    patch2, landed2 = C.seat_fill(
        _session(), _floor(("bg", "one_more, and_another | 二つ出した")),
        ledger=led2, taken=set(),
    )
    assert landed2["bg"] == ["one_more"]


def test_the_field_label_never_rides_in():
    """`CRAFT: BG: …` と書かれても、欄名は入口で剥がれる（`craft_tags`）。"""
    patch, _ = C.seat_fill(
        _session(), _floor(("bg", "BG: paper_menu, worn_edges | 小道具")),
        ledger=L.blank(), taken=set(),
    )
    assert "BG:" not in patch["bg"]
    assert "paper_menu" in patch["bg"]


def test_no_crew_no_fill():
    """席が喋っていない回は何も起きない（一人撮り・W撮りはここを通らない）。"""
    assert C.seat_fill(_session(), [], ledger=L.blank(), taken=set()) == ({}, {})


def test_the_turn_asks_the_seats_after_the_director():
    """**順番が要。** 監督の patch を入れてから席を落とす（素通しの判定に要る）。"""
    import inspect

    from app.muse import service

    src = inspect.getsource(service.chat)
    i_patch = src.index("led = ledger_mod.apply_patch(led, patch)")
    i_fill = src.index("crew_room.seat_fill(")
    assert i_patch < i_fill, "席の着地は監督のあと"
    assert "taken=set(patch.keys()) | director_sticky" in src
    assert 'debug_mod.note(\n                session, "seat_fill"' in src, "黙って足さない"
