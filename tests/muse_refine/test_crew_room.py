"""**スタジオ撮り（班）を Refine に載せる。**（2026-09-11）

総監督「スタジオ撮り（複数の撮影スタッフのモード）を Muse refine に取り込みたい」
「classic からそのまま移植したあと、磨きましょう」「18役職を全部残す」。

classic では席は talk-only で、書くのは Scripter 一人だった。Refine ではその席に
`writer.write_patch` が座る —— だから手帖は要らない。

**そして一人撮りを壊さないこと。** `inputs.crew_preset` は `ALL_DEFAULTS` から
`"standard"` が入るので、**席の有無を門にすると一人撮りでも16席が回る**。
"""
from __future__ import annotations

from app.muse import crew
from app.muse_refine import crew_room as C
from app.muse_refine import ledger as L, service


def _session(**kw):
    s = service.new_session({"locale": "ja", "model": "m"})
    s["character"] = {"character_id": "c1", "name_ja": "各務 みお", "name": "Mio"}
    s.update(kw)
    return s


# ── 門 ──────────────────────────────────────────────────────────────────
def test_a_plain_session_has_no_crew():
    """**既定値を門にしない。** `crew_preset` は既定で `standard` が入っている。"""
    s = _session()
    assert s["inputs"].get("crew_preset") == "standard"   # 既定は入っている
    assert C.cast_of(s)                                    # 席は組める
    assert C.has_crew(s) is False                          # それでも班は開いていない


def test_the_table_opens_only_when_it_is_opened():
    s = _session()
    s[C.TABLE_OPEN] = True
    assert C.has_crew(s) is True


def test_an_open_flag_without_a_cast_is_still_no_crew():
    s = _session()
    s[C.TABLE_OPEN] = True
    s["inputs"] = {**s["inputs"], "crew_preset": "", "crew_ids": []}
    assert C.has_crew(s) is False


def test_the_chat_turn_asks_the_gate_not_the_mode():
    import inspect
    src = inspect.getsource(service.chat)
    assert "crew_room.has_crew(session)" in src
    assert "is_duet" not in src


# ── 席 ──────────────────────────────────────────────────────────────────
def test_all_eighteen_roles_are_kept():
    """総監督「18役職を全部残す」。"""
    s = _session()
    cast = C.cast_of(s)
    assert len({crew.role_of(m) for m in cast}) == len(crew.ROLE_ORDER) == 18


def test_five_seats_have_no_pen():
    """classic が取り上げたペンは取り上げたまま（`NOTE_MUTED`）。"""
    s = _session()
    cast = C.cast_of(s)
    pens = {crew.role_of(m) for m in C.writing_seats(cast)}
    for muted in ("continuity", "gate", "finisher", "grade", "hook"):
        assert muted not in pens, muted
    assert "plan" not in pens          # 構成は別経路
    assert len(pens) == 12


def test_the_opening_dresses_her_before_framing_her():
    """衣装 → 撮影 → 主演。席順ではなく着付けの順（classic の実測）。"""
    s = _session()
    got = [crew.role_of(m) for m in C.opening_seats(C.cast_of(s))]
    assert got == ["wardrobe", "lens", "actress"]


def test_every_seat_with_a_slot_owns_a_ledger_field():
    for role, slot in crew.CRAFT_SLOTS.items():
        field = C.SLOT_FIELD.get(slot)
        assert field, f"{role} の slot {slot} に欄が無い"
        assert field in L.LEDGER_KEYS, f"{field} は台帳の欄ではない"


def test_two_seats_may_share_a_field():
    """演出と振付はどちらも体、レイアウトと撮影はどちらも構図。classic も同じ。"""
    assert C.SLOT_FIELD["BODY"] == "beat"
    assert C.SLOT_FIELD["SHAPE"] == C.SLOT_FIELD["OPTICS"] == "frame"


# ── 席の返事 ────────────────────────────────────────────────────────────
def test_the_craft_line_is_split_off():
    say, craft = C.split_craft(
        "うん、逆光でいきましょう。\nCRAFT: backlighting, rim_light | low sun"
    )
    assert "逆光" in say
    assert "CRAFT" not in say
    assert craft == "backlighting, rim_light | low sun"


def test_a_seat_that_omits_craft_just_talks():
    say, craft = C.split_craft("今日はこのままでいいと思います。")
    assert craft == ""
    assert say == "今日はこのままでいいと思います。"


def test_omit_words_count_as_no_craft():
    for word in ("none", "-", "omit", "(omit)"):
        _, craft = C.split_craft(f"はい。\nCRAFT: {word}")
        assert craft == "", word


# ── writer への材料 ─────────────────────────────────────────────────────
def test_the_craft_is_grouped_by_field_not_interleaved():
    """**欄ごとにまとめる。** 交互に並べると writer がどちらを採るか迷う。"""
    got = C.craft_block([
        {"name": "照明", "role": "gaffer", "field": "light", "craft": "rim_light | low sun"},
        {"name": "演出", "role": "beat", "field": "beat", "craft": "sitting | weight left"},
        {"name": "振付", "role": "spine", "field": "beat", "craft": "leaning | elbows"},
        {"name": "やじ", "role": "hook", "field": "", "craft": ""},
    ])
    assert got.index("beat:") < got.index("light:")        # 台帳の欄順
    body = got[got.index("beat:"):got.index("light:")]
    assert "演出" in body and "振付" in body               # 同じ欄は隣り合う
    assert "やじ" not in got                               # craft の無い発言は入らない


def test_no_crew_means_no_block():
    assert C.craft_block([]) == ""
    assert C.craft_block([{"name": "やじ", "role": "hook", "field": "", "craft": ""}]) == ""


def test_the_writer_only_hears_the_crew_when_there_is_one():
    """**一人撮りの条文を一字も動かさない。** 規則ごと班の回にだけ届ける。"""
    import inspect
    from app.muse_refine import writer
    assert "THE CREW SPOKE" not in writer.WRITER_SYSTEM
    src = inspect.getsource(writer.write_patch)
    assert "crew_craft" in src
    assert 'if str(crew_craft or "").strip() else ""' in src
