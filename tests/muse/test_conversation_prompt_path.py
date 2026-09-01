"""Conversation → final prompt path: boxes authority, atmosphere, VERIFY guard, pipeline."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import identity, notebook, pipeline_view, schema, service


def _solo_cast():
    return [{
        "name": "Mio",
        "identity_tags": ["silver_hair", "bob_cut", "blue_eyes"],
        "subject_tag": "1girl",
    }]


def _session_with_shot(**nb_patch):
    s = schema.new_session({
        "theme": "t", "character_id": "c", "workflow": "w.json", "model": "m",
    })
    s["mode"] = "duet"
    s["character"] = {
        "name": "Mio",
        "identity_tags": ["silver_hair", "bob_cut", "blue_eyes"],
        "subject_tag": "1girl",
    }
    s["notebook"] = notebook.blank()
    notebook.apply_patch(notebook.of(s), nb_patch or {
        "atmosphere": "quiet, still",
        "scene": "school library, afternoon",
        "bg": "tall bookshelves, dusty sunlight",
        "light": "backlit from the window",
        "wearing": "light blue dress",
        "beat": "standing, holding a book",
        "expression": "soft smile",
    })
    s["craft"] = {}
    s["inputs"]["framing"] = "auto"
    return s


def test_atmosphere_reaches_frame_wide():
    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "scene": "classroom",
        "bg": "chalkboard",
        "light": "soft window light",
        "atmosphere": "quiet, expectant",
    })
    wide = notebook.frame_wide_phrases(nb)
    assert any("quiet" in p for p in wide)
    assert any("chalkboard" in p for p in wide)
    assert any("window" in p.lower() or "soft" in p for p in wide)


def test_fight_craft_scene_drops_conflicting_prose():
    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "scene": "school library",
        "wearing": "light blue dress",
        "beat": "standing",
    })
    # Mostly invents a different place/outfit — should drop.
    bad = (
        "She lounges on a neon rooftop in a red leather jacket under strobe lights, "
        "crowds cheering, fireworks exploding over the harbor skyline."
    )
    assert notebook.fight_craft_scene(nb, bad) == ""
    # Aligned prose survives.
    good = "Standing in the school library in a light blue dress."
    assert "library" in notebook.fight_craft_scene(nb, good).lower()


def test_solo_assemble_uses_person_boxes():
    """箱が無いと書けない — solo も boxes 経路で最終を組む。"""
    out = identity.assemble_from_boxes(
        cast=_solo_cast(),
        people=notebook.mint_person_box(_session_with_shot()["notebook"]),
        frame_wide=notebook.frame_wide_phrases(_session_with_shot()["notebook"]),
        style="", framing="auto", scene="",
    )
    assert out
    assert "standing" in out.lower()
    assert "light blue dress" in out.lower() or "dress" in out.lower()
    assert "quiet" in out.lower() or "still" in out.lower()


def test_apply_compiled_craft_prefers_boxes_and_keeps_notebook_phrases():
    session = _session_with_shot()
    # Weave bag uses a garment alias; boxes must still carry notebook wearing.
    ok = service._apply_compiled_craft(
        session,
        "1girl, blue_dress, standing, library",
        "Crowds cheer on a neon rooftop in red leather under strobes and fireworks.",
    )
    assert ok
    prompt = str((session.get("craft") or {}).get("prompt") or "")
    assert prompt
    assert "people" in (session.get("craft") or {})
    # Notebook wearing phrase survives via boxes.
    assert "dress" in prompt.lower()
    # Conflicting craft_scene should not dominate (fight drops or boxes win).
    assert "neon rooftop" not in prompt.lower()
    assert "red leather" not in prompt.lower()


def test_reassemble_does_not_flatten_to_positive_only():
    session = _session_with_shot()
    assert service._apply_compiled_craft(
        session, "1girl, standing, dress", "In the library.",
    )
    before = str(session["craft"]["prompt"])
    service._reassemble(session)
    after = str(session["craft"]["prompt"])
    assert after
    assert "people" in session["craft"]
    # Still box-shaped (named dynamic line), not a pure flat bag collapse.
    assert "Mio:" in after or "standing" in after.lower()
    assert "dress" in after.lower()
    assert before  # sanity


def test_pipeline_view_on_public_view():
    session = _session_with_shot()
    session["scripter_intent"] = "shot"
    session["asked_fields"] = ["beat", "atmosphere"]
    session["craft_route"] = [
        {"hop": "1 weave（生）", "added": ["standing"], "dropped": []},
        {"hop": "2 scrub_craft_tags", "added": [], "dropped": ["socks"]},
        {"hop": "9 人ごとの箱", "sides": ("standing", "")},
    ]
    session["turn_trace"] = [{
        "at": 1, "line": "立って、静かな空気で",
        "asked": ["beat", "atmosphere"], "moved": {"beat": "∅ → standing"},
        "missed": [],
    }]
    service._apply_compiled_craft(
        session, "1girl, standing", "quiet library",
    )
    view = schema.public_view(session)
    pipe = view.get("pipeline") or {}
    assert pipe.get("schema") == pipeline_view.PIPELINE_SCHEMA
    ids = [s["id"] for s in pipe.get("stages") or []]
    assert ids == [
        "classify", "clerks", "notebook", "weave", "scrub",
        "boxes", "prompt", "board",
    ]
    assert "craft_route" in view
    assert "turn_trace" in view
    # Atmosphere in notebook should be detectable if missing from prompt.
    assert isinstance(pipe.get("divergences"), list)


def test_classify_fields_include_atmosphere():
    from app.muse import chain
    assert "atmosphere" in chain.CLASSIFY_FIELDS
    assert "bg" in chain.FIELD_CLERK_KINDS
    assert "light" in chain.FIELD_CLERK_KINDS
    assert "expression" in chain.FIELD_CLERK_KINDS
    assert "atmosphere" in chain._PER_PERSON


def test_verify_guard_filters_settled_fields_in_source():
    """確定欄を VERIFY が置換しないガードがソースにあること。"""
    import inspect
    src = inspect.getsource(service._run_duet_scripter)
    assert "settled_shot" in src
    assert "k not in settled_shot" in src or "not in settled_shot" in src


def test_joke_skip_still_gates_notebook_writers():
    """PR #30: skip_picture が画経路を塞いだままであること。"""
    import inspect
    src = inspect.getsource(service)
    assert "if not skip_picture" in src


