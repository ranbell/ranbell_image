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
    # ポーズは beat が持つので、体の席には枠を与えない。
    assert crew.craft_slot("beat:ichibyou") == ""
    assert crew.craft_slot("spine:bane") == ""
    assert len(set(crew.CRAFT_SLOTS.values())) == len(crew.CRAFT_SLOTS)


def test_crew_look_records_owner_and_shows_up_in_the_ledger():
    session = {
        "mode": "", "inputs": {"locale": "ja", "crew_ids": ["gaffer:gyakkou"]},
        "notebook": {}, "craft": {},
    }
    service._record_crew_look(
        session, "gaffer:gyakkou", "low sun from behind, hard rim on the jaw",
    )
    service._record_crew_look(session, "lens:pinto", "85mm, shallow, eyes sharp")
    # 他人の枠は書けない（席に枠がなければ黙って捨てる）
    service._record_crew_look(session, "beat:ichibyou", "wide_shot, full body")

    look = service.crew_look(session)
    assert look["LIGHT"].startswith("low sun")
    assert look["OPTICS"].startswith("85mm")
    assert "SHAPE" not in look and len(look) == 2
    block = service.crew_look_block(session)
    assert "LIGHT: low sun from behind" in block
    assert [e["muse_id"] for e in session["ledger"]] == [
        "gaffer:gyakkou", "lens:pinto",
    ]


def test_crew_look_seeds_light_from_the_plan():
    session = {
        "mode": "", "inputs": {"theme": "x", "locale": "ja", "crew_ids": ["actress"]},
        "plan": {"place": "classroom", "hour": "dusk", "light": "backlit, low sun"},
        "costume": {}, "craft": {}, "notebook": {},
    }
    service.sync_crew_notebook(session, force_scene=True)
    assert service.crew_look(session)["LIGHT"] == "backlit, low sun"
    # …そして scene は場所と時間だけになる。
    assert "backlit" not in notebook.of(session)["scene"]


def test_struck_items_never_re_enter_through_crew_look():
    session = {
        "mode": "", "inputs": {"locale": "ja", "crew_ids": ["propshop:takarabako"]},
        "notebook": {}, "craft": {}, "struck": ["parasol"],
    }
    service._record_crew_look(
        session, "propshop:takarabako", "a parasol leaning on the bench",
    )
    assert "PROPS" not in service.crew_look(session)
