"""**欄ごとの会議 —— 同じ台帳を持つ席を束ね、結論を一つ出す。**（2026-09-14）

総監督「同じ台帳のメンバーを束ねて1つのセッションにして、結論として一つの台帳を
だしたらいいんじゃないかな。そうすると衝突は回避できると思う。あとその時に今の
台帳が何かを告知してから、今はこうなっててどう変えるのかという話をしたらいいのでは？」

実機（`6dc11d0e`・standard 12席）で起きていたこと:

    look    12語  amber_theme … magenta_theme   ← 色彩と線画が別々に足し、**琥珀と
                                                   マゼンタが同居**
    light    8語  backlighting, rim_light, hard_rim, edge_lighting
                  ← **席は一つしかない**のに、逆光の言い換えが四つ
    bg      12語  … silver_spoon … silver_sugar_spoon
    frame    7語  … air_between_limbs … air_between_elbows

原因は席の数ではなく、**毎ターン「足す」ことしかできず、欄の全体を言い直す機会が
無かった**こと。だから欄ごとに束ねて、いまの値を告知してから、欄ぜんぶの値を
一つ決めさせる。呼び出しも席数から欄数へ減る（standard は 12 → 9）。

ここで守るのは三つ —— **束ねること・告知すること・総監督の言葉を消さないこと**。
"""
from __future__ import annotations

from app.muse import crew
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


# ── 束ねること ──────────────────────────────────────────────────────────

def test_the_seats_that_share_a_field_sit_down_together():
    """standard の12席は **9つの会議**になる。取り合う欄が三つ束ねられる。"""
    seats = C.writing_seats(crew.resolve_crew(preset="standard"))
    groups = C.field_groups(seats)

    assert len(seats) == 12 and len(groups) == 9
    bundled = {f: [crew.role_of(m) for m in g] for f, g in groups if len(g) > 1}
    assert bundled == {
        "beat": ["beat", "spine"],
        "frame": ["cutout", "lens"],
        "look": ["palette", "ink"],
    }


def test_a_seat_with_no_field_keeps_its_own_room():
    """主演は欄を持たない。**欄なし同士を同じ部屋に入れない** —— 話の相手が居ない。"""
    groups = C.field_groups(["beat:ichibyou", "actress", "spine:bane"])
    assert groups == [
        ("beat", ["beat:ichibyou", "spine:bane"]),
        ("", ["actress"]),
    ]


def test_the_order_follows_the_seat_who_sits_first():
    """並びは席順のまま —— 会議は「その欄に最初に座る席」の位置に置く。"""
    groups = C.field_groups(["palette:itten", "propshop:zatsuka", "ink:ipponsen"])
    assert [f for f, _ in groups] == ["look", "bg"]
    assert groups[0][1] == ["palette:itten", "ink:ipponsen"]


# ── 告知すること ────────────────────────────────────────────────────────

def test_the_meeting_opens_with_what_the_ledger_says_now():
    """総監督「今の台帳が何かを告知してから、今はこうなっててどう変えるのか」。"""
    head = C.field_header("look", ledger={"look": "amber_theme, cel_shading"})
    assert "amber_theme, cel_shading" in head
    assert "READS after this turn" in head
    assert str(C.FIELD_CONCLUSION_MAX) in head


def test_an_empty_field_says_so_out_loud():
    head = C.field_header("atmosphere", ledger=L.blank())
    assert "(empty)" in head
    assert "Showrunner's own words" not in head


def test_the_showrunner_words_are_named_and_the_crew_words_are_not():
    """**誰の言葉かを告げる。** 班は自分が置いた語しか言い直せない。"""
    head = C.field_header(
        "look", ledger={"look": "amber_theme, cel_shading, clean_lineart"},
        mine=["cel_shading", "clean_lineart"],
    )
    line = [x for x in head.splitlines() if "Showrunner's own" in x][0]
    assert "amber_theme" in line
    assert "cel_shading" not in line and "clean_lineart" not in line


def test_the_single_seat_gets_the_same_announcement():
    """1席の欄も同じ —— `light` は一人なのに言い換えが四つ積もっていた。"""
    s = _session(refine_ledger={**L.blank(), "light": "backlighting, rim_light"})
    prompt = C.seat_prompt(
        s, "gaffer:gyakkou", director_line="夕方にして", floor=[],
    )
    assert "FIELD `light`" in prompt
    assert "now: backlighting, rim_light" in prompt
    assert "SHOWRUNNER:\n夕方にして" in prompt


