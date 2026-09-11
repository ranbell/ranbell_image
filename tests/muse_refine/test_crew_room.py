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
def test_only_the_tag_half_reaches_the_ledger():
    """`CRAFT: <tags> | <prose>` の散文側は台帳に渡さない。

    実機で渡したら、欄にパイプと日本語が入り、しかも欄をまたいで混ざった
    （`light` に `translucent_fabric | 襟が夕陽を透かす` が着いた）。
    """
    assert C.craft_tags("backlight, rim_light | golden hour, warm") == "backlight, rim_light"
    assert C.craft_tags("sitting") == "sitting"
    assert C.craft_tags("") == ""


def test_the_craft_is_grouped_by_field_not_interleaved():
    """**欄ごとに一行。** 交互に並べると writer がどちらを採るか迷う。"""
    got = C.craft_block([
        {"name": "照明", "role": "gaffer", "field": "light", "craft": "rim_light | low sun"},
        {"name": "演出", "role": "beat", "field": "beat", "craft": "sitting | weight left"},
        {"name": "振付", "role": "spine", "field": "beat", "craft": "leaning, sitting | elbows"},
        {"name": "やじ", "role": "hook", "field": "", "craft": ""},
    ])
    assert got.index("beat:") < got.index("light:")        # 台帳の欄順
    assert "beat: sitting, leaning" in got                 # 同じ欄は一行に畳む
    assert "low sun" not in got                            # 散文側は渡さない
    assert "やじ" not in got                               # craft の無い発言は入らない


def test_a_field_never_repeats_a_tag():
    got = C.craft_block([
        {"name": "演出", "role": "beat", "field": "beat", "craft": "sitting, calm"},
        {"name": "振付", "role": "spine", "field": "beat", "craft": "SITTING, leaning"},
    ])
    assert got.count("sitting") + got.count("SITTING") == 1


def test_the_seat_format_overrides_the_classic_one():
    """職能文の直後に classic の OUTPUT（TAGS/SCENE）が来る。最後に上書きする。"""
    assert "REPLACES any format above" in C.SEAT_OUTPUT
    assert "CRAFT:" in C.SEAT_OUTPUT
    # TAGS は**禁止として**だけ出てくる（求めてはいない）
    assert "Never write a TAGS: or SCENE: block" in C.SEAT_OUTPUT
    import inspect
    src = inspect.getsource(C._seat_turn)
    assert "SEAT_OUTPUT" in src and "system_prompt_for" in src
    assert src.index("system_prompt_for") < src.index("SEAT_OUTPUT"), "上書きは後ろ"


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


# ── 手帖の欄名が漏れてくる（実機 2026-09-11）─────────────────────────────
def test_a_notebook_label_never_reaches_the_ledger():
    """席の職能文は `BEAT` `WEARING` を名指しで説明するので、模型が写す。

    実機で台帳にこう着いた:

        wearing: "BEAT: standing still, eyes towards the light"
        bg:      "ATMOSPHERE:"        ← 中身すら無い

    条文でも禁じたが、**届く手前でも落とす**。模型の行儀に台帳の綺麗さを
    預けない（[[feedback-a-box-or-it-wont-land]] の裏返し）。
    """
    assert C.craft_tags("BEAT: standing still, eyes towards the light | 重心") \
        == "standing still, eyes towards the light"
    assert C.craft_tags("WEARING: BEAT: sitting | x") == "sitting"   # 重なっても剥がす
    assert C.craft_tags("ATMOSPHERE:") == ""                          # 空ラベルは消える
    assert C.craft_tags("ATMOSPHERE: | dusty air") == ""


def test_an_ordinary_tag_that_looks_like_a_label_survives():
    """`atmospheric` は欄名ではない。コロンが無いものは剥がさない。"""
    assert C.craft_tags("atmospheric, dusty") == "atmospheric, dusty"
    assert C.craft_tags("backlight, rim_light") == "backlight, rim_light"


def test_an_empty_craft_makes_no_line():
    assert C.craft_block([
        {"name": "美術", "role": "propshop", "field": "bg", "craft": "ATMOSPHERE:"},
    ]) == ""


def test_the_seat_contract_forbids_labels_too():
    assert "No field label inside CRAFT" in C.SEAT_OUTPUT


