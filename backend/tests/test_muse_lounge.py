"""Lounge share/reaction parsing and friends_of ranking."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.characters import compat
from backend.app.muse import lounge


def test_parse_labelled_share_and_normalize():
    raw = """
TEXT_JA: 今日は屋上で撮ったよ！監督が振り返りの表情いいって
TEXT_EN: Shot on the rooftop — director liked my looking-back face
POSE: looking back
OUTFIT: sailor uniform
EXPRESSION: soft smile
PLACE: rooftop
VIBE: windy dusk
"""
    parsed = lounge.parse_labelled(raw)
    fields = lounge.normalize_share(parsed)
    assert "屋上" in fields["text_ja"]
    assert fields["tags"]["pose"] == "looking back"
    assert fields["tags"]["place"] == "rooftop"


def test_normalize_reactions_maps_friends():
    friends = [
        {"id": "a", "name_ja": "アヤ", "name": "Aya", "tier": "best_friend", "score": 0.9},
        {"id": "b", "name_ja": "ミオ", "name": "Mio", "tier": "close", "score": 0.5},
    ]
    raw = """
REACTOR_1_REACTION: 💕
REACTOR_1_JA: いいねそれ！私も振り返りやってみたい
REACTOR_1_EN: Love that — I want to try looking back too
REACTOR_1_STANCE: try
REACTOR_2_REACTION: ✨
REACTOR_2_JA: 私なら前髪を見せるほうが好きかも
REACTOR_2_EN: I'd rather show my bangs though
REACTOR_2_STANCE: twist
REACTOR_2_TWIST: 前髪チラ見せ
"""
    reacts = lounge.normalize_reactions(lounge.parse_labelled(raw), friends)
    assert len(reacts) == 2
    assert reacts[0]["character_id"] == "a"
    assert reacts[0]["stance"] == "try"
    assert reacts[1]["stance"] == "twist"
    assert reacts[1]["twist"] == "前髪チラ見せ"


def test_pitch_chance_boosts_for_outgoing_traits():
    shy = lounge.pitch_chance({"personality": {"traits": ["shy", "quiet"]}})
    bold = lounge.pitch_chance({"personality": {"traits": ["bold", "curious", "creative"]}})
    assert 0.1 <= shy <= 0.2
    assert bold > shy
    assert bold <= 0.45


def test_should_write_habit_needs_notes():
    rng = __import__("random").Random(0)
    assert lounge.should_write_habit(notes=[], rng=rng) is False
    # Fixed seed: eventually true within a few rolls when notes exist.
    hits = sum(
        1 for i in range(40)
        if lounge.should_write_habit(notes=["逆光が好き"], rng=__import__("random").Random(i))
    )
    assert hits >= 1


@pytest.mark.asyncio
async def test_lounge_summary_dedupes_new_open_pitch(monkeypatch):
    async def fake_list(_db, *, limit=100, kind=""):
        return [
            {"id": "p1", "kind": "pitch", "status": "open", "created_at": 200.0},
            {"id": "w1", "kind": "wrap_share", "created_at": 150.0},
            {"id": "old", "kind": "wrap_share", "created_at": 10.0},
            # Reaction bump after last peek — should count as new via updated_at.
            {"id": "w2", "kind": "wrap_share", "created_at": 50.0, "updated_at": 180.0},
        ]

    from backend.app.muse import lounge_db
    monkeypatch.setattr(lounge_db, "list_threads", fake_list)
    out = await lounge_db.summary(object(), since=100.0)
    assert out["new_threads"] == 3
    assert out["open_pitches"] == 1
    assert out["unread"] == 3  # p1, w1, w2 — p1 not double-counted


def test_normalize_pitch_and_habit():
    pitch = lounge.normalize_pitch(lounge.parse_labelled(
        "TEXT_JA: 次は窓辺でどうでしょう？\nTEXT_EN: How about by the window next?"
    ))
    assert "窓辺" in pitch["text_ja"]
    habit = lounge.normalize_habit(lounge.parse_labelled(
        "TITLE_JA: 逆光好き\nTITLE_EN: Backlight fan\n"
        "BODY_JA: 監督は逆光にこだわりがち。\nBODY_EN: They linger on backlight."
    ))
    assert habit["title"] == "逆光好き"
    assert "逆光" in habit["body_ja"]


@pytest.mark.asyncio
async def test_friends_of_ranks_best_friend_first(monkeypatch):
    class FakeDB:
        pass

    async def fake_matrix(_db):
        return {
            "characters": [
                {"id": "me", "name": "Me", "name_ja": "私", "board": {}},
                {"id": "best", "name": "Best", "name_ja": "親友", "board": {}},
                {"id": "close", "name": "Close", "name_ja": "仲良し", "board": {}},
                {"id": "acq", "name": "Acq", "name_ja": "顔見知り", "board": {}},
            ],
            "pairs": [
                {"a": "me", "b": "acq", "score": 0.2, "tier": "acquaintance", "co_appearances": 0},
                {"a": "me", "b": "close", "score": 0.5, "tier": "close", "co_appearances": 1},
                {"a": "me", "b": "best", "score": 0.8, "tier": "best_friend", "co_appearances": 3},
            ],
        }

    monkeypatch.setattr(compat, "compat_matrix", fake_matrix)
    friends = await compat.friends_of(FakeDB(), "me", min_tier="close", limit=5)
    assert [f["id"] for f in friends] == ["best", "close"]


@pytest.mark.asyncio
async def test_next_liked_pitch_skips_already_recommended(monkeypatch):
    async def fake_list(_db, *, limit=100, kind=""):
        assert kind == "pitch"
        return [
            {
                "id": "old",
                "kind": "pitch",
                "author_character_id": "mio",
                "liked": True,
                "recommended_at": 1.0,
                "text_ja": "古い",
            },
            {
                "id": "fresh",
                "kind": "pitch",
                "author_character_id": "mio",
                "liked": True,
                "recommended_at": 0,
                "text_ja": "窓辺",
            },
            {
                "id": "other",
                "kind": "pitch",
                "author_character_id": "aya",
                "liked": True,
                "text_ja": "別の子",
            },
        ]

    from backend.app.muse import lounge_db
    monkeypatch.setattr(lounge_db, "list_threads", fake_list)
    hit = await lounge_db.next_liked_pitch(object(), "mio")
    assert hit["id"] == "fresh"
    none = await lounge_db.next_liked_pitch(object(), "unknown")
    assert none is None


# ── お出かけ ────────────────────────────────────────────────────────────────
def test_normalize_outing_maps_speakers():
    """一度の呼び出しで全員ぶん。人数が増えても呼び出しは増えない。"""
    cast = [
        {"character_id": "a", "name_ja": "各務 みお", "name": "Mio"},
        {"character_id": "b", "name_ja": "ゆかり", "name": "Yukari"},
    ]
    raw = """