def test_the_bundled_prompt_carries_the_field_and_the_floor():
    s = _session(refine_ledger={**L.blank(), "look": "amber_theme"})
    prompt = C.group_prompt(
        s, ["palette:itten", "ink:ipponsen"], field="look",
        director_line="もっと画を強く",
        floor=[{"name": "衣装", "say": "ベルベットで"}],
    )
    assert "FIELD `look`" in prompt and "now: amber_theme" in prompt
    assert "THE FLOOR SO FAR" in prompt and "衣装: ベルベットで" in prompt


def test_the_corner_is_told_the_slot_is_shared():
    """束ねた前置きでは「君だけが書く」が嘘になる。**一つの値で閉じる**と言う。"""
    sysmsg = crew.field_table_prompt(
        ["palette:itten", "ink:ipponsen"], field="look", preset_id="standard",
    )
    assert "SPEAKER 1" in sysmsg and "SPEAKER 2" in sysmsg
    assert "you are the only seat that writes it" not in sysmsg
    assert "SHARE with the other speakers" in sysmsg
    assert "ONE value for `look`" in sysmsg
    # 口調は人物カードで保つ（束ねても「一人の語り手が名札を付け替える」にしない）
    assert "口調 (JA):" in sysmsg and sysmsg.count("VOICE (EN):") == 2


def test_the_output_contract_asks_for_one_craft_at_the_end():
    assert "ONE CRAFT line for the whole corner" in C.GROUP_OUTPUT
    assert "REPLACES any format above" in C.GROUP_OUTPUT


# ── 返事を解くこと ──────────────────────────────────────────────────────

def test_the_packed_reply_splits_into_voices_and_one_conclusion():
    raw = (
        "SPEAKER: palette:itten\n"
        "SAY: 総監督、この画の色の芯は琥珀です。\n"
        "SPEAKER: ink:ipponsen\n"
        "SAY: 一点さんの琥珀に乗ります。線は締めますね。\n"
        "\n"
        "CRAFT: amber_theme, cel_shading, clean_lineart | 琥珀の芯に締めた線\n"
    )
    rows, craft = C.split_packed(raw, ["palette:itten", "ink:ipponsen"])
    assert [m for m, _ in rows] == ["palette:itten", "ink:ipponsen"]
    assert rows[0][1].startswith("総監督、この画の色の芯")
    assert "SAY:" not in rows[1][1] and "SPEAKER" not in rows[1][1]
    assert craft.startswith("amber_theme, cel_shading, clean_lineart")


def test_when_every_speaker_writes_a_craft_the_closing_one_wins():
    """条文では一行だが、席ごとに書いてきたら**閉めの一行が会議の結論**。"""
    raw = (
        "SPEAKER: 1\nSAY: 琥珀で。\nCRAFT: amber_theme | 色\n"
        "SPEAKER: 2\nSAY: 線を締めます。\nCRAFT: amber_theme, cel_shading | 結論\n"
    )
    rows, craft = C.split_packed(raw, ["palette:itten", "ink:ipponsen"])
    assert [m for m, _ in rows] == ["palette:itten", "ink:ipponsen"]
    assert craft.startswith("amber_theme, cel_shading")


def test_a_reply_that_ignores_the_format_is_not_dropped():
    """形式を守らなかった回も落とさない —— 丸ごと先頭の席の発言にする。"""
    rows, craft = C.split_packed(
        "琥珀でいきましょう。\nCRAFT: amber_theme | 色",
        ["palette:itten", "ink:ipponsen"],
    )
    assert rows == [("palette:itten", "琥珀でいきましょう。")]
    assert craft.startswith("amber_theme")


def test_the_stream_follows_whoever_is_speaking():
    """**束ねた回だけ画面が無言、にしない。** `SPEAKER:` で吹き出しを切り替える。"""
    seen: dict[str, list[str]] = {}

    def _fake_stream_to(session, muse_id):
        return lambda text: seen.setdefault(muse_id, []).append(text)

    original, C._stream_to = C._stream_to, _fake_stream_to
    try:
        feed = C._packed_stream(_session(session_id="s"),
                                ["palette:itten", "ink:ipponsen"])
        for ch in ("SPEAKER: palette:itten\nSAY: 琥珀です。\n"
                   "SPEAKER: ink:ipponsen\nSAY: 線を締めます。\n"):
            feed(ch)
    finally:
        C._stream_to = original

    got = {k: "".join(v) for k, v in seen.items()}
    assert "琥珀です。" in got["palette:itten"]
    assert "線を締めます。" in got["ink:ipponsen"]
    assert "琥珀" not in got.get("ink:ipponsen", "")
    for text in got.values():
        assert "SPEAKER" not in text


