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
        "SAY: コートにします。\n\n"
        "SPEAKER: actress\n"
        "SAY: 寒そうだから助かる。\n\n"
        "SPEAKER: lens:pinto\n"
        "SAY: 寄りで顔を残します。"
    )
    speakers = ["wardrobe:shiwa", "actress", "lens:pinto"]
    hits = service._parse_table_talk(raw, speakers)
    assert [m for m, _ in hits] == speakers
    assert "コート" in hits[0][1]


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
