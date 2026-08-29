"""制作スタッフ: PLAN/COSTUME → living notebook → scripter craft compile."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook, service, session_db
from tests.muse.test_duet_notebook import NotebookOllama, _scripter_block
from tests.muse.test_service import FakeDb, FakeOllama


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


async def _crew_session(db, **over):
    session = await service.create_session(db, {
        "theme": "夕暮れの屋上で待つ",
        "character_id": "char-1",
        "workflow": "w.json",
        "model": "m",
        "crew_preset": "trio",
        **over,
    })
    session["character"] = {
        "id": "char-1", "name": "Hana", "name_ja": "花",
        "identity_tags": ["1girl", "black_hair"],
        "personality": {}, "palette": [], "signature_prop": "",
    }
    service._rebuild_brief(session)
    await session_db.save(db, session)
    return session


def test_uses_notebook_after_crew_seed():
    session = {
        "mode": "",
        "inputs": {"theme": "夕暮れの屋上で待つ", "locale": "ja", "crew_ids": ["actress"]},
        "plan": {
            "place": "rooftop", "hour": "sunset", "light": "golden hour",
            "action": "waiting",
        },
        "costume": {
            "hero": "school blazer",
            "garments": "top=blazer / bottom=skirt / feet=loafers",
            "tags": ["blazer", "skirt", "loafers"],
        },
        "craft": {},
        "notebook": {},
    }
    assert service.uses_notebook(session) is False
    # Plan-style mirror must not silence the opening craft pass.
    service.sync_crew_notebook(session, force_scene=True)
    assert service.uses_notebook(session) is False
    service.sync_crew_notebook(session, force_wearing=True, force_scene=True, activate=True)
    assert service.uses_notebook(session) is True
    nb = notebook.of(session)
    assert "rooftop" in nb["scene"]
    assert "blazer" in nb["wearing"] or "school blazer" in nb["wearing"]
    assert int(nb["rev"] or 0) >= 1


def test_costume_change_forces_wearing_refresh():
    session = {
        "mode": "",
        "notebook_craft": True,
        "inputs": {"theme": "x", "locale": "ja", "crew_ids": ["actress"]},
        "notebook": {**notebook.blank(), "wearing": "old raincoat", "rev": 1},
        "costume": {
            "hero": "red cardigan",
            "garments": "top=cardigan / bottom=jeans",
            "tags": ["cardigan", "jeans"],
        },
        "plan": {},
        "craft": {},
    }
    service.sync_crew_notebook(session, force_wearing=True)
    assert "cardigan" in notebook.of(session)["wearing"]
    assert "raincoat" not in notebook.of(session)["wearing"]


@pytest.mark.asyncio
async def test_start_table_seeds_notebook():
    db, ollama = FakeDb(), FakeOllama()
    session = await _crew_session(db)
    session = await service.start_table(db, ollama, session)
    assert session.get("notebook_craft") is True
    nb = notebook.of(session)
    assert int(nb.get("rev") or 0) >= 1 or nb.get("atmosphere") or nb.get("scene")


@pytest.mark.asyncio
async def test_crew_note_runs_scripter_compile(monkeypatch):
    db = FakeDb()
    session = await _crew_session(db)
    session["status"] = "chat"
    session["table_stage"] = "full"
    session["craft"] = {
        "prompt": "1girl, rooftop, blazer",
        "tags": "1girl, rooftop, blazer, skirt",
        "scene": "She waits on the rooftop in a blazer.",
        "pose_intent": "waiting",
    }
    session["plan"] = {
        "place": "rooftop", "hour": "sunset", "light": "gold", "action": "waiting",
    }
    session["costume"] = {
        "hero": "blazer",
        "garments": "top=blazer / bottom=skirt",
        "tags": ["blazer", "skirt"],
    }
    session["spoken"] = ["plan", "actress", "lens", "wardrobe"]
    service.sync_crew_notebook(session, force_wearing=True, force_scene=True)

    scripts = {
        "カーディガン": _scripter_block(
            intent="shot",
            scene="rooftop at sunset",
            wearing="red cardigan over a white blouse; denim skirt",
            beat="hugging her elbows, waiting",
            tags="1girl, rooftop, sunset, red_cardigan, white_blouse, denim_skirt, standing",
            craft_scene=(
                "She stands on the rooftop at sunset in a red cardigan over a "
                "white blouse and a denim skirt, hugging her elbows while she waits."
            ),
        ),
    }
    ollama = NotebookOllama(scripts=scripts)

    async def _no_banter(*_a, **_k):
        return None

    monkeypatch.setattr(service, "_run_banter", _no_banter)

    async def _fake_table_talk(*_a, **_k):
        return [
            {
                "role": "muse", "muse_id": "wardrobe:shiwa", "name": "衣装",
                "kind": "banter", "text": "じゃあ赤いカーディガンでいきましょう。",
            },
            {
                "role": "muse", "muse_id": "actress", "name": "花",
                "kind": "banter", "text": "了解、カーディガンにします。",
            },
        ]

    monkeypatch.setattr(service, "_run_crew_table_talk", _fake_table_talk)

    out = await service.post_chat(
        db, ollama, None, None, session, "赤いカーディガンにして",
    )
    assert ollama.scripter_prompts, "crew note must call the scripter"
    tags = str((out.get("craft") or {}).get("tags") or "").lower()
    wearing = notebook.of(out).get("wearing", "").lower()
    assert "cardigan" in tags or "cardigan" in wearing


def test_trait_blurb_reflects_busy_vs_simple_background():
    from app.muse import crew
    busy = crew.trait_blurb("propshop:takarabako", locale="ja")
    simple = crew.trait_blurb("propshop:yohaku", locale="ja")
    assert "情報量" in busy or "物量" in busy
    assert "余白" in simple or "空ける" in simple


def test_parse_table_talk_keeps_speaker_order():
    raw = (
        "SPEAKER: wardrobe:shiwa\n"
        "SAY: コートにします。\n"
        "CRAFT: heavy wool melton, deep folds at the elbow\n\n"
        "SPEAKER: actress\n"
        "SAY: 寒そうだから助かる。\n\n"
        "SPEAKER: lens:pinto\n"
        "SAY: 寄りで顔を残します。"
    )
    speakers = ["wardrobe:shiwa", "actress", "lens:pinto"]
    hits = service._parse_table_talk(raw, speakers)
    assert [m for m, _, _ in hits] == speakers
    assert "コート" in hits[0][1]
    # The owned craft clause comes out of SAY, not into the chat bubble.
    assert "melton" in hits[0][2]
    assert "melton" not in hits[0][1] and "CRAFT" not in hits[0][1]
    assert hits[1][2] == ""


def test_preset_meta_exposed_on_roster():
    from app.muse import crew
    roster = crew.public_roster()
    assert "calm" in roster["preset_meta"]
    assert roster["preset_meta"]["calm"]["look_ja"]
    assert roster["preset_meta"]["calm"]["team_ja"] == "チームパステル"
    assert roster["preset_meta"]["vivid"]["team_ja"] == "チーム彩宴"
    assert roster["preset_meta"]["photoreal"]["team_ja"] == "チームフィルム"


def test_person_cards_expose_vibe_and_shoot_style():
    from app.muse import crew
    roster = crew.public_roster()
    soft = next(m for m in roster["muses"] if m["id"] == "gaffer:andon")
    assert soft["vibe_ja"]
    assert "パステル" in soft["shoot_style_ja"] or "包" in soft["shoot_style_ja"]
    gate = next(m for m in roster["muses"] if m["id"] == "gate:mon")
    assert "やさしい" in gate["vibe_ja"] or "優しい" in gate["voice_ja"]
    assert gate["say_examples"]
    prompt = crew.system_prompt_for("gate:mon")
    assert "ROOM VIBE" in prompt or "やさしい" in prompt
    assert "厳しい編集者" not in crew.MUSES["ink:ipponsen"]["voice_ja"]
    assert "即却下" not in crew.MUSES["ink:ipponsen"]["voice_ja"]


# ── 掛け合いと主演（f27ef7b の会話パック化で失われたもの） ──────────────────
def test_pack_never_seats_the_lead():
    """主演はパックに入らない — 自分のターンを持つ。"""
    from app.muse import crew
    cast = crew.resolve_crew(preset="standard")
    assert any(crew.role_of(m) == "actress" for m in cast), "cast must hold the Lead"
    pack = service._pack_speakers(cast)
    assert pack, "the floor still speaks"
    assert all(crew.role_of(m) != "actress" for m in pack)


def test_packed_prompt_carries_each_person_card():
    """1行ロスターではなく、席ごとの声・口調・例セリフが入る。"""
    from app.muse import crew
    speakers = ["wardrobe:shiwa", "spine:bane", "gaffer:gyakkou"]
    prompt = crew.table_talk_system_prompt(
        speakers, base_style="anime", locale="ja",
        preset_id="standard", seed="sess-1", lead_name="花",
    )
    for mid in speakers:
        assert f"`{mid}`" in prompt
        assert crew.MUSES[mid]["voice_ja"] in prompt
        assert crew.MUSES[mid]["line_ja"] in prompt
        assert crew._pick_say_example(mid, "sess-1") in prompt
    # 反応の契約（名指し・エコー禁止・主演に向けて話す）
    assert "names the person before them" in prompt
    assert "echo is not a reaction" in prompt
    assert "花" in prompt


@pytest.mark.asyncio
async def test_crew_note_gives_the_lead_her_own_voice(monkeypatch):
    """ノート1回で、主演がSAYと独り言(ASIDE)を出し、班はそれに反応する。"""
    from app.muse import crew

    class LeadOllama(NotebookOllama):
        def generate_text_stream(self, prompt, **kw):
            system = str(kw.get("system") or "")
            if "ASIDE:" in system:
                self.calls.append({**kw, "prompt": prompt})

                async def _stream():
                    yield {"type": "token", "text": (
                        "SAY: はい、羽織りますね。\n\n"
                        "ASIDE: ……袖、ちょっと長いかも。\n\n"
                        "CARD:\nBEAT: sitting, pulling the cardigan closed\n"
                    )}
                return _stream()
            return super().generate_text_stream(prompt, **kw)

    db = FakeDb()
    session = await _crew_session(db)
    session["status"] = "chat"
    session["table_stage"] = "full"
    session["notebook_craft"] = True
    session["spoken"] = list(service._crew_ids(session))
    session["craft"] = {
        "prompt": "1girl, rooftop", "tags": "1girl, rooftop",
        "scene": "rooftop", "pose_intent": "waiting",
    }
    ollama = LeadOllama()
    packed: list[list[str]] = []

    async def _fake_table_talk(_ollama, _session, speakers, **kw):
        packed.append(list(speakers))
        assert "羽織りますね" in str(kw.get("lead_say") or ""), \
            "the floor must be handed her actual line"
        return []

    monkeypatch.setattr(service, "_run_crew_table_talk", _fake_table_talk)

    out = await service.post_chat(
        db, ollama, None, None, session, "カーディガン羽織って",
    )

    lead = crew.DEFAULT_MEMBER["actress"]
    said = [m for m in out["chat"] if m.get("muse_id") == lead]
    assert any(m.get("kind") != "banter" for m in said), "主演のSAYが無い"
    assert any(m.get("kind") == "banter" for m in said), "主演の独り言(ASIDE)が無い"
    assert packed and all(
        crew.role_of(m) != "actress" for m in packed[0]
    ), "主演がパックにも入っている（二重発話）"


# ── 追従: ノートの契約と、総監督の一言が先に絵になる順序 ──────────────────
def test_costume_wearing_line_is_short_absolute_garments():
    """`top=… / bottom=…` の台帳表記も、素材・色の散文もノートに入れない。"""
    line = service._costume_wearing_line({
        "hero": "navy sailor uniform",
        "silhouette": "a long clean vertical",
        "layers": "white shirt / navy collar",
        "garments": "top=white_shirt, navy_collar / bottom=pleated_skirt / feet=loafers",
        "colourway": "navy 60 / white 30 / red 10",
        "fabric": "dry cotton twill that takes light flatly",
    })
    assert "=" not in line
    assert "twill" not in line and "60" not in line
    assert "navy sailor uniform" in line
    assert "pleated_skirt" in line and "loafers" in line
    assert len(line) <= 120


def test_plan_scene_line_holds_no_body_action():
    line = service._plan_scene_line({
        "place": "school rooftop", "hour": "late afternoon",
        "light": "low sun from the west", "action": "she has just sat down",
    })
    assert "school rooftop" in line and "late afternoon" in line
    assert "sat down" not in line


def test_beat_seed_is_a_stem_not_a_paragraph():
    session = {
        "mode": "", "inputs": {"theme": "x", "locale": "ja", "crew_ids": ["actress"]},
        "plan": {"place": "rooftop", "hour": "dusk", "action": "sitting on the bench"},
        "costume": {"garments": "top=blazer"},
        "craft": {"pose_intent": (
            "On the edge of a wooden bench at the school rooftop, she leans "
            "forward heavily with her chin resting on her crossed arms"
        )},
        "notebook": {},
    }
    service.sync_crew_notebook(session, activate=True)
    beat = notebook.of(session)["beat"]
    assert beat == "sitting on the bench"
    assert len(beat) <= 80


@pytest.mark.asyncio
async def test_plan_turn_does_not_walk_back_the_live_scene(monkeypatch):
    """場所が動いていないのに PLAN が scene を上書きしない。"""
    db = FakeDb()
    session = await _crew_session(db)
    session["notebook_craft"] = True
    session["plan"] = {"place": "rooftop", "hour": "sunset", "light": "gold"}
    notebook.apply_patch(notebook.of(session), {"scene": "rooftop at blue hour"})

    async def _same_plan(_ollama, **_kw):
        return {
            "say": "", "place": "rooftop", "hour": "sunset", "light": "gold",
            "must_appear": ["bench"],
        }

    monkeypatch.setattr(service.chain, "run_plan", _same_plan)

    async def _no_images(*_a, **_k):
        return []

    monkeypatch.setattr(service, "board_images", _no_images)
    await service._run_plan_turn(db, FakeOllama(), session, cfg={})
    assert notebook.of(session)["scene"] == "rooftop at blue hour"


@pytest.mark.asyncio
async def test_note_compiles_before_the_room_talks(monkeypatch):
    """compile(+VERIFY) → 主演 → 班 → fold。喋ってから compile ではない。"""
    order: list[str] = []
    db = FakeDb()
    session = await _crew_session(db)
    session["status"] = "chat"
    session["table_stage"] = "full"
    session["notebook_craft"] = True
    session["spoken"] = list(service._crew_ids(session))
    session["craft"] = {"prompt": "1girl", "tags": "1girl", "scene": "x"}

    async def _scripter(_db, _ollama, _session, _text, **_kw):
        order.append("compile")

    async def _lead(_db, _ollama, _session, _text, **_kw):
        order.append("lead")
        return "はい、座りますね。"

    async def _pack(_ollama, _session, _speakers, **_kw):
        order.append("pack")
        return []

    async def _fold(_db, _ollama, _session, **_kw):
        order.append("fold")

    async def _note(*_a, **_k):
        return None

    monkeypatch.setattr(service, "_run_crew_scripter", _scripter)
    monkeypatch.setattr(service, "_run_crew_lead_turn", _lead)
    monkeypatch.setattr(service, "_run_crew_table_talk", _pack)
    monkeypatch.setattr(service, "_fold_muse_after_talk", _fold)
    monkeypatch.setattr(service, "take_note", _note)
    monkeypatch.setattr(service, "_run_plan_turn", _note)

    await service.post_chat(db, FakeOllama(), None, None, session, "座って")
    assert order == ["compile", "lead", "pack", "fold"]


@pytest.mark.asyncio
async def test_crew_board_weaves_the_notebook_into_tags(monkeypatch):
    """班撮影でもノートがタグに織られる（旧: 主演撮り限定で、班のタグは固着した）。"""
    db = FakeDb()
    session = await _crew_session(db)
    session["notebook_craft"] = True
    session["craft"] = {
        "prompt": "1girl, rooftop, blazer",
        "tags": "1girl, rooftop, blazer",
        "scene": "an old opening line",
    }
    notebook.apply_patch(notebook.of(session), {
        "scene": "classroom at dusk",
        "wearing": "knit cardigan, pleated skirt",
        "beat": "sitting on the desk",
        "frame": "close up",
    })
    session["notebook_rev_compiled"] = 0

    woven = _scripter_block(
        intent="shot",
        tags="1girl, classroom, dusk, knit_cardigan, pleated_skirt, sitting, close_up",
        craft_scene="She sits on the desk in a knit cardigan as the classroom goes gold.",
    )
    ollama = NotebookOllama(scripts={"WEAVE": woven})
    out = await service.weave_craft_if_needed(db, ollama, session)

    tags = str((out.get("craft") or {}).get("tags") or "").lower()
    assert "knit_cardigan" in tags and "classroom" in tags
    assert "blazer" not in tags, "weave must replace the tag bag, not append to it"
    assert int(out.get("notebook_rev_compiled") or 0) == int(
        notebook.of(out).get("rev") or 0
    )


@pytest.mark.asyncio
async def test_opening_still_keeps_the_seats_craft():
    """読み合わせ直後はノート＝craft の写しなので、開幕の絵を織り直さない。"""
    db = FakeDb()
    session = await _crew_session(db)
    session["craft"] = {
        "prompt": "1girl, rooftop, blazer", "tags": "1girl, rooftop, blazer",
        "scene": "She waits on the roof.", "pose_intent": "waiting",
    }
    session["plan"] = {"place": "rooftop", "hour": "sunset", "action": "waiting"}
    session["costume"] = {"garments": "top=blazer / bottom=skirt"}
    service.sync_crew_notebook(
        session, force_wearing=True, force_scene=True, activate=True,
    )
    assert session["notebook_rev_compiled"] == int(notebook.of(session).get("rev") or 0)
    # …and once a note moves the notebook, the weave is owed again.
    notebook.apply_patch(notebook.of(session), {"wearing": "raincoat"})
    assert int(notebook.of(session).get("rev") or 0) > session["notebook_rev_compiled"]


# ── CREW LOOK: 専門席の仕事が weave まで届く ────────────────────────────────
def test_craft_slots_have_one_owner_each():
    from app.muse import crew
    assert crew.craft_slot("gaffer:gyakkou") == "LIGHT"
    assert crew.craft_slot("lens:pinto") == "OPTICS"
    # 服そのものはノートの WEARING（所有者は台本）。衣装席は生地だけ。
    assert crew.craft_slot("wardrobe:shiwa") == "CLOTH"
    # ポーズはノートの BEAT が正本。演出/振付は BODY スロットで weave まで届ける
    # （talk group で口は一人なので同じ鍵を共有してよい）。
    assert crew.craft_slot("beat:ichibyou") == "BODY"
    assert crew.craft_slot("spine:bane") == "BODY"
    owned = list(crew.CRAFT_SLOTS.values())
    # BODY だけ beat+spine で共有。他は一人一枠。
    assert owned.count("BODY") == 2
    assert len(set(owned)) == len(owned) - 1


def test_crew_look_records_owner_and_shows_up_in_the_ledger():
    """Visual Script: `タグ | 言葉`。タグはサンプラーへ、言葉は散文へ。"""
    session = {
        "mode": "", "inputs": {"locale": "ja", "crew_ids": ["gaffer:gyakkou"]},
        "notebook": {}, "craft": {},
    }
    service._record_crew_look(
        session, "gaffer:gyakkou",
        "backlighting, rim_light | low sun from behind, hard rim on the jaw",
    )
    service._record_crew_look(
        session, "lens:pinto", "depth_of_field, blurry_background | 85mm, eyes sharp",
    )
    # 演出は BODY を書ける。ポーズが weave まで届くための枠。
    service._record_crew_look(
        session, "beat:ichibyou",
        "sitting, hands_on_lap | sitting, weight left, hands in lap",
    )
    # 枠の無い席の CRAFT は黙って捨てる
    service._record_crew_look(session, "hook:kugizuke", "wide_shot | full body")

    look = service.crew_look(session)
    assert look["LIGHT"]["tags"] == "backlighting, rim_light"
    assert look["LIGHT"]["note"].startswith("low sun from behind")
    assert look["BODY"]["tags"] == "sitting, hands_on_lap"
    assert "SHAPE" not in look and "hook" not in str(look).lower()
    assert len(look) == 3

    block = service.crew_look_block(session)
    assert "LIGHT: backlighting, rim_light — low sun from behind" in block
    # 台帳は実タグで残る（破壊行列が読める）
    entry = next(e for e in session["ledger"] if e["muse_id"] == "gaffer:gyakkou")
    assert set(entry["added"]) == {"backlighting", "rim_light"}
    # サンプラーに渡す語として取り出せる
    assert service.crew_look_tags(session) == [
        "backlighting", "rim_light", "depth_of_field", "blurry_background",
        "sitting", "hands_on_lap",
    ]


def test_crew_look_without_a_bar_is_all_prose():
    """`|` の無い行は意図だけ。タグは名乗っていないので足さない。"""
    session = {"mode": "", "inputs": {"locale": "ja"}, "notebook": {}, "craft": {}}
    service._record_crew_look(
        session, "weather:shitsudo", "hazy golden particles in the evening air",
    )
    look = service.crew_look(session)["AIR"]
    assert look["tags"] == ""
    assert look["note"].startswith("hazy golden")
    assert service.crew_look_tags(session) == []


def test_the_plans_light_lands_in_the_notebook():
    """光はノートの正本フィールドに入る。crew_look は照明席の作り方のほう。

    主演撮りには構成席も照明席も居ないので、`light` が唯一の家になる。
    """
    session = {
        "mode": "", "inputs": {"theme": "x", "locale": "ja", "crew_ids": ["actress"]},
        "plan": {"place": "classroom", "hour": "dusk", "light": "backlit, low sun"},
        "costume": {}, "craft": {}, "notebook": {},
    }
    service.sync_crew_notebook(session, force_scene=True)
    assert notebook.of(session)["light"] == "backlit, low sun"
    # …そして scene は場所と時間だけ。光は混ざらない。
    assert "backlit" not in notebook.of(session)["scene"]
    # 二重管理しない: 種は crew_look ではなくノートへ。
    assert "LIGHT" not in service.crew_look(session)


def test_light_is_its_own_field_end_to_end():
    """「逆光にして」が scene や atmosphere に紛れず、次のターンで消えない。"""
    session = {"mode": "duet", "inputs": {"locale": "ja"}, "notebook": notebook.blank()}
    nb = notebook.of(session)
    notebook.apply_patch(nb, {"scene": "a classroom at dusk", "light": "backlit, hard rim"})
    assert nb["light"] == "backlit, hard rim"
    # 別のフィールドを書き換えても光は残る
    notebook.apply_patch(nb, {"beat": "standing"})
    assert nb["light"] == "backlit, hard rim"
    # ノートの表示にも出るので、台本も主演も読める
    assert "LIGHT:" in notebook.render(nb)
    # 台本の出力（ラベル / JSON どちらでも）から取り込める
    assert notebook.parse_scripter(
        "INTENT: shot\nLIGHT: one lantern at floor level"
    )["patch"]["light"] == "one lantern at floor level"


def test_struck_items_never_re_enter_through_crew_look():
    session = {
        "mode": "", "inputs": {"locale": "ja", "crew_ids": ["propshop:takarabako"]},
        "notebook": {}, "craft": {}, "struck": ["parasol"],
    }
    service._record_crew_look(
        session, "propshop:takarabako", "a parasol leaning on the bench",
    )
    assert "PROPS" not in service.crew_look(session)


# ── struck は「いま写っているもの」を締め出してはいけない ──────────────────
def test_struck_never_holds_what_the_shot_now_says():
    """立ち上がった後にまた座れる。struck は追記専用の墓場ではない。"""
    session = {"mode": "", "inputs": {"locale": "ja"}, "notebook": notebook.blank()}
    nb = notebook.of(session)
    notebook.apply_patch(nb, {"beat": "sitting on the bench", "wearing": "sailor uniform, straw hat"})
    # 立ち上がる → sitting が struck に入る
    notebook.record_struck_tokens(session, prev="sitting on the bench", new="standing", min_len=4)
    notebook.apply_patch(nb, {"beat": "standing, holding the hem"})
    assert "sitting" in notebook.struck_tokens(session)
    # 帽子を取る → straw_hat も struck
    notebook.record_struck_from_wearing(
        session, prev_wearing="sailor uniform, straw hat", new_wearing="sailor uniform",
    )
    notebook.apply_patch(nb, {"wearing": "sailor uniform"})
    assert "straw_hat" in notebook.struck_tokens(session)
    # また座らせたら、sitting は締め出しから外れる（帽子は外れたまま）
    notebook.apply_patch(nb, {"beat": "sitting on the floor"})
    live = notebook.struck_tokens(session)
    assert "sitting" not in live
    assert "straw_hat" in live
    assert "sitting" not in " ".join(notebook.live_struck(session))


def test_struck_does_not_mint_grammar_pairs():
    """文をまたいだ語のペアは物の名前ではない。"""
    toks = notebook.wearing_tokens(
        "sitting on the wooden bench while staring at nothing",
    )
    assert "wooden_bench" in toks
    for junk in ("on_the", "the_wooden", "while_staring", "at_nothing", "bench_while"):
        assert junk not in toks


def test_theme_never_seeds_the_mood_field():
    """テーマは場所も服も含む日本語文。atmosphere は英語の気分だけ。"""
    session = {
        "mode": "",
        "inputs": {"theme": "放課後の教室。セーラー服。", "locale": "ja", "crew_ids": ["actress"]},
        "plan": {"place": "classroom", "hour": "dusk"},
        "costume": {"garments": "top=blazer"}, "craft": {}, "notebook": {},
    }
    service.sync_crew_notebook(session, force_scene=True, activate=True)
    assert not str(notebook.of(session).get("atmosphere") or "").strip()


def test_only_clothes_are_struck_not_place_or_pose(monkeypatch):
    """場所や姿勢の語を struck に入れない（入れると次の指示を自分で塞ぐ）。"""
    import inspect
    src = inspect.getsource(service._run_duet_scripter)
    # wearing からの記録は残す。scene/beat/frame からの記録は無い。
    assert "record_struck_from_wearing" in src
    assert "prev=prev_scene" not in src
    assert "prev=prev_beat" not in src
    assert "prev=prev_frame" not in src


def test_weave_cannot_leave_a_worn_garment_out_of_the_tags():
    """ノートが着せている服は必ずサンプラーまで届く。"""
    session = {
        "mode": "", "session_id": "s-1", "inputs": {"locale": "ja", "framing": "auto"},
        "notebook": notebook.blank(), "craft": {}, "character": {},
    }
    notebook.apply_patch(notebook.of(session), {
        "wearing": "sailor uniform, straw hat, knit cardigan",
        "beat": "sitting", "scene": "a bench at dusk",
    })
    ok = service._apply_compiled_craft(
        session, "1girl, sailor_uniform, knit_cardigan, sitting, bench", "prose",
    )
    assert ok
    tags = str((session.get("craft") or {}).get("tags") or "")
    assert "straw_hat" in tags, tags
    # すでに入っている服を二重に足さない。
    assert tags.count("knit_cardigan") == 1


def test_removed_garment_is_not_put_back_by_coverage():
    """脱がせた服は復活させない（drop_banned の抜け道を作らない）。"""
    session = {
        "mode": "", "inputs": {"locale": "ja"}, "notebook": notebook.blank(),
        "craft": {}, "character": {}, "banned": ["straw_hat"],
    }
    notebook.apply_patch(notebook.of(session), {"wearing": "sailor uniform"})
    notebook.record_struck_from_wearing(
        session, prev_wearing="sailor uniform, straw hat",
        new_wearing="sailor uniform",
    )
    tags, _ = notebook.reconcile_wardrobe_tags(
        "1girl, sailor_uniform",
        wearing="sailor uniform",
        struck=notebook.struck_tokens(session),
        banned={"straw_hat"},
    )
    assert "straw_hat" not in tags


def test_posture_stem_always_reaches_the_tags():
    """beat が名乗る姿勢は必ずタグに出る（旧: 「立って」が語ごと消えた）。"""
    session = {
        "mode": "", "session_id": "s-2", "inputs": {"locale": "ja"},
        "notebook": notebook.blank(), "craft": {}, "character": {},
    }
    notebook.apply_patch(notebook.of(session), {
        "wearing": "sailor uniform", "beat": "standing, holding the hem of her skirt",
    })
    assert "standing" in service._missing_wearing_tags(
        session, "close_up, sailor_uniform, skirt_hem, trembling_fingertips",
    )
    # 既に入っていれば足さない。日本語の beat も拾う。
    assert "standing" not in service._missing_wearing_tags(
        session, "standing, sailor_uniform",
    )
    notebook.apply_patch(notebook.of(session), {"beat": "しゃがんで、日傘は持ったまま"})
    assert notebook.posture_stem("しゃがんで、日傘は持ったまま") == "squatting"
    assert "squatting" in service._missing_wearing_tags(session, "sailor_uniform, parasol")


def test_place_from_the_notebook_always_reaches_the_tags():
    """SCENE/BG は手帖から直接タグになる（突き合わせではない）。"""
    session = {
        "mode": "", "session_id": "s-place", "inputs": {"locale": "ja"},
        "notebook": notebook.blank(), "craft": {}, "character": {},
    }
    notebook.apply_patch(notebook.of(session), {
        "wearing": "sailor uniform",
        "beat": "standing",
        "scene": "night classroom by the window",
        "bg": "a crowd of cosplayers",
    })
    bag = notebook.apply_notebook_authority_tags(
        "close_up, standing, rooftop",
        notebook.of(session), struck=set(), banned=set(),
    )
    joined = bag.lower().replace(" ", "_")
    assert "classroom" in joined or "night_classroom" in joined
    assert "cosplayers" in joined or "crowd" in joined
    assert "rooftop" not in joined
    assert "sailor_uniform" in joined


@pytest.mark.asyncio
async def test_card_reaches_fold_but_never_a_plain_compile(monkeypatch):
    """1ターン古い CARD は compile に渡さない（脱がせる指示に勝ってしまう）。

    fold のときだけ渡す — そこでは今回の CARD が正本。
    """
    db = FakeDb()
    session = await _crew_session(db)
    session["notebook_craft"] = True
    session["muse_card"] = "WEARING: sailor uniform, straw hat\nBEAT: sitting"
    notebook.apply_patch(notebook.of(session), {
        "wearing": "sailor uniform, straw hat", "beat": "sitting",
    })
    cards: list[str] = []

    async def _run_scripter(_ollama, **kw):
        cards.append(str(kw.get("card") or ""))
        return {"intent": "casual", "patch": {}, "raw": "ok", "valid": True}

    monkeypatch.setattr(service.chain, "run_scripter", _run_scripter)

    await service._run_duet_scripter(db, FakeOllama(), session, "帽子は外して", cfg={})
    assert cards and all(c == "" for c in cards), cards

    cards.clear()
    await service._run_duet_scripter(
        db, FakeOllama(), session, "帽子は外して", cfg={}, fold=True,
    )
    assert cards and any("straw hat" in c for c in cards), cards


# ── ルックの明示指定・strike の誤爆・提案の経路 ─────────────────────────────
def test_named_look_beats_the_room_average():
    """16席の平均は常に無難な真ん中に落ちる。名前で呼べば総監督が決める。"""
    from app.muse import crew
    cast = crew.resolve_crew(preset="standard")
    assert crew.base_style_for(cast, "", "") == "anime illustration"  # 平均の実測値
    assert crew.base_style_for(cast, "", "vivid") == "vivid anime illustration"
    assert crew.base_style_for(cast, "", "flat") == "flat anime cel shading"
    # 総監督が文で書いたものより、名指しのルックが強い。
    assert crew.base_style_for(cast, "水彩っぽく", "flat") == "flat anime cel shading"
    # 知らない名前は無視して従来どおり。
    assert crew.base_style_for(cast, "水彩っぽく", "nonsense") == "水彩っぽく"


@pytest.mark.asyncio
async def test_look_change_is_said_out_loud(monkeypatch):
    db = FakeDb()
    session = await _crew_session(db)
    session["chat"] = []
    await service.patch_inputs(db, session, {"look": "vivid"})
    said = [m for m in session["chat"] if m.get("role") == "system"]
    assert said and "vivid anime illustration" in said[-1]["text"]
    assert service._style(session) == "vivid anime illustration"


def test_a_camera_note_cannot_strike_the_room():
    """「手元だけ見せて」で27語追放された回の再発防止。"""
    session = {
        "mode": "", "session_id": "s-3", "inputs": {"locale": "ja"},
        "notebook": notebook.blank(), "craft": {},
    }
    notebook.apply_patch(notebook.of(session), {
        "scene": "a cafe by the window, afternoon",
        "wearing": "cream cable knit sweater, pleated skirt",
        "beat": "standing, holding a bag",
    })
    picked = [
        "cafe", "wooden_table", "window", "cream_cable_knit_sweater",
        "pleated_skirt", "afternoon_sun", "bokeh", "warm_lighting",
    ]
    # ノートが「そこにある/着ている」と言うものは落とせない。
    assert service._sane_strike(session, picked) == []
    # 大量削除そのものが誤読なので捨てる。
    assert service._sane_strike(session, [f"prop_{i}" for i in range(9)]) == []
    # 本物の refusal は通る。
    assert service._sane_strike(session, ["neon_sign"]) == ["neon_sign"]
    # 脱がせる指示は compile より先に来るので、1件だけ弾かれるのは正常
    # （帽子はまだ WEARING にある）。残りはそのまま通す。
    assert service._sane_strike(
        session, ["pleated_skirt", "neon_sign"],
    ) == ["neon_sign"]


def test_fold_moves_the_body_and_nothing_else():
    """fold が触れるのは beat だけ。絵そのものは総監督の指示でしか動かない。

    提案欄 `open` は撤去した。390セッションで一度も提案が入らず、入っていた
    50件は `$$OPEN$$` や `clear_open: true` といったパーサのゴミで、それが
    台本のプロンプトに戻りパネルにも出ていた。席の提案は chat に残る。
    """
    assert notebook.FOLD_PATCH_KEYS == ("beat", "beat_b")
    from app.muse import chain
    assert "crew's lines from this turn" in chain.SCRIPTER_FOLD_NOTE
    assert "open" not in chain.SCRIPTER_FOLD_NOTE


def test_the_camera_is_not_in_the_picture():
    """撮影台本の口調がそのまま被写体になるのを止める。

    実セッション: ノートのどこにもカメラが無いのに、タグに `handheld_camera`、
    散文が「The camera lingers in a close-up on Mio's face」で始まっており、
    サンプラーは素直に彼女の手にカメラを描いた。
    """
    session = {
        "mode": "", "session_id": "s-4", "inputs": {"locale": "ja"},
        "notebook": notebook.blank(), "craft": {}, "character": {},
    }
    notebook.apply_patch(notebook.of(session), {
        "beat": "standing, looking toward the port lights",
        "wearing": "yellow sundress",
    })
    out = service._scrub_invented_tags(
        session, "close_up, handheld_camera, 各務 みお, yellow_sundress, bokeh",
    )
    kept = [t.strip() for t in out.split(",")]
    assert "handheld_camera" not in kept
    assert "各務 みお" not in kept          # 日本語の名前はタグではない
    assert kept == ["close_up", "yellow_sundress", "bokeh"]

    # 総監督が本当にカメラを持たせたときは通す。
    notebook.apply_patch(notebook.of(session), {"beat": "standing, holding a camera"})
    assert "handheld_camera" in service._scrub_invented_tags(
        session, "handheld_camera, close_up",
    )


def test_weave_is_told_the_camera_is_not_a_subject():
    from app.muse import chain
    assert "THE CAMERA IS NOT IN THE PICTURE" in chain.SCRIPTER_WEAVE_SYSTEM
    assert "Never write her name" in chain.SCRIPTER_WEAVE_SYSTEM


@pytest.mark.asyncio
async def test_weave_receives_the_look_and_the_room_leaning(monkeypatch):
    """35〜55語を書く weave が、ルックを知らずに書いていた。"""
    db = FakeDb()
    session = await _crew_session(db, crew_preset="flat")
    session["notebook_craft"] = True
    notebook.apply_patch(notebook.of(session), {
        "scene": "a classroom at dusk", "wearing": "sailor uniform", "beat": "sitting",
    })
    seen: dict = {}

    async def _run_scripter(_ollama, **kw):
        seen.update(kw)
        return {"intent": "shot", "patch": {}, "tags": "1girl, sitting",
                "craft_scene": "prose", "raw": "ok", "valid": True}

    monkeypatch.setattr(service.chain, "run_scripter", _run_scripter)
    session["craft_dirty"] = True
    await service.weave_craft_if_needed(db, FakeOllama(), session)

    assert seen.get("mode") == "weave"
    assert "flat anime cel shading" in str(seen.get("style") or "")
    assert str(seen.get("room_leaning") or "").strip(), "班の傾向が渡っていない"
    # 主演撮りには班の傾向は無い
    assert service._room_leaning({"mode": "duet", "inputs": {}}) == ""


def test_the_partner_wardrobe_is_restored_too():
    """相方の服も戻す（旧: WEARING_B だけ weave に落とされたまま）。"""
    session = {
        "mode": "", "session_id": "s-w", "inputs": {"locale": "ja"},
        "notebook": notebook.blank(partner=True), "craft": {},
        "character": {}, "partner_character": {"name": "Sumire Hiraoka"},
    }
    notebook.apply_patch(notebook.of(session), {
        "wearing": "professional blouse", "wearing_b": "linen apron",
    })
    tags, _ = notebook.reconcile_wardrobe_tags(
        "2girls, professional_blouse",
        wearing="professional blouse", wearing_b="linen apron",
        partner=True,
    )
    assert "linen_apron" in tags


def test_each_muse_wears_only_her_own_side_of_the_notebook():
    session = {
        "mode": "", "session_id": "s-w2", "inputs": {"locale": "ja"},
        "notebook": notebook.blank(partner=True), "craft": {},
        "character": {}, "partner_character": {"name": "Sumire Hiraoka"},
    }
    notebook.apply_patch(notebook.of(session), {
        "wearing": "professional blouse, knit cardigan",
        "wearing_b": "linen apron, dark denim shirt",
    })
    mine, hers = service._sides(
        session,
        "2girls, professional_blouse, knit_cardigan, linen_apron, "
        "dark_denim_shirt, indoors, window_light",
    )
    assert mine == ["professional_blouse", "knit_cardigan"]
    assert hers == ["linen_apron", "dark_denim_shirt"]
    # 場所と光は誰のものでもない。動かさない。
    assert "indoors" not in mine + hers
    assert "window_light" not in mine + hers


def test_a_garment_they_both_wear_belongs_to_neither_line():
    session = {
        "mode": "", "session_id": "s-w3", "inputs": {"locale": "ja"},
        "notebook": notebook.blank(partner=True), "craft": {},
        "character": {}, "partner_character": {"name": "Sumire Hiraoka"},
    }
    notebook.apply_patch(notebook.of(session), {
        "wearing": "linen apron, knit cardigan",
        "wearing_b": "linen apron, dark denim shirt",
    })
    mine, hers = service._sides(
        session, "2girls, linen_apron, knit_cardigan, dark_denim_shirt",
    )
    assert "linen_apron" not in mine + hers
    assert mine == ["knit_cardigan"]
    assert hers == ["dark_denim_shirt"]


def test_a_solo_shoot_never_splits_a_wardrobe():
    session = {
        "mode": "", "session_id": "s-solo", "inputs": {"locale": "ja"},
        "notebook": notebook.blank(), "craft": {}, "character": {},
    }
    notebook.apply_patch(notebook.of(session), {"wearing": "sailor uniform"})
    assert service._sides(session, "1girl, sailor_uniform") == []


def _w_session(**over) -> dict:
    session = {
        "mode": "", "session_id": "s-sides", "inputs": {"locale": "ja"},
        "notebook": notebook.blank(partner=True), "craft": {},
        "character": {"name": "Mio Kagami"},
        "partner_character": {"name": "Sumire Hiraoka"},
    }
    session.update(over)
    return session


def test_the_weave_own_split_is_what_places_a_tag():
    """weave は誰のものか書いている。手帖の語照合より、そちらが先。"""
    session = _w_session(craft={
        "tags_a": "blue_dress, sitting, hands_on_chest",
        "tags_b": "black_dress, standing, behind_another",
    })
    mine, hers = service._sides(
        session,
        "2girls, blue_dress, sitting, hands_on_chest, black_dress, standing, "
        "behind_another, harbor",
    )
    assert mine == ["blue_dress", "sitting", "hands_on_chest"]
    assert hers == ["black_dress", "standing", "behind_another"]


def test_a_tag_in_both_bags_belongs_to_the_picture():
    session = _w_session(craft={
        "tags_a": "blue_dress, smiling", "tags_b": "black_dress, smiling",
    })
    mine, hers = service._sides(session, "blue_dress, black_dress, smiling")
    assert "smiling" not in mine + hers
    assert mine == ["blue_dress"] and hers == ["black_dress"]


def test_an_unsplit_weave_falls_back_to_the_two_wardrobes():
    session = _w_session(craft={"tags_a": "", "tags_b": ""})
    notebook.apply_patch(notebook.of(session), {
        "wearing": "professional blouse", "wearing_b": "linen apron",
    })
    mine, hers = service._sides(
        session, "professional_blouse, linen_apron, indoors",
    )
    assert mine == ["professional_blouse"]
    assert hers == ["linen_apron"]


def test_one_garment_under_three_names_is_cut_back_to_one():
    """実測（2026-08-25）: gown / blue_dress / sleeveless_dress が同時に並んだ。"""
    session = _w_session()
    notebook.apply_patch(notebook.of(session), {
        "wearing": "blue sleeveless gown, earrings",
        "wearing_b": "black cocktail dress",
    })
    tags, sides = service._drop_garment_aliases(
        session,
        "2girls, gown, blue_dress, sleeveless_dress, earrings, black_dress, sitting",
        ("gown, blue_dress, sleeveless_dress, earrings, sitting", "black_dress"),
    )
    assert "blue_dress" not in tags and "sleeveless_dress" not in tags
    assert "gown" in tags and "earrings" in tags
    # すみれの黒は名前が合っているので残る。振り分けからも消えていない。
    assert "black_dress" in tags and sides[1] == "black_dress"


def test_an_unsplit_bag_keeps_a_name_the_other_wardrobe_owns():
    """どちらの服か決められない回は、消しにいかない。"""
    session = _w_session()
    notebook.apply_patch(notebook.of(session), {
        "wearing": "blue sleeveless gown",
        "wearing_b": "black cocktail dress",
    })
    tags, _ = service._drop_garment_aliases(
        session, "2girls, gown, blue_dress, black_dress", ("", ""),
    )
    assert "blue_dress" in tags


# ── 総監督が OK を出した絵で撮る ──────────────────────────────────
def test_the_shot_uses_the_prompt_the_board_was_drawn_with():
    """**見た絵と撮る絵を一致させる。**

    実測（`42b55492`）: `still_read_after_board` が写真を読んで手帖を書き換え、
    rev が 45 に対しコンパイル済みが 44。本番は「遅れている」と判断して織り
    直し、**総監督が OK を出したボードとは違う指示で撮っていた**。種のほうは
    ボードから引き継いでいたので、同じ賽で違う指示という最悪の形だった。
    """
    session = {
        "board": {"prompt": "2girls, Mio and Sumire, …", "seed": 7,
                  "images": ["img-1"], "pending": False, "round": 9},
        "craft": {"prompt": "織り直したあとの、別の指示"},
    }
    assert service._approved_prompt(session) == "2girls, Mio and Sumire, …"


def test_a_board_still_rendering_is_not_something_to_shoot():
    for board in ({"prompt": "x", "pending": True, "images": []},
                  {"prompt": "x", "images": []},
                  {}):
        assert service._approved_prompt({"board": board}) == ""


def test_the_photo_is_not_read_back_into_the_notebook():
    """写真読みの配線を外した。**手帖は会話で書かれるのが正本。**"""
    import inspect

    from app.muse import runner as muse_runner
    src = inspect.getsource(muse_runner)
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert "still_read_after_board" not in code


def test_her_name_is_not_a_tag_in_latin_either():
    """実測（`f8b72d5f`）: `kagami_mio` `hiraoka_sumire` が焼かれていた。

    漢字の `各務 みお` は非 ASCII で落ちていたが、ローマ字は素通りしていた。
    danbooru の人名タグは実在のキャラを指すので、**別人の顔を引いてくる**。
    """
    sess = {
        "character": {"name": "Mio Kagami", "name_ja": "各務 みお"},
        "partner_character": {"name": "Sumire Hiraoka", "name_ja": "平岡 すみれ"},
        "notebook": {}, "plan": {},
    }
    out = service._scrub_invented_tags(
        sess, "kagami_mio, hiraoka_sumire, 各務 みお, mio, silver_hair, sitting",
    )
    kept = [t.strip() for t in out.split(",")]
    assert kept == ["silver_hair", "sitting"]


def test_a_tag_that_merely_contains_a_name_word_survives():
    """`mio_park` は場所。**名前だけで出来ている語**を落とす。"""
    sess = {"character": {"name": "Mio Kagami", "name_ja": "各務 みお"},
            "notebook": {}, "plan": {}}
    out = service._scrub_invented_tags(sess, "mio_park, standing")
    assert "mio_park" in out


def test_the_prose_calls_her_what_the_prompt_calls_her():
    """実測（`156091c6`）: 地の文に「平岡 すみれ stands poised」と漢字が出た。

    サンプラーに漢字は読めない。**消さずに綴りを揃える** —— 名前で結ぶ行が
    `Mio` `Sumire` と書いている以上、地の文も同じ綴りでなければ結んだ相手を
    指せない。
    """
    sess = {"character": {"name": "Mio Kagami", "name_ja": "各務 みお"},
            "partner_character": {"name": "Sumire Hiraoka", "name_ja": "平岡 すみれ"}}
    out = service._latin_names(
        sess,
        "平岡 すみれ stands poised. Beside her, 各務 みお sits still, "
        "while すみれ is composed and みお's eyes hold a silence.",
    )
    assert "平岡" not in out and "各務" not in out
    assert "すみれ" not in out and "みお" not in out
    assert out.count("Sumire") == 2 and out.count("Mio") == 2


def test_a_scene_without_names_is_untouched():
    sess = {"character": {"name": "Mio Kagami", "name_ja": "各務 みお"}}
    text = "A close-up against the fading dusk, harbour lights blurred behind."
    assert service._latin_names(sess, text) == text


def test_the_strike_gate_hears_kanji_nashi():
    """`なし` はひらがなだけ見ていた。「靴下は無しで」が素通りしていた。

    偽陽性は LLM 一回で済むが、偽陰性は脱いだはずの服が残る。**拾う側に倒す。**
    """
    from app.muse.service import _note_looks_like_strike as looks
    for note in ("靴下は無しで", "帽子はもういい", "上着を脱いで",
                 "メガネ外して", "ジャケットはいらない"):
        assert looks(note), note
    for note in ("寄りで撮ろう", "夕暮れの港が見える公園で撮ろう"):
        assert not looks(note), note