# ── 着地 ────────────────────────────────────────────────────────────────

def test_the_conclusion_becomes_the_whole_field():
    """**結論は欄の全体。** 足し算ではないので、山にならない。"""
    led = {**L.blank(), "look": "amber_theme, sunlight, cel_shading"}
    s = _session(**{C.CREW_WORDS: {"look": ["amber_theme", "sunlight", "cel_shading"]}})
    patch, landed = C.field_land(
        s, _floor(("look", "cel_shading, clean_lineart | 線で締める")),
        ledger=led, taken=set(),
    )
    assert patch["look"] == "cel_shading, clean_lineart"
    assert landed["look"] == ["cel_shading", "clean_lineart"]


def test_the_showrunner_words_survive_the_rewrite():
    """**消せるのは班が置いた語だけ。** 監督の言葉は結論の前に必ず残る。"""
    led = {**L.blank(), "look": "amber_theme, magenta_theme, magenta_glint"}
    s = _session(**{C.CREW_WORDS: {"look": ["magenta_theme", "magenta_glint"]}})
    patch, _ = C.field_land(
        s, _floor(("look", "cel_shading | 線画寄りに")),
        ledger=led, taken=set(),
    )
    assert patch["look"] == "amber_theme, cel_shading"


def test_an_old_session_without_the_note_loses_nothing():
    """印の無いセッションは**全語を総監督のもの**として扱う（消えない側に倒す）。"""
    led = {**L.blank(), "bg": "green grass, plastic_bottle"}
    patch, landed = C.field_land(
        _session(), _floor(("bg", "sandy_sandal | 芝生の忘れ物")),
        ledger=led, taken=set(),
    )
    assert patch["bg"] == "green grass, plastic_bottle, sandy_sandal"
    assert landed["bg"] == ["sandy_sandal"]


def test_the_empty_sticky_fields_finally_get_filled():
    """`look` 88% / `atmosphere` 76% が空だった —— 空なら会議の結論で埋める。"""
    patch, _ = C.field_land(
        _session(),
        _floor(("look", "amber_theme, cel_shading | 色の芯"),
               ("atmosphere", "heat_haze, shimmering_air | 陽炎")),
        ledger=L.blank(), taken=set(),
    )
    assert patch["look"] == "amber_theme, cel_shading"
    assert patch["atmosphere"] == "heat_haze, shimmering_air"


def test_the_field_the_director_named_is_left_alone():
    """**監督の言葉が勝つ。** その回に書かれた欄に、班は口を出さない。"""
    led = {**L.blank(), "light": "backlighting"}
    patch, landed = C.field_land(
        _session(), _floor(("light", "hard_shadow | 硬く")),
        ledger=led, taken={"light"},
    )
    assert patch == {} and landed == {}


def test_the_body_fields_are_never_touched():
    """姿勢・表情・服は入れない —— 一つの体の掃除を壊さないため。
    束ねた結論は `craft_block` 経由で台本係に渡る。"""
    patch, _ = C.field_land(
        _session(),
        _floor(("beat", "standing, hand_on_hip | 姿勢"),
               ("expression", "soft_smile | 顔"),
               ("wearing", "apron | 服")),
        ledger=L.blank(), taken=set(),
    )
    assert patch == {}


def test_what_the_showrunner_refused_does_not_come_back():
    """総監督が拒否した語は、会議の結論からも落ちる。"""
    s = _session(banned=["towel"])
    patch, _ = C.field_land(
        s, _floor(("bg", "blue_towel, small_stone | 忘れ物")),
        ledger=L.blank(), taken=set(),
    )
    assert "towel" not in patch.get("bg", "")
    assert "small_stone" in patch["bg"]


def test_the_same_thing_is_not_said_twice():
    """**実機ですり抜けた重複。** `silver_spoon` と `silver_sugar_spoon` が同居した。

    語の境目（`talk.word_hit`）では当たらないので、語の重なりでも見る。
    """
    led = {**L.blank(), "bg": "coffee cup, silver_spoon"}
    patch, landed = C.field_land(
        _session(), _floor(("bg", "silver_sugar_spoon, cheesecake | 卓上")),
        ledger=led, taken=set(),
    )
    assert landed["bg"] == ["cheesecake"]
    assert patch["bg"] == "coffee cup, silver_spoon, cheesecake"


