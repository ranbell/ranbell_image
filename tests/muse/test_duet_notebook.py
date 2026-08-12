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
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "公園のベンチで")
    joined = "\n".join(ollama.scripter_prompts)
    assert "傘" not in joined
    assert "流行" not in joined
    assert "NOTEBOOK NOW" in joined