def test_the_second_compile_is_gated_on_asked_not_on_filled():
    """**境目は「埋まっているか」ではなく「誰かが頼んだか」。**

    実機（`c9d83e6e`・2026-08-31）の主犯:

        「みおちゃん、靴下脱いでもらえる？」
           係が名指し   ['wearing']
           実際に動いた  wearing_b（相方の服）と beat_b（相方の姿勢）
           [scripter] beat_b: 'standing…' → 'sitting on a bench…'

    誰も頼んでいない相方の欄を、二度目の compile が書き換えていた。

    ただし**埋まっているだけで塞ぐと、総監督自身の指示まで止まる** ——
    「カーディガン脱いで。引いて全身に戻して」で一度目が `frame` しか動かさ
    ないと、VERIFY が `wearing` を直そうにも非空なので弾かれ、脱げない。
    """
    import inspect
    from app.muse import service

    src = inspect.getsource(service._run_duet_scripter)
    assert "k not in asked" in src, "頼まれた欄まで塞いでいる"
    # 係が落ちた回（`asked` が空）に全面停止しないこと。
    assert "} if asked else set()" in src


def test_the_end_of_turn_clerk_may_overwrite_a_filled_field():
    """拾う段は**非空の欄を書き換えられねばならない**。

    この段が走るのは「名指しされたのに動かなかった欄」だけで、そういう欄は
    前の値が入ったまま —— **定義上いつも非空**。「非空なら書かない」を足すと
    段が常に空振りし、実測（`ask_field_clerks.py`）で

        「立って。」   本番の compile 1/5   欄だけを訊く係 5/5

    だったものが死ぬ。VERIFY への規制とは**入口が違う**。
    """
    import inspect
    from app.muse import service

    src = inspect.getsource(service._ask_the_field_clerks)
    assert 'got = {k: v for k, v in got.items() if str(v or "").strip()}' in src
    assert "not str(nb.get(k)" not in src, "非空の欄を守ってしまっている"