def test_the_conclusion_is_capped_at_six_words():
    tags = ", ".join(f"tag_{i}" for i in range(10))
    patch, landed = C.field_land(
        _session(), _floor(("bg", f"{tags} | 十語出した")),
        ledger=L.blank(), taken=set(),
    )
    assert len(landed["bg"]) == C.FIELD_CONCLUSION_MAX == 6
    assert patch["bg"] == ", ".join(f"tag_{i}" for i in range(6))


def test_a_field_full_of_the_showrunners_words_stops_growing():
    """**上限は「増やさない」約束で、「削る」約束ではない。**"""
    full = ", ".join(f"thing_{i}" for i in range(C.FIELD_CAP))
    patch, landed = C.field_land(
        _session(), _floor(("bg", "late_arrival | もう入らない")),
        ledger={**L.blank(), "bg": full}, taken=set(),
    )
    assert patch == {} and landed == {}


def test_a_silent_corner_does_not_clear_the_field():
    """CRAFT を書かなかった欄（据え置き）は動かさない。空にもしない。"""
    led = {**L.blank(), "look": "amber_theme"}
    patch, _ = C.field_land(
        _session(), _floor(("look", "")), ledger=led, taken=set(),
    )
    assert patch == {}


def test_the_field_label_never_rides_in():
    """`CRAFT: BG: …` と書かれても、欄名は入口で剥がれる（`craft_tags`）。"""
    patch, _ = C.field_land(
        _session(), _floor(("bg", "BG: paper_menu, worn_edges | 小道具")),
        ledger=L.blank(), taken=set(),
    )
    assert "BG:" not in patch["bg"]
    assert "paper_menu" in patch["bg"]


def test_no_crew_no_landing():
    """席が喋っていない回は何も起きない（一人撮り・W撮りはここを通らない）。"""
    assert C.field_land(_session(), [], ledger=L.blank(), taken=set()) == ({}, {})


# ── 一周の組み立て ──────────────────────────────────────────────────────

def test_the_turn_asks_the_crew_after_the_director():
    """**順番が要。** 監督の patch を入れてから着地させる（素通しの判定に要る）。"""
    import inspect

    from app.muse import service

    src = inspect.getsource(service.chat)
    i_patch = src.index("led = ledger_mod.apply_patch(led, patch)")
    i_fill = src.index("crew_room.field_land(")
    assert i_patch < i_fill, "班の着地は監督のあと"
    assert "taken=set(patch.keys()) | director_sticky" in src
    assert 'debug_mod.note(\n                session, "field_land"' in src, "黙って足さない"


def test_the_field_the_writer_rewrote_belongs_to_the_showrunner_again():
    """**台本係が書いた欄の控えは捨てる。**（2026-09-14）

    残したままだと、次の会議が「これは自分の語」として監督の言葉を消せてしまう。
    """
    import inspect

    from app.muse import service

    src = inspect.getsource(service.chat)
    assert "words = dict(crew_room.crew_words_of(session))" in src
    assert "for key in patch:\n        words.pop(key, None)" in src