WHEN_JA: この前の日曜
TURN_1_WHO: 各務 みお
TURN_1_JA: 並んだのに、結局座れたの一番奥の席だった
TURN_1_EN: We queued and still got the worst table
TURN_2_WHO: ゆかり
TURN_2_JA: あそこ、次はもっと早く行こうね
TURN_2_EN: Next time we go earlier
"""
    msgs = lounge.normalize_outing(lounge.parse_labelled(raw), cast)
    assert [m["name_ja"] for m in msgs] == ["各務 みお", "ゆかり"]
    assert [m["character_id"] for m in msgs] == ["a", "b"]
    assert msgs[0]["turn"] == 0 and msgs[1]["turn"] == 1


def test_normalize_outing_falls_back_to_cast_order():
    """話者名が書かれなくても、並びで割り当てる。空にはしない。"""
    cast = [{"character_id": "a", "name_ja": "みお"}, {"character_id": "b", "name_ja": "あおい"}]
    msgs = lounge.normalize_outing(
        lounge.parse_labelled("TURN_1_JA: いこっか\nTURN_2_JA: いこいこ"), cast,
    )
    assert [m["character_id"] for m in msgs] == ["a", "b"]
    assert all(m["text_en"] for m in msgs), "英語が空なら日本語で埋める"


def test_outing_summary_line_is_a_pointer_not_a_summary():
    """彼女の手元に残るのは**指し先**。中身は楽屋のスレッドにある。

    総監督:「要約は諸刃の剣。結構消えてしまうので。」690字を45字に縮めると
    ほとんど捨てたうえで、全部あるかのように読める。いつ・誰と・何を、だけ。
    """
    line = lounge.outing_summary_line({
        "when_ja": "この前の日曜", "occasion": "パンケーキ",
        "cast": [{"name_ja": "みお"}, {"name_ja": "ゆかり"}],
    })
    assert "ゆかり" in line and "パンケーキ" in line and "この前の日曜" in line
    assert "みお" not in line, "本人の名前は要らない"
    assert len(line) <= 80


def test_the_occasions_are_never_about_work():
    """撮影・衣装・カメラの語が入っていたら、休みの日の話にならない。"""
    blob = " ".join(f"{a} {b}" for a, b in lounge._OUTINGS)
    for word in ("撮影", "カメラ", "レンズ", "衣装", "ポーズ", "スタジオ", "監督"):
        assert word not in blob, word


# ── 彼女の手元に残る分 ──────────────────────────────────────────────────────
from backend.app.muse import service as muse_service  # noqa: E402


def test_the_circle_block_stays_small():
    """常駐は上限つき。**ここは軽量化の対象にしない代わりに、最初から小さく。**

    2026-08-21 に常駐を 2,468字 → 1,373字 に削ったばかりで、この手の欄は
    放っておくとすぐ膨らむ。要約ではなく指し先にしてあるのはそのため。
    """
    session = {"circle": [
        "この前の日曜、ゆかりとパンケーキ",
        "先週、あおいと買い物",
        "先月、みんなと旅行",
    ]}
    block = muse_service._memory_block(session)
    assert "ゆかり" in block and "あおい" in block
    assert "旅行" not in block, f"{muse_service.CIRCLE_MAX_LINES}行まで"
    assert muse_service.CIRCLE_MAX_CHARS <= 150


def test_she_may_bring_her_friends_up_but_only_so_often():
    """禁じずに理由を渡し、上限だけ置く。数えるのは**実際に言った時**。

    毎ターン言えとするとくどくなる。訊かれた時だけとすると、休みの日が
    無かったのと同じになる。
    """
    session = {
        "circle": ["この前の日曜、ゆかりとパンケーキ"],
        "circle_names": ["ゆかり"], "circle_mentions": 0,
        "chat": [{"role": "muse", "text": "……この前、ゆかりちゃんと行ったんです。"}],
    }
    assert muse_service._circle_note(session), "最初は出る"

    muse_service._count_circle_mention(session)
    assert session["circle_mentions"] == 1

    # 触れていないターンは数えない ―― 使わなかった分は残る
    session["chat"].append({"role": "muse", "text": "はい、そこに座りますね。"})
    muse_service._count_circle_mention(session)
    assert session["circle_mentions"] == 1

    session["circle_mentions"] = muse_service.CIRCLE_MENTION_MAX
    assert not muse_service._circle_note(session), "上限で黙る"


def test_no_circle_no_note():
    """お出かけの記録が無ければ、プロンプトは一文字も増えない。"""
    assert muse_service._circle_note({"circle": []}) == ""
    assert muse_service._memory_block({"circle": []}) == ""


@pytest.mark.asyncio
async def test_a_day_off_only_comes_round_every_few_shoots(monkeypatch):
    """彼女たちの生活は撮影より遅く流れる。毎回は書かない。

    回数は preset の `shoot_count`（既にある・`push_shoot_recap` が進める）と、
    直近の一件が持つ `shoot_count` の差で見る。**preset に欄を足さない。**
    """
    from backend.app.muse import service as svc

    preset = {"shoot_count": 13}
    threads: list[dict] = []
    monkeypatch.setattr(svc.presets_db, "get_preset",
                        lambda db, cid: _async(preset))
    monkeypatch.setattr(svc.lounge_db, "list_threads",
                        lambda db, **kw: _async(list(threads)))

    # 一度も無ければ、まず一件
    assert await svc._outing_is_due(None, "mio") is True

    threads.append({"kind": "outing", "shoot_count": 13,
                    "cast": [{"character_id": "mio"}]})
    assert await svc._outing_is_due(None, "mio") is False, "直後は要らない"

    preset["shoot_count"] = 13 + svc.OUTING_EVERY_SHOOTS - 1
    assert await svc._outing_is_due(None, "mio") is False

    preset["shoot_count"] = 13 + svc.OUTING_EVERY_SHOOTS
    assert await svc._outing_is_due(None, "mio") is True

    # 他の子の記録では自分の番は進まない
    threads[0]["cast"] = [{"character_id": "someone-else"}]
    assert await svc._outing_is_due(None, "mio") is True


@pytest.mark.asyncio
async def test_no_shoots_yet_means_no_day_off(monkeypatch):
    """撮ったことのない子に、思い出だけ先にある状態を作らない。"""
    from backend.app.muse import service as svc
    monkeypatch.setattr(svc.presets_db, "get_preset",
                        lambda db, cid: _async({"shoot_count": 0}))
    monkeypatch.setattr(svc.lounge_db, "list_threads", lambda db, **kw: _async([]))
    assert await svc._outing_is_due(None, "newcomer") is False


def _async(value):
    async def _run():
        return value
    return _run()


# ── お出かけを二段構えにする ────────────────────────────────────────────────
def test_there_are_enough_days_to_choose_from():
    """候補は50件ほど。**12件だと誰が行っても同じ話になる。**"""
    assert len(lounge._OUTINGS) >= 50
    names = [n for n, _ in lounge._OUTINGS]
    assert len(set(names)) == len(names), "同じお題が二度ある"


def test_the_candidate_list_is_japanese():
    """候補に日本語以外を混ぜない。**自分で三度踏んだので、置いておく。**

    下書きの段階で `프리마켓`（ハングル）、`river の河川敷`、`три`（キリル）が
    紛れた。日記で直したのと同じ崩れを、こちらの手でやっていた。
    """
    from backend.app.muse.diary import stray_script
    for name, hint in lounge._OUTINGS:
        assert not stray_script(name), name
        assert not stray_script(hint), hint
    # 読み込み時にも見ている
    with pytest.raises(ValueError):
        lounge._assert_ja((("프리마켓", "ふつうの一日"),))


def test_the_last_place_is_not_offered_again():
    """前回の行き先は候補から外す。**続き物にはしない**（総監督の指定）。"""
    got = lounge.outing_choices(8, avoid="水族館")
    assert len(got) == 8
    assert all(n != "水族館" for n, _ in got)
    # 全部は見せない —— 52件並べると読み流される
    assert len(lounge.outing_choices(12)) == 12


def test_the_season_reaches_the_talk():
    """同じ「散歩」でも二月と八月では違う話になる。"""
    import time as _t
    def at(month):
        return lounge.season_ja(_t.mktime((2026, month, 15, 12, 0, 0, 0, 0, -1)))
    assert at(1) == "冬" and at(12) == "冬"      # 年をまたぐ
    assert at(4) == "春" and at(7) == "夏" and at(10) == "秋"


def test_the_errand_stays_rare():
    """総監督からの頼まれごとは**たまに**。

    お出かけは「総監督が居なかった時間」を作るための機能で、毎回が頼まれごとに
    なると意味が反転する。
    """
    import random as _r
    assert lounge.OUTING_ERRAND_CHANCE <= 0.3
    rng = _r.Random(11)
    hits = sum(1 for _ in range(400) if lounge.outing_is_an_errand(rng))
    assert 40 <= hits <= 200, hits


def test_faces_reach_every_speaker():
    """楽屋は話者が変わるので、**発言ごと**に顔が要る。"""
    rows = [{
        "author_character_id": "a",
        "messages": [{"character_id": "a"}, {"character_id": "b"}, "こわれた行"],
        "cast": [{"character_id": "b"}],
    }]
    lounge.stamp_faces(rows, {"a": "sha-a", "b": "sha-b"})
    assert rows[0]["face"] == "sha-a"
    assert [m.get("face") for m in rows[0]["messages"] if isinstance(m, dict)] == \
        ["sha-a", "sha-b"]
    assert rows[0]["cast"][0]["face"] == "sha-b"

    # 顔を引いていない子は空のまま（画面側で出し分ける）
    lounge.stamp_faces(rows, {})
    assert rows[0]["face"] == ""


def test_the_snapshot_is_not_a_studio_shot():
    """スナップは**撮影のカットではない**。寄りも決めポーズも作らない。"""
    got = lounge.snapshot_prompt(
        [{"subject_tag": "1girl"}] * 3,
        identity_tags=[["silver_hair"], ["black_hair"], ["brown_hair"]],
        occasion=lounge.outing_place_en("古本屋"),
    )
    assert got.startswith("3girls")            # 人数は cast から derive
    assert "old bookstore" in got
    assert "candid photo" in got and "snapshot" in got
    # スタジオの語彙は入れない
    for studio in ("cowboy_shot", "close-up", "professional lighting", "posing"):
        assert studio not in got
    # 同じタグを二度並べない
    parts = [p.strip() for p in got.split(",")]
    assert len(parts) == len(set(parts))


def test_every_day_out_has_somewhere_to_photograph():
    """52件すべてに、画に入れられる場所がある。

    日本語のお題（「古本屋」）はそのままではタグに向かないので、英語の場所を
    別に持つ。抜けていれば場所を入れないだけだが、**全部埋めておく**。
    """
    missing = [n for n, _ in lounge._OUTINGS if not lounge.outing_place_en(n)]
    assert not missing, missing
    assert lounge.outing_place_en("知らないお題") == ""


# ── 手帖に英語が漏れる ──────────────────────────────────────────────────────
def test_the_english_half_does_not_land_in_the_japanese_page():
    """出力例の値を真似た行が、日本語の本文に流れ込んでいた。

    本番の手帖 4頁中2頁（総監督が UI で発見）:

        body_ja: 監督は雨の後の静けさ……こだわりますね。
                 English body: The Director loves the stillness after the rain…

    指示の最終行が `BODY_EN: English body` で、**モデルが値ごと真似た**。
    `_LABEL_RE` は大文字の語しかラベルと見ないので `English body:` は境界に
    ならず、直前の `BODY_JA` に落ちた。そのうえ `BODY_EN` が空のままなので
    「英語が無ければ日本語で埋める」が働き、**両方の欄が同じ塊**になった。
    """
    raw = (
        "TITLE_JA: 雨上がりの、少し寂しい空気感\n"
        "TITLE_EN: The Quiet Air After Rain\n"
        "BODY_JA: 監督は雨の後の静けさにこだわりますね。\n"
        "English body: The Director loves the stillness after the rain."
    )
    got = lounge.normalize_habit(lounge.parse_labelled(raw))
    assert "English" not in got["body_ja"]
    assert got["body_ja"] == "監督は雨の後の静けさにこだわりますね。"
    # こぼれた英語は捨てずに、空だった英語の欄へ回す
    assert got["body_en"].startswith("The Director loves")


def test_a_healthy_page_is_left_alone():
    """壊れていない出力は触らない。"""
    raw = ("TITLE_JA: ページの中の静寂\nTITLE_EN: Silence in the Pages\n"
           "BODY_JA: 密やかな暗がりを好むようです。\n"
           "BODY_EN: The Director favors shadowed moments.")
    got = lounge.normalize_habit(lounge.parse_labelled(raw))
    assert got["body_ja"] == "密やかな暗がりを好むようです。"
    assert got["body_en"] == "The Director favors shadowed moments."


def test_the_word_english_in_prose_is_not_a_boundary():
    """本文に `English` と出てきても、ラベルの形でなければ切らない。"""
    got = lounge.normalize_habit({"BODY_JA": "English の教科書の話をしていた。"})
    assert got["body_ja"] == "English の教科書の話をしていた。"
    ja, spilled = lounge.split_trailing_english("英語版はありません。")
    assert ja == "英語版はありません。" and spilled == ""


def test_the_output_contract_does_not_show_an_english_value():
    """**例の値を英語で書かない。** 書くと、その値ごと真似られる。

    日記の身体感覚（指先 14/15）でも、`MY_FEEL` の語彙リスト（W撮り 0/10）でも
    同じことが起きた。**例は「こう書け」ではなく「これを書け」として効く。**
    """
    from backend.app.muse import crew as muse_crew
    got = muse_crew.showrunner_habit_prompt(
        notes="夕方の公園で撮ろう", muse_name="各務 みお",
    )
    assert "BODY_EN: English body" not in got
    assert "同じ本文を英語で" in got


def test_pages_already_saved_are_cleaned_when_read():
    """既に保存されている頁も、読むときに整える。

    書く側を直しても、**壊れたまま残っている頁は新しいものが来るまで
    表示され続ける**（総監督が見たのは 4頁中2頁）。保存し直しはしない ——
    読むたびに切るだけで足りる。
    """
    import inspect
    from backend.app.muse import api as muse_api
    src = inspect.getsource(muse_api.handpost_list)
    assert "split_trailing_english" in src
    assert "body_ja" in src and "body_en" in src