def test_the_prose_check_drops_sentences_not_the_whole_prose():
    """散文は**手帖と正面から矛盾する文だけ**落とす。

    最初の版は内容語の過半数が手帖に無ければ散文ごと捨てた。実データ13本で
    **4本（31%）が全損**し、しかも良い散文だった:

        「A wide shot shows her sitting on a park bench, her weight settled
         back against the wood…」        未知 52% → 落とす

    散文は手帖に無い言葉で書くもの。見るのは**服と場所の二軸**だけで、
    手帖のどこかにある語は許す（`oversized_hoodie` は `hoodie` を許す）。
    直したあと、同じ13本は **13/13 そのまま**。
    """
    from app.muse import notebook

    nb = notebook.blank()
    notebook.apply_patch(nb, {
        "wearing": "oversized_hoodie, denim_skirt, black_tights, sneakers",
        "scene": "a park path, midday",
        "beat": "sitting on a bench, holding a book",
    })
    good = (
        "She sits on a bench with the book open in her lap. "
        "The oversized hoodie hangs loosely over her denim skirt and black tights."
    )
    assert notebook.fight_craft_scene(nb, good) == good

    # 外した服を名指しする文だけ落ちる。前後の文は残る。
    with_hat = good + " She slowly lowers a straw hat toward her hands."
    out = notebook.fight_craft_scene(nb, with_hat, struck={"straw_hat", "hat"})
    assert "straw hat" not in out
    assert "book open in her lap" in out and "denim skirt" in out

    # 手帖と無関係な場所を名乗る文も落ちる。
    out = notebook.fight_craft_scene(nb, good + " She stands on a neon rooftop.")
    assert "rooftop" not in out and "book open in her lap" in out


def test_the_prose_keeps_its_paragraph_breaks():
    """二人の撮影では、改行が二人の描写を分けている。潰さない。"""
    from app.muse import notebook

    nb = notebook.blank(partner=True)
    notebook.apply_patch(nb, {
        "wearing": "white blouse", "beat": "sitting on a bench",
        "wearing_b": "knit cardigan", "beat_b": "standing by the bench",
    })
    body = "She sits on the bench.\n\nHer partner stands beside her."
    assert notebook.fight_craft_scene(nb, body) == body


def test_the_mid_turn_clerk_is_not_handed_her_previous_line():
    """**台本係は彼女が話す前に走る。** だから `_last_lead_say` は必ず古い。

    実機（`0fa9dbb1`・2026-09-01）で「立って。」に対し compile が正しく
    `standing` と書いたあと、姿勢の係が**前のターンの「座って本を読んで」**を
    読んで `sitting` に戻した:

        [beat 係] beat: 'standing, hands holding a book'
                     -> 'sitting, weight on left hip, hands resting…'

    同じ汚染は weave で測ってある（7/8 対 0/8・`1564313`）。
    **ターン末の拾う段では正しい** —— あちらは彼女が話し終えている。
    """
    import inspect
    from app.muse import service

    mid = inspect.getsource(service._run_duet_scripter)
    i = mid.index("per_person.update(await chain.read_per_person(")
    assert 'her_say=""' in mid[i:i + 900], "途中の係に古い言葉を渡している"

    end = inspect.getsource(service._ask_the_field_clerks)
    assert "her_say=her_say" in end, "拾う段では彼女の答えを渡すべき"


def test_an_unchanged_marker_never_reaches_the_picture():
    """合図が値に混ざる回がある。丸ごと一致だけ見る版は通してしまう。

    実機で場所の係が「その場所, unchanged」と返し、`unchanged` がそのまま
    絵に載った。
    """
    import asyncio
    from app.muse import chain

    class _Ollama:
        def __init__(self, reply):
            self.reply = reply

        def generate_text_stream(self, prompt, **kw):
            async def _stream():
                yield {"type": "token", "text": self.reply}
            return _stream()

    got = asyncio.run(chain.read_per_person(
        _Ollama('{"各務 みお": "the school gate, unchanged"}'),
        kind="scene", note="校門の前に行こう。", name_a="各務 みお", name_b="",
        model="m", num_ctx=1024))
    assert got == {"scene": "the school gate"}

    # 丸ごと合図なら、いままでどおり何も書かない。
    assert asyncio.run(chain.read_per_person(
        _Ollama('{"各務 みお": "unchanged"}'),
        kind="scene", note="いい感じ。", name_a="各務 みお", name_b="",
        model="m", num_ctx=1024)) == {}