def test_one_call_per_field_instead_of_one_per_seat():
    """**12席が8回になる。** 取り合う欄が三つ束ねられ、主演の席が抜けるぶん。

    実測で席1本は約9〜10秒（`stage_ms`・`6dc11d0e`）、主演の席は24〜28秒。
    """
    import asyncio

    from app.muse import service

    session = service.new_session({"locale": "ja", "model": "m"})
    session["character"] = {"character_id": "c1", "name_ja": "各務 みお"}
    session["inputs"] = {**session["inputs"], "crew_preset": "standard",
                         "banter_mode": "off"}
    session[C.TABLE_OPEN] = True

    calls: list[tuple[str, tuple[str, ...]]] = []

    async def _seat(ollama, sess, muse_id, *, model, prompt):
        calls.append(("seat", (muse_id,)))
        return "SAY: はい。\nCRAFT: one_thing | ひとつ"

    async def _group(ollama, sess, seats, *, field, model, prompt):
        calls.append(("corner", tuple(seats)))
        return "".join(
            f"SPEAKER: {mid}\nSAY: {i} 番目です。\n" for i, mid in enumerate(seats)
        ) + "CRAFT: agreed_one, agreed_two | 会議の結論"

    keep = (C._seat_turn, C._group_turn)
    C._seat_turn, C._group_turn = _seat, _group
    try:
        floor = asyncio.run(C.run_table(None, None, session, director_line="夕方に"))
    finally:
        C._seat_turn, C._group_turn = keep

    assert len(calls) == 8, calls
    assert sum(1 for kind, _ in calls if kind == "corner") == 3
    # **主演は一周に居ない（2026-09-16）** —— 彼女の言葉はターンの最後に届く。
    spoke = {m for _, ids in calls for m in ids}
    assert not any(crew.role_of(m) == "actress" for m in spoke), spoke

    # 会議の結論は**閉めの一人**に付く（欄に二つ着地しない）
    look = [r for r in floor if r["field"] == "look"]
    assert [r["craft"] for r in look] == ["", "agreed_one, agreed_two | 会議の結論"]
    assert [crew.role_of(r["muse_id"]) for r in look] == ["palette", "ink"]

    patch, _ = C.field_land(session, floor, ledger=L.blank(), taken=set())
    assert patch["look"] == "agreed_one, agreed_two"


def test_the_same_light_under_another_ending_is_not_added_again():
    """**実機（`e805ffac`・2026-09-15）で残った最後の重複。**

    `light` が `rim_lighting, backlighting, eye_glint, rim_light` になった ——
    台本係の `rim_lighting` と会議の `rim_light` は、語の境目でも語の重なりでも
    当たらない。語尾だけ均すと当たる。
    """
    led = {**L.blank(), "light": "rim_lighting, backlighting"}
    patch, landed = C.field_land(
        _session(), _floor(("light", "rim_light, eye_glint | 逆光を締める")),
        ledger=led, taken=set(),
    )
    assert landed["light"] == ["eye_glint"]
    assert patch["light"] == "rim_lighting, backlighting, eye_glint"


def test_two_different_accents_still_both_get_through():
    """**落としすぎない。** 語が一つ重なるだけの別物は通す（決めるのは会議）。"""
    assert C._too_close("amber_accent", "scarlet_accent") is False
    assert C._too_close("light_particles", "rim_light") is False
    assert C._too_close("cel_shading", "clean_lineart") is False
    assert C._too_close("depth_of_field", "shallow_depth_of_field") is True


# ── 綻び五件（2026-09-16）──────────────────────────────────────────────

def test_the_lead_is_dressed_at_the_studio_door_too():
    """**班の扉でも服を着せる。**（2026-09-16）

    総監督「初回の会話スタート時にデフォルト衣装の読み込みができていない場合あり」。

    画面の「開始」はスタジオ撮りのとき `/open` ではなく `/table` を叩く。
    着せるのは `open_session` の側だけだったので、**班で始めたセッションは
    服が空のまま**だった（実機 `f8961eaa`：1ターン目の台帳は `wearing` も空）。
    """
    import inspect

    from app.muse import service

    src = inspect.getsource(service.open_table)
    assert "talk.dress_from_signature(session)" in src, "班の扉で服を着せていない"
    i_dress = src.index("dress_from_signature")
    i_table = src.index("crew_room.run_table(")
    assert i_dress < i_table, "開幕の三席より前に着せる（衣装の席がその値を見る）"


def test_the_seat_wears_its_nickname_on_the_name_tag():
    """**画面の名札と、席同士の呼びかけを同じ言葉にする。**（2026-09-16）

    席は「一点さん」「すきま」と呼び合うのに、吹き出しは役職（色彩設計）だった。
    同じ役職に二人いる（`palette:itten` と `palette:aku`）ので見分けも付かない。
    """
    s = _session(character={"name_ja": "各務 みお"})
    assert C.seat_name(s, "palette:itten") == "一点（色彩設計）"
    assert C.seat_name(s, "beat:ichibyou") == "一秒（演出）"
    # 主演はキャストした本人の名前のまま
    cast = [m for m in crew.resolve_crew(preset="standard")
            if crew.role_of(m) == "actress"][0]
    assert C.seat_name(s, cast) == "各務 みお"


def test_the_lead_keeps_her_seat_at_the_opening():
    """一周からは外すが、**開幕の当たり付けには残る**（衣装 → 撮影 → 主演）。"""
    cast = crew.resolve_crew(preset="standard")
    opening = [crew.role_of(m) for m in C.opening_seats(cast)]
    assert opening == ["wardrobe", "lens", "actress"]
    walk = [crew.role_of(m) for m in C.writing_seats(cast, without=("actress",))]
    assert "actress" not in walk


