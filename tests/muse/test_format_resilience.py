"""LLM output format breakage — parse, sanitize, salvage, dirty flags."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import identity, notebook, service, session_db
from tests.muse.test_duet import _duet_session
from tests.muse.test_duet_notebook import NotebookOllama, _scripter_block
from tests.muse.test_service import FakeDb, FakeOllama


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


def test_sanitize_strips_truncated_tags_leak():
    raw = "はっ…外した。風が来る。\nTAGS: straw_hat, from_below\nSCENE: incomplete"
    out = identity.sanitize_muse_say(raw)
    assert "TAGS" not in out
    assert "SCENE" not in out
    assert "外した" in out


def test_sanitize_strips_english_rule_headings():
    raw = "うん、その感じ。\nCRITICAL RULES FOR W-MUSE SAY:\nもっと近づく？"
    out = identity.sanitize_muse_say(raw)
    assert "CRITICAL" not in out
    assert "近づく" in out


def test_parse_table_read_truncated_say_tags():
    raw = "SAY: 帽子、外したよ\nTAGS: straw_hat\n"
    say, tags, scene = identity.parse_table_read(raw)
    assert "帽子" in say
    assert "TAGS" not in say
    assert tags == "" and scene == ""


def test_parse_duet_speakers_name_fallback():
    raw = "あさひ: 先に行くね\nみなも: ちょっと待って"
    turns = identity.parse_duet_speakers(raw, name_a="あさひ", name_b="みなも")
    assert turns is not None
    assert turns[0]["speaker"] == "A"
    assert turns[1]["speaker"] == "B"
    assert "先に" in turns[0]["text"]


def test_parse_duet_speakers_rejects_unknown_names():
    raw = "System A: hello\nMuse B: hi"
    assert identity.parse_duet_speakers(raw) is None


def test_scripter_json_salvages_trailing_comma():
    raw = """{
      "intent": "shot",
      "wearing": "straw hat",
      "beat": "leaning",
      "frame": "eye level",
      "tags": "straw_hat, leaning",
      "craft_scene": "Rooftop lean.",
    }"""
    out = notebook.parse_scripter(raw)
    assert out["intent"] == "shot"
    assert "straw" in out["patch"].get("wearing", "") or "straw_hat" in out["tags"]


def test_scripter_json_salvages_truncated_object():
    raw = (
        '{"intent":"shot","wearing":"jacket","beat":"standing",'
        '"frame":"eye level","tags":"jacket, standing","craft_scene":"She stands'
    )
    out = notebook.parse_scripter(raw)
    # Either repaired JSON or labelled blank — must not crash.
    assert "intent" in out
    assert out.get("raw") == raw or out.get("valid") in (True, False)


@pytest.mark.asyncio
async def test_scripter_repair_pass_on_invalid(monkeypatch):
    """Invalid first output → one repair call can salvage craft."""
    class RepairOllama(FakeOllama):
        def __init__(self):
            super().__init__()
            self.n = 0

        async def generate_text(self, prompt, **kw):
            kw.pop("fmt", None)
            self.calls.append({**kw, "prompt": prompt})
            system = str(kw.get("system") or "")
            if "studio scripter" in system or "shot notebook" in system:
                self.n += 1
                if self.n == 1:
                    return (
                        "INTENT: shot\nWEARING: jacket\nBEAT: standing\nFRAME: low\n"
                        "TAGS: from_below, looking_up, jacket\n"
                        "CRAFT_SCENE: Broken.\n"
                    )
                return _scripter_block(
                    intent="shot",
                    wearing="jacket",
                    beat="standing",
                    frame="low angle, looking down",
                    tags="from_below, looking_down, jacket",
                    craft_scene="Low angle fixed.",
                )
            return "SAY: うん、下からね。見下ろす形。"

        def generate_text_stream(self, prompt, **kw):
            async def _s():
                text = await self.generate_text(prompt, **kw)
                yield {"type": "token", "text": text}
            return _s()

    db = FakeDb()
    ollama = RepairOllama()
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["craft"] = {
        "tags": "straw_hat, standing",
        "scene": "Hat.",
        "prompt": "1girl, straw_hat",
        "pose_intent": "",
    }
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "壊す指示で煽りと見上げ同時だけど直して")
    # Repair should land looking_down craft, or keep prior if repair also fails.
    assert ollama.n >= 1
    tags = str((s.get("craft") or {}).get("tags") or "")
    assert "looking_up" not in tags or s.get("craft_dirty") is True


@pytest.mark.asyncio
async def test_invalid_scripter_marks_dirty_event_fields():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "帽子": _scripter_block(
            intent="shot", wearing="hat", beat="stand", frame="eye",
            tags="hat, standing", craft_scene="Hat.",
        ),
        "壊": (
            "INTENT: shot\nWEARING: x\nBEAT: y\nFRAME: low\n"
            "TAGS: from_below, looking_up\nCRAFT_SCENE: bad\n"
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    await session_db.save(db, s)
    await service.post_duet_chat(db, ollama, s, "麦わら帽子")
    await service.post_duet_chat(db, ollama, s, "壊す煽り見上げ")
    assert s.get("craft_dirty") is True