def test_the_camera_box_reaches_the_picture():
    """**焦点はカメラワークの箱。** 書ける箱にしても、届かなければ意味がない。

    総監督（2026-09-01）「視点は Muse A/B がどこを向いているのかなので、
    自ずと beat に入るかな。**焦点はカメラワークがいいかも。`focus to …` とか
    `long shot` とかはこの箱**かと」。

    `frame` の文面はどこにも出ていなかった —— 絵に載っていたのは
    `framing_tags` が正規化した一語（`full_body` など）だけ。手帖に
    `focus on Mio` と書いても届かない。

    **共有面の先頭に置く。** 総監督「priority はプロンプト内の位置」で、
    どちらに寄るかは場所や光より先に効いてほしい。
    """
    from app.muse import notebook

    nb = notebook.blank(partner=True)
    notebook.apply_patch(nb, {
        "frame": "medium shot, focus on Mio",
        "scene": "a park bench, afternoon",
        "bg": "trees",
        "light": "soft daylight",
        "atmosphere": "quiet",
    })
    wide = notebook.frame_wide_phrases(nb)
    assert "focus on Mio" in wide
    # カメラが先。場所より前に置く。
    assert wide.index("medium shot") < wide.index("a park bench")


def test_the_gaze_belongs_to_each_person_now():
    """視線は人ごとの箱（`beat`）に入る。**共有の一欄では二つの答えを持てない。**

    実機（`c9d83e6e`）で「すみれちゃんは後ろを向いて、遠くを見てて。
    みおちゃんはこっち見て」が `frame` 一本に潰れ、片方が消えた。
    """
    from app.muse import chain, identity, notebook

    low = chain.build_scripter_system().lower()
    assert "eyes are beat's" in low
    assert "focus on" in low

    nb = notebook.blank(partner=True)
    notebook.apply_patch(nb, {
        "beat": "sitting, looking at the camera",
        "beat_b": "standing, looking toward the distance",
        "wearing": "white blouse", "wearing_b": "knit cardigan",
        "frame": "medium shot, focus on Mio",
    })
    cast = [{"name": "Mio", "identity_tags": ["silver_hair", "bob_cut"]},
            {"name": "Sumire", "identity_tags": ["blonde_hair", "braid"]}]
    out = identity.assemble_from_boxes(
        cast=cast, people=notebook.mint_person_box(nb, partner=True),
        frame_wide=notebook.frame_wide_phrases(nb),
        style="anime_coloring", framing="auto", scene="",
    )
    lines = {l.split(":")[0].strip(): l for l in out.splitlines() if ":" in l}
    assert "looking at the camera" in lines["Mio"]
    assert "looking toward the distance" in lines["Sumire"]
    assert "looking toward the distance" not in lines["Mio"]
    assert "focus on Mio" in out


def test_a_person_is_never_background():
    """「すみれちゃんは背景で」は**カメラの話**であって、背景の欄ではない。

    実機（`47cb5f1c`・2026-09-01）で焦点は正しく動いたが、`bg` にこれが
    残った:

        frame: long shot, focus on both        ← 正しい
        bg   : **Mio close up, Sumire in background**

    「みおちゃんに寄って。すみれちゃんは背景でいいよ」の**「背景で」を背景の
    欄への指示と読んだ**。引きに戻したあとも `Mio close up` がプロンプトに
    残り、焦点の指示と正面から矛盾していた。

    実測（4件×5回・係を直接叩く）:

        みおちゃんに寄って。すみれちゃんは背景でいいよ  書いた 0/5  ✓
        今度はすみれちゃんに焦点を。みおちゃんはぼかして 書いた 0/5  ✓
        背景に噴水を入れて                       書いた 5/5  ✓
        後ろにベンチをもう一つ置こう                書いた 5/5  ✓
    """
    from app.muse import chain

    bg = chain._PER_PERSON["bg"][2]
    # **名指しではなく種類で断つ。** 「すばるちゃんは背景で」には効いていたが、
    # 「**二人を小さく捉えて**、木々を多めに」——カメラと背景が同じ一行に
    # 入った回——で 4/5 が `two people, park trees` を書いた（実機
    # `98ab63a5`・2026-09-02）。名前を挙げる言い方だけを塞いでいた。
    #
    # 実測（5件×5回）で、人が入るのは全件 0/5。「木々を多めに」は
    # `many trees` だけを書く。
    assert "Never write a person here" in bg
    assert "two people" in bg, "言い換えを列挙しないと `two people` が通る"
    assert "FRAME" in bg, "行き先を言わないと、どこへ書けばよいか分からない"
    # 本当の背景の仕事は残っている。
    assert "buildings behind her" in bg or "what ELSE is in the picture" in bg