def test_a_decorated_speaker_line_still_switches_the_bubble():
    """**飾られた名札でも宛先が変わる。**（2026-09-16）

    `**SPEAKER: …**` と書かれると行頭が `*` なので欄名に育たず、ラベルごと
    前の席の吹き出しへ流れていた（総監督「SAY などの Tag が漏れる」
    「Muse同士の会話が混ざる」）。
    """
    seen: dict[str, list[str]] = {}

    def _fake_stream_to(session, muse_id):
        return lambda text: seen.setdefault(muse_id, []).append(text)

    original, C._stream_to = C._stream_to, _fake_stream_to
    try:
        feed = C._packed_stream(_session(session_id="s"),
                                ["palette:itten", "ink:ipponsen"])
        for ch in ("**SPEAKER: palette:itten**\nSAY: 琥珀です。\n"
                   "  - SPEAKER: ink:ipponsen\nSAY: 線を締めます。\n"):
            feed(ch)
    finally:
        C._stream_to = original

    got = {k: "".join(v) for k, v in seen.items()}
    assert "琥珀です。" in got["palette:itten"]
    assert "線を締めます。" in got["ink:ipponsen"]
    assert "琥珀" not in got.get("ink:ipponsen", "")
    for text in got.values():
        assert "SPEAKER" not in text


def test_an_unknown_name_walks_the_seats_instead_of_piling_on_the_first():
    """**当たらない名札でも席順に進む。**（2026-09-16）

    `used` を空で渡していたので、名前が当たらないと毎回 `seats[0]` に落ち、
    **二人目の言葉が一人目の吹き出しに積まれていた**。
    """
    seen: dict[str, list[str]] = {}
    session = _session(session_id="s")

    def _fake_stream_to(_s, muse_id):
        return lambda text: seen.setdefault(muse_id, []).append(text)

    original, C._stream_to = C._stream_to, _fake_stream_to
    try:
        feed = C._packed_stream(session, ["palette:itten", "ink:ipponsen"])
        for ch in ("SPEAKER: ???\nSAY: 一人目。\nSPEAKER: ???\nSAY: 二人目。\n"):
            feed(ch)
    finally:
        C._stream_to = original

    got = {k: "".join(v) for k, v in seen.items()}
    assert "一人目。" in got["palette:itten"]
    assert "二人目。" in got["ink:ipponsen"], "二人目が一人目に積まれている"
    # 黙って間違えない
    notes = [n for n in (session.get("refine_log") or [])
             if n.get("kind") == "corner_speaker_miss"]
    assert len(notes) == 2, notes


def test_the_speaker_label_also_shuts_the_say_gate():
    """取りこぼしたときの止め —— `SPEAKER:` でも吹き出しは閉じる。"""
    from app.muse import shared

    assert shared._SAY_SHUT_RE.match("SPEAKER: palette:itten")
    assert shared._SAY_SHUT_RE.match("**SPEAKER: palette:itten")


def test_the_nickname_is_what_the_seats_call_each_other():
    """あだ名で呼ばれた名札も宛先に当たる（模型は日本語で書いてくる）。"""
    seats = ["palette:itten", "ink:ipponsen"]
    assert C._match_speaker("一点", seats, []) == ("palette:itten", True)
    assert C._match_speaker("色彩設計", seats, []) == ("palette:itten", True)
    assert C._match_speaker("2", seats, []) == ("ink:ipponsen", True)
    assert C._match_speaker("だれか", seats, ["palette:itten"]) == ("ink:ipponsen", False)


def test_a_hyphenated_id_still_finds_its_seat():
    """**区切りの揺れで席を取り違えない。**（2026-09-18）

    実機（`c62274f6`）で模型が `cut-out:sukima` と書き、id に当たらず
    「まだ喋っていない席の先頭」へ落ちた。たまたま正解だったが、**席順の運**に
    預けている形だった。
    """
    seats = ["cutout:sukima", "lens:pinto"]
    assert C._match_speaker("cut-out:sukima", seats, []) == ("cutout:sukima", True)
    assert C._match_speaker("cut out", seats, []) == ("cutout:sukima", True)
    assert C._match_speaker("LENS", seats, []) == ("lens:pinto", True)