def test_the_ledger_door_strips_labels_whoever_knocked():
    """**台帳の値が、欄の名前で始まってはいけない。**（2026-09-11）

    出どころは一つではなかった —— 班の席の CRAFT だけでなく、女優の CARD
    （`persona.card_to_patch`）も writer の JSON も、ラベルを頭に付けてくる。
    実機で `wearing: "BEAT: standing by the railing…"` が残り続けたのは、
    班の経路だけを塞いでいたから。**入口は `normalize_patch` 一つ。**
    """
    from app.muse_refine import persona

    got = L.normalize_patch({"wearing": "BEAT: standing by the railing", "bg": "ATMOSPHERE:"})
    assert got["wearing"] == "standing by the railing"
    assert got["bg"] == ""                                  # 空は scrub が捨てる

    # 女優の CARD 経由でも同じ
    card = L.normalize_patch(persona.card_to_patch("WEARING: BEAT: standing, silhouette"))
    assert card["wearing"] == "standing, silhouette"


def test_a_value_that_merely_looks_like_a_label_survives():
    got = L.normalize_patch({
        "atmosphere": "atmospheric, dusty",
        "scene": "cafe: the corner table",
    })
    assert got["atmosphere"] == "atmospheric, dusty"
    assert got["scene"] == "cafe: the corner table"


def test_there_is_only_one_label_stripper():
    """二つ持つと必ずずれる。班は台帳のものを使う。"""
    import inspect
    assert not hasattr(C, "_LABEL_HEAD_RE")
    assert "ledger_mod.strip_field_label" in inspect.getsource(C.craft_tags)


# ── 画面の配線 ──────────────────────────────────────────────────────────
def test_the_panel_is_told_whether_the_table_is_open():
    """ボタンの出し分けは公開ビューの二つで決まる。"""
    s = _session()
    v = service.public_view(s)
    assert v["crew_open"] is False and v["crew_seats"] == 0
    s[C.TABLE_OPEN] = True
    v = service.public_view(s)
    assert v["crew_open"] is True and v["crew_seats"] == 18


def test_the_crew_presets_come_from_the_catalogue_not_the_panel():
    """画面に直書きすると、席を足した日に黙ってずれる。"""
    panel = __import__("pathlib").Path(
        "frontend/src/components/MuseRefinePanel.vue"
    ).read_text(encoding="utf-8")
    assert "catalog.value?.crew?.presets" in panel
    for name in ("photoreal", "vivid", "calm"):
        assert f"'{name}'" not in panel, f"{name} が画面に直書きされている"


def test_the_seat_rows_have_their_own_look():
    """18人が喋るので、彼女の台詞と同じ見た目にしない。"""
    panel = __import__("pathlib").Path(
        "frontend/src/components/MuseRefinePanel.vue"
    ).read_text(encoding="utf-8")
    for fn in ("isSeatRow", "isHeckleRow"):
        assert f"function {fn}(row)" in panel
    assert "'seat'" in panel and "'heckle'" in panel


def test_the_mode_is_chosen_before_the_session_opens():
    """総監督「監督のみ / スタジオ撮りは Muse Classic のUI のような選択がいい」。

    班は途中から呼べない（開幕の三席が当たりを付けてから全班、という順番が
    classic の設計）ので、**開始の扉で分かれる**。
    """
    panel = __import__("pathlib").Path(
        "frontend/src/components/MuseRefinePanel.vue"
    ).read_text(encoding="utf-8")
    # 文言はテンプレートリテラル経由（`t(\`museRefine.${m.k}\`)`）なので鍵で見る
    assert "k: 'modeSolo'" in panel and "k: 'modeStudio'" in panel
    # 開始ボタンが扉を振り分ける
    assert "shootMode.value === 'studio' && !tableOpen.value ? 'table' : 'open'" in panel
    # 開いたあとは選び直せない
    assert ':disabled="chatLocked || opened"' in panel


def test_the_seat_rows_do_not_show_the_say_label():
    """総監督「スタジオ撮りだと SAY: が露出する」。"""
    say, craft = C.split_craft("SAY: 総監督、いいですね。\nCRAFT: rim_light | low sun")
    assert say == "総監督、いいですね。"
    assert not say.startswith("SAY")
    assert craft == "rim_light | low sun"


def test_the_stream_stops_before_the_craft_line():
    """流れている間も danbooru 語を出さない。"""
    from app.muse import shared

    out = []
    feed = shared._say_only(out.append)
    for ch in "SAY: 総監督、いいですね。\nCRAFT: rim_light | low sun\n":
        feed(ch)
    got = "".join(out)
    assert "いいですね" in got
    assert "rim_light" not in got and "CRAFT" not in got


def test_the_seats_stream_too():
    """総監督「streaming 表示しないので待たされる感覚がかなり大きい」。"""
    import inspect
    assert "on_token=_stream_to(session, muse_id)" in inspect.getsource(C._seat_turn)
    assert "on_token=_stream_to(session, muse_id)" in inspect.getsource(C._banter_turn)