def test_each_person_is_written_as_one_run():
    """**一人ぶんを一続きに書く。** 交互に並べると体型が混ざる。

    実機（`d2a56ace`・2026-09-02）の並びと、その絵:

        Mio is …, flat_chest, slim,
        Subaru is …, large_breasts, tall,
        Mio: lying on the bench, …
        Subaru: standing near the bench, …

    **人が二回ずつ交互に出る**ので、どこからどこまでが一人ぶんか見失う。
    絵ではみおがすばるの胸を引き受け、すばるの姿勢（立つ）も座りに化けた。
    総監督「Mio danbooru / Mio 散文 / Subaru danbooru / Subaru 散文 と
    したほうがいいかも」。
    """
    from app.muse import identity, notebook

    nb = notebook.blank(partner=True)
    notebook.apply_patch(nb, {
        "wearing": "professional_blouse, tailored_trousers",
        "beat": "lying on the bench, hands supporting head",
        "wearing_b": "knit_cardigan, long_skirt",
        "beat_b": "standing near the bench",
        "scene": "a park, daytime",
    })
    cast = [
        {"name": "Mio", "identity_tags": ["silver_hair", "flat_chest", "slim"]},
        {"name": "Subaru", "identity_tags": ["navy_hair", "large_breasts", "tall"]},
    ]
    out = identity.assemble_from_boxes(
        cast=cast, people=notebook.mint_person_box(nb, partner=True),
        frame_wide=notebook.frame_wide_phrases(nb),
        style="anime_coloring", framing="auto", scene="They share the bench.",
    )
    rows = [l for l in out.splitlines() if l.startswith(("Mio", "Subaru"))]
    # みおの二行が続き、そのあとすばるの二行。**交互にしない。**
    assert rows[0].startswith("Mio is ")
    assert rows[1].startswith("Mio: ")
    assert rows[2].startswith("Subaru is ")
    assert rows[3].startswith("Subaru: ")
    # 体つきは自分の行にだけ。
    assert "flat_chest" in rows[0] and "flat_chest" not in rows[2]
    assert "large_breasts" in rows[2] and "large_breasts" not in rows[0]


def test_the_compile_is_told_a_person_is_never_background():
    """人が背景の欄に入る。係には言ってあったが、**compile には言っていなかった**。

    実機（`d2a56ace`）で `bg: two people, park trees`。総監督の報告
    「背景に『すばる』という文言が入った」と同じ形。
    """
    from app.muse import chain

    built = chain.build_scripter_system()
    assert "never a person" in built
    # 係のほうにも同じ境目があること（言い方は `715b2b2` で種類ベースに変えた）。
    assert "Never write a person here" in chain._PER_PERSON["bg"][2]


def test_a_japanese_name_never_leaves_the_clerk():
    """係の出口で、名前をラテン表記へ差し替える。

    実機（`2088299b`・2026-09-02）で姿勢の係がこう書いた:

        beat_b: standing near the fountain, finger poking **みお's** cheek

    そのまま絵のプロンプトへ載る。人名タグを落とす門は前からあるが
    （`_scrub_invented_tags`）、**あれはタグ側だけで、欄の文面は素通り**
    だった。

    **条文には足していない。** 「名前はラテン表記で」と書き足して測ったが、
    条文あり／なし・門あり／なしの三通りとも **0/30** で差が出なかった ——
    実機で一度出たものが、同じ行を30回叩いても再現しない稀な事象。
    **効果の測れない条文は入れない**（この現場の 8,281字 → 2,327字 の教訓）。

    門は決定的に効く。確率ではなく保証。
    """
    from app.muse.chain import latin_names_in

    cast = [{"name": "Mio Kagami", "name_ja": "各務 みお"},
            {"name": "Subaru Asakura", "name_ja": "朝倉 すばる"}]
    assert latin_names_in("finger poking みお's cheek", cast) == (
        "finger poking Mio's cheek")
    # 姓だけ・名だけでも差し替える —— 実機に出たのは「みお」だった。
    assert latin_names_in("leaning toward 各務 みお, looking at 朝倉 すばる", cast) == (
        "leaning toward Mio, looking at Subaru")
    # 名前が無い文は触らない。
    plain = "sitting on a bench, hands in lap"
    assert latin_names_in(plain, cast) == plain
    # 相方がいなければ何もしない（ラテン表記が揃わない回も含む）。
    assert latin_names_in("finger poking みお's cheek", None) == (
        "finger poking みお's cheek")


def test_both_clerk_call_sites_pass_the_cast():
    """門は**係を呼ぶ二箇所とも**通す。片方だけだと、そこから漏れる。"""
    import inspect
    from app.muse import service

    for fn in (service._run_duet_scripter, service._ask_the_field_clerks):
        src = inspect.getsource(fn)
        i = src.index("chain.read_per_person(")
        assert "cast=_cast(session)" in src[i:i + 500], fn.__name__
