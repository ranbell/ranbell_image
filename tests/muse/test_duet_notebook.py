"""Notebook + scripter path for 主演撮り — live craft, no prep gate."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import notebook, service, session_db
from tests.muse.test_duet import _duet_session  # noqa: E402
from tests.muse.test_service import FakeDb, FakeOllama  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


def _scripter_block(
    *, intent="shot", atmosphere="", scene="", frame="", wearing="", beat="",
    vibe="", open_="", tags="", craft_scene="", clear_open="no",
):
    return "\n".join([
        f"INTENT: {intent}",
        f"ATMOSPHERE: {atmosphere}" if atmosphere else "",
        f"SCENE: {scene}" if scene else "",
        f"FRAME: {frame}" if frame else "",
        f"WEARING: {wearing}" if wearing else "",
        f"BEAT: {beat}" if beat else "",
        f"VIBE: {vibe}" if vibe else "",
        f"OPEN: {open_}" if open_ else "",
        f"CLEAR_OPEN: {clear_open}",
        "STANDING: none",
        "UNCHANGED: none",
        f"TAGS: {tags}" if tags else "TAGS: none",
        f"CRAFT_SCENE: {craft_scene}" if craft_scene else "CRAFT_SCENE: none",
    ])


class NotebookOllama(FakeOllama):
    """Keyword → scripter labelled block; Muse always says a short SAY."""

    def __init__(self, scripts=None):
        super().__init__()
        self.scripts = scripts or {}
        self.scripter_prompts: list[str] = []

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        text = "SAY: うん、その感じ。"
        if "studio scripter" in system or "shot notebook" in system:
            self.scripter_prompts.append(str(prompt))
            # Longest keyword wins so later turns like "また煽って、カーディガン"
            # do not match an earlier bare "煽って" script.
            hits = [k for k in self.scripts if k in str(prompt)]
            key = max(hits, key=len) if hits else ""
            text = self.scripts.get(key) or _scripter_block(
                intent="casual", vibe="chatting",
            )

        async def _stream():
            yield {"type": "token", "text": text}
        return _stream()


def test_parse_scripter_intent_and_patch():
    raw = _scripter_block(
        intent="mixed",
        wearing="sailor uniform, straw hat",
        beat="leaning on fence",
        frame="eye-level, looking at viewer",
        tags="sailor_collar, straw_hat, leaning, looking_at_viewer",
        craft_scene="She leans on a fence in a sailor uniform and straw hat.",
    )
    out = notebook.parse_scripter(raw)
    assert out["intent"] == "mixed"
    assert "straw hat" in out["patch"]["wearing"]
    assert "straw_hat" in out["tags"]
    assert "looking_at_viewer" in out["tags"]


def test_apply_patch_absolute_replace():
    nb = notebook.blank()
    notebook.apply_patch(nb, {"wearing": "jacket, skirt"})
    notebook.apply_patch(nb, {"wearing": "blouse, skirt"})
    assert nb["wearing"] == "blouse, skirt"
    assert nb["rev"] == 2


@pytest.mark.asyncio
async def test_live_compile_without_prep():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "麦わら": _scripter_block(
            intent="shot",
            scene="rooftop fence, late afternoon",
            frame="eye level, looking at viewer",
            wearing="sailor uniform, straw hat",
            beat="leaning on the fence",
            tags="rooftop, fence, sailor_collar, straw_hat, leaning, looking_at_viewer",
            craft_scene="On a rooftop she leans on the fence in a sailor uniform and straw hat.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)

    await service.post_duet_chat(db, ollama, s, "屋上でフェンスにもたれて、麦わら帽子")
    tags = s["craft"]["tags"]
    assert "straw_hat" in tags
    assert "sailor_collar" in tags
    assert s["notebook"]["wearing"]
    # Prep was never pressed.
    assert any(m.get("role") == "muse" for m in s["chat"])


@pytest.mark.asyncio
async def test_casual_does_not_wipe_craft():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "帽子": _scripter_block(
            intent="shot",
            wearing="straw hat, white shirt",
            beat="standing",
            frame="eye level, looking at viewer",
            tags="straw_hat, white_shirt, standing, looking_at_viewer",
            craft_scene="She stands in a white shirt and straw hat.",
        ),
        "かき氷": _scripter_block(intent="casual", vibe="talking about melon shaved ice"),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子かぶって")
    before = s["craft"]["tags"]
    await service.post_duet_chat(db, ollama, s, "かき氷なら何味がいい？")
    assert s["craft"]["tags"] == before
    assert "straw_hat" in before


@pytest.mark.asyncio
async def test_hat_off_full_replace_removes_hat():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "かぶ": _scripter_block(
            intent="shot",
            wearing="sailor uniform, straw hat",
            beat="standing",
            frame="eye level, looking at viewer",
            tags="sailor_collar, straw_hat, standing, looking_at_viewer",
            craft_scene="Sailor uniform and straw hat.",
        ),
        "要らない": _scripter_block(
            intent="shot",
            wearing="sailor uniform",
            beat="standing",
            frame="eye level, looking at viewer",
            tags="sailor_collar, standing, looking_at_viewer",
            craft_scene="Sailor uniform, no hat.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子をかぶせて")
    await service.post_duet_chat(db, ollama, s, "その麦わら帽子はもう要らない")
    tags = s["craft"]["tags"]
    assert "straw_hat" not in tags
    assert "sailor_collar" in tags


@pytest.mark.asyncio
async def test_low_angle_gaze_is_looking_down_not_up():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "セーラー": _scripter_block(
            intent="shot",
            wearing="sailor uniform",
            beat="leaning on fence",
            frame="eye level, looking at viewer",
            tags="sailor_collar, leaning, looking_at_viewer, eye_level",
            craft_scene="Eye-level lean on a fence.",
        ),
        "煽って": _scripter_block(
            intent="shot",
            wearing="sailor uniform",
            beat="leaning on fence",
            frame="low angle from below, she looks down into the lens",
            tags="sailor_collar, leaning, from_below, low_angle, looking_down",
            craft_scene="Low angle; she looks down toward the lens.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "セーラーでフェンスにもたれて")
    await service.post_duet_chat(db, ollama, s, "やっぱり下から煽って")
    tags = s["craft"]["tags"]
    assert "from_below" in tags or "low_angle" in tags
    assert "looking_down" in tags
    assert "looking_up" not in tags


@pytest.mark.asyncio
async def test_look_up_rewrites_frame_not_merge():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "煽って": _scripter_block(
            intent="shot",
            wearing="white shirt",
            beat="standing",
            frame="low angle, looks down into lens",
            tags="white_shirt, standing, from_below, looking_down",
            craft_scene="Low angle looking down.",
        ),
        "見上げ": _scripter_block(
            intent="shot",
            wearing="white shirt",
            beat="standing, head tilted back toward the sky",
            frame="slightly low camera, she looks up at the sky away from lens",
            tags="white_shirt, standing, looking_up, from_below",
            craft_scene="She looks up at the sky; camera adjusted as one story.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "下から煽って")
    # Looking up + from_below is refused by the safety check — craft stays prior.
    # A clean rewrite without the conflict should compile:
    ollama.scripts["見上げ"] = _scripter_block(
        intent="shot",
        wearing="white shirt",
        beat="standing, head tilted toward the sky",
        frame="eye level three-quarter, looking up at the sky",
        tags="white_shirt, standing, looking_up, eye_level",
        craft_scene="She looks up at the sky at eye level.",
    )
    await service.post_duet_chat(db, ollama, s, "空を見上げて")
    tags = s["craft"]["tags"]
    assert "looking_up" in tags
    assert "looking_down" not in tags


@pytest.mark.asyncio
async def test_scripter_prompt_has_no_diary_injection():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "公園": _scripter_block(
            intent="shot",
            scene="park bench at dusk",
            wearing="cardigan",
            beat="sitting",
            frame="eye level",
            tags="park, bench, cardigan, sitting",
            craft_scene="Park bench at dusk.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["memories"] = ["堤防で傘をさしていた"]
    s["social_seeds"] = ["今度は赤い傘が流行ってる"]
    s["caught"] = {"ids": ["x"], "summary": "日記を読まれた"}
    s["handpost_notices"] = ["足は映さないで"]
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "公園のベンチで")
    joined = "\n".join(ollama.scripter_prompts)
    assert "傘" not in joined
    assert "流行" not in joined
    assert "日記を読まれた" not in joined
    assert "足は映さない" not in joined
    assert "NOTEBOOK NOW" in joined


@pytest.mark.asyncio
async def test_casual_chit_chat_skips_scripter():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "帽子": _scripter_block(
            intent="shot",
            wearing="straw hat",
            beat="standing",
            frame="eye level",
            tags="straw_hat, standing",
            craft_scene="Hat.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子かぶって")
    before = len(ollama.scripter_prompts)
    await service.post_duet_chat(db, ollama, s, "かき氷なら何味がいい？")
    assert len(ollama.scripter_prompts) == before
    assert "straw_hat" in s["craft"]["tags"]


@pytest.mark.asyncio
async def test_open_affirm_promotes_and_compiles():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "ベンチ": _scripter_block(
            intent="shot",
            scene="park bench at dusk",
            wearing="thin cardigan",
            beat="sitting on a bench",
            frame="eye level",
            open_="落ち葉を一枚だけ手に",
            tags="park, bench, cardigan, sitting",
            craft_scene="Park bench, no leaf yet.",
        ),
        "いいね": _scripter_block(
            intent="casual",
            vibe="happy",
            clear_open="yes",
        ),
        "COMPILE ONLY": _scripter_block(
            intent="shot",
            scene="park bench at dusk",
            wearing="thin cardigan",
            beat="sitting on a bench, holding one fallen leaf",
            frame="eye level",
            tags="park, bench, cardigan, sitting, leaf",
            craft_scene="Park bench with one leaf in hand.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "ベンチに座って薄いカーディガン")
    assert s["notebook"]["open"]
    await service.post_duet_chat(db, ollama, s, "いいね")
    assert s["notebook"]["open"] == ""
    assert "落ち葉" in (s["notebook"]["beat"] + s["notebook"]["wearing"])
    assert "leaf" in s["craft"]["tags"]


@pytest.mark.asyncio
async def test_scripter_exception_keeps_craft_and_muse_talks():
    db = FakeDb()

    class Boom(NotebookOllama):
        async def generate_text(self, prompt, **kw):
            system = str(kw.get("system") or "")
            if "studio scripter" in system or "shot notebook" in system:
                raise RuntimeError("scripter down")
            return await super().generate_text(prompt, **kw)

        def generate_text_stream(self, prompt, **kw):
            system = str(kw.get("system") or "")
            if "studio scripter" in system or "shot notebook" in system:
                async def _boom():
                    raise RuntimeError("scripter down")
                    yield {"type": "token", "text": ""}  # pragma: no cover
                return _boom()
            return super().generate_text_stream(prompt, **kw)

    ollama = Boom(scripts={
        "帽子": _scripter_block(
            intent="shot",
            wearing="straw hat",
            beat="standing",
            frame="eye level",
            tags="straw_hat, standing",
            craft_scene="Hat.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子")
    before = s["craft"]["tags"]
    # Force next scripter call to boom via keyword that needs scripter
    ollama.scripts.clear()
    await service.post_duet_chat(db, ollama, s, "帽子外して煽って")
    assert s["craft"]["tags"] == before
    assert s.get("craft_dirty") is True
    assert any(m.get("role") == "muse" for m in s["chat"])


@pytest.mark.asyncio
async def test_dialogue_path_reunion_recall_chat_shot_affirm(monkeypatch):
    """再会 → recall → 雑談 → shot → OPEN肯定 のノート正本パス。"""
    db = FakeDb()
    bond_store = {
        "distance": "もう顔見知り",
        "inside": "堤防の夕焼けを一緒に見た",
        "last": "堤防 / セーラー",
    }
    taste_store = {"prefers": "ローアングル", "avoids": "足", "notes": ""}

    async def _bond(db, cid):
        return dict(bond_store)

    async def _taste(db, cid):
        return dict(taste_store)

    async def _chem(db, cid, limit=2):
        return ["息が合いやすい"]

    async def _set_bond(db, cid, bond):
        bond_store.update(bond)
        return {
            "distance": bond_store["distance"],
            "inside": bond_store["inside"],
            "last": bond_store["last"],
        }

    async def _set_taste(db, cid, taste):
        taste_store.update(taste)
        return {
            "prefers": taste_store.get("prefers", ""),
            "avoids": taste_store.get("avoids", ""),
            "notes": taste_store.get("notes", ""),
        }

    monkeypatch.setattr(service.presets_db, "get_bond", _bond)
    monkeypatch.setattr(service.presets_db, "get_showrunner_taste", _taste)
    monkeypatch.setattr(service.presets_db, "get_recent_chemistry_notes", _chem)
    monkeypatch.setattr(service.presets_db, "update_bond", _set_bond)
    monkeypatch.setattr(service.presets_db, "update_showrunner_taste", _set_taste)

    async def _search(db, ollama, *, character_id, query, limit=3):
        return [{
            "when": "堤防の夕焼け",
            "feel": "風が強かった",
            "liked": "セーラー",
            "shot": "looking_at_viewer, sailor_collar",
        }]

    monkeypatch.setattr(service.memories_db, "search", _search)

    ollama = NotebookOllama(scripts={
        "この前": (
            "INTENT: recall\nVIBE: remembering the embankment\n"
            "CLEAR_OPEN: no\nUNCHANGED: none\nTAGS: none\nCRAFT_SCENE: none"
        ),
        "かき氷": (
            "INTENT: casual\nVIBE: chatting about shaved ice\n"
            "CLEAR_OPEN: no\nUNCHANGED: none\nTAGS: none\nCRAFT_SCENE: none"
        ),
        "屋上": _scripter_block(
            intent="shot",
            scene="school rooftop at dusk",
            frame="eye level, looking at viewer",
            wearing="sailor uniform",
            beat="leaning on the fence",
            open_="ラムネを片手に",
            tags="rooftop, fence, sailor_collar, leaning, looking_at_viewer",
            craft_scene="Rooftop lean in sailor uniform.",
        ),
        "いいね": _scripter_block(
            intent="casual",
            vibe="happy",
            clear_open="yes",
        ),
        "COMPILE ONLY": _scripter_block(
            intent="shot",
            scene="school rooftop at dusk",
            frame="eye level, looking at viewer",
            wearing="sailor uniform",
            beat="leaning on the fence, holding ramune",
            tags="rooftop, fence, sailor_collar, leaning, ramune, looking_at_viewer",
            craft_scene="Rooftop lean with ramune.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["character"]["name_ja"] = "あさひ"
    await session_db.save(db, s)

    s = await service.start_duet(db, ollama, s)
    assert s.get("bond", {}).get("inside")
    assert "堤防" in (s.get("bond") or {}).get("inside", "")
    # Prior sticky notebook so chit-chat can skip scripter (empty shot would
    # still call it for long theme-like lines).
    notebook.apply_patch(s["notebook"], {
        "scene": "embankment at dusk",
        "frame": "eye level",
        "wearing": "sailor uniform",
        "beat": "standing in the wind",
    })
    s["craft"] = {
        "tags": "embankment, sailor_collar, looking_at_viewer",
        "scene": "Embankment at dusk.",
        "prompt": "1girl, embankment",
        "pose_intent": "",
    }
    await session_db.save(db, s)

    # Reunion / recall — Muse prompt must ground cited nouns.
    await service.post_duet_chat(db, ollama, s, "この前の堤防、覚えてる？")
    muse_prompts = [
        c["prompt"] for c in ollama.calls
        if "studio scripter" not in str(c.get("system") or "")
        and "shot notebook" not in str(c.get("system") or "")
    ]
    joined_muse = "\n".join(muse_prompts)
    assert "CITED_MEMORIES" in joined_muse or "堤防" in joined_muse
    assert "関係" in joined_muse
    assert "GROUNDED_TOKENS" in joined_muse

    # Casual skip — craft stays.
    before_scripts = len(ollama.scripter_prompts)
    before_tags = str((s.get("craft") or {}).get("tags") or "")
    await service.post_duet_chat(db, ollama, s, "かき氷なら何味がいい？")
    assert len(ollama.scripter_prompts) == before_scripts
    assert str((s.get("craft") or {}).get("tags") or "") == before_tags

    # Shot + OPEN affirm.
    await service.post_duet_chat(db, ollama, s, "屋上でセーラー、フェンスにもたれて")
    assert "sailor" in s["notebook"]["wearing"] or "sailor" in s["craft"]["tags"]
    assert s["notebook"]["open"]
    await service.post_duet_chat(db, ollama, s, "いいね")
    assert s["notebook"]["open"] == ""
    assert "ramune" in s["craft"]["tags"] or "ラムネ" in s["notebook"]["beat"]

    # Continuity write after ③ snapshot.
    s["continuity_snapshot"] = {
        "theme": "屋上",
        "notebook": dict(s["notebook"]),
        "craft_tags": s["craft"]["tags"],
        "craft_scene": s["craft"]["scene"],
    }
    s["standing"] = ["足は映さない"]
    s.pop("continuity", None)
    await service.record_shoot_continuity(db, s, ollama=ollama)
    assert "足" in taste_store.get("avoids", "")


def test_cited_allowlist_extracts_grounded_tokens():
    s = {
        "memories": ["堤防で夕焼けを見た"],
        "cited_memories": [{"when": "公園のベンチ", "feel": "風", "liked": "帽子"}],
        "bond": {"distance": "", "inside": "セーラーが似合う", "last": ""},
        "partner_memories": [],
    }
    allow = service._cited_allowlist(s)
    joined = " ".join(allow)
    assert "堤防" in joined
    assert "セーラー" in joined or "公園" in joined


def test_bond_and_taste_from_snapshot_are_short():
    s = {
        "continuity_snapshot": {
            "theme": "屋上",
            "notebook": {
                "atmosphere": "夕暮れの屋上",
                "vibe": "少し照れてる",
                "wearing": "セーラー",
                "frame": "low angle 煽り",
                "open": "",
            },
        },
        "standing": ["足は映さない"],
    }
    bond, taste = service._bond_and_taste_from_snapshot(s)
    assert bond["last"]
    assert "ローアングル" in taste["prefers"] or "セーラー" in taste["prefers"]
    assert "足" in taste["avoids"]
