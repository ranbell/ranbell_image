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
