"""主演撮り: Muse CARD + Script compile/weave, struck, still-as-base."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from app.muse import crew, identity, notebook, service
from tests.muse.test_duet import _duet_session  # noqa: E402
from tests.muse.test_duet_notebook import NotebookOllama, _scripter_block  # noqa: E402
from tests.muse.test_service import FakeDb  # noqa: E402


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


def test_parse_talk_blocks_keeps_card_out_of_say():
    raw = (
        "SAY: 風、ちょっと冷たいね。\n"
        "ASIDE: 帽子、外して正解だったかも。\n"
        "CARD:\n"
        "PLACE: school rooftop\n"
        "HOUR: dusk\n"
        "WEARING: thin cardigan\n"
        "BEAT: sitting on a bench\n"
        "FRAME: eye level\n"
        "PITCH: 麦わら帽子をかぶる | 帽子なし\n"
    )
    blocks = identity.parse_talk_blocks(raw)
    say = identity.sanitize_muse_say(blocks["say"])
    assert "風" in say
    assert "PLACE" not in say
    assert "thin cardigan" in blocks["card"]
    assert "帽子" in blocks["aside"]
    assert notebook.parse_pitch_choices(blocks["pitch"]) == [
        "麦わら帽子をかぶる", "帽子なし",
    ]


def test_duet_talk_output_answers_nouns_when_asked():
    text = crew.DUET_TALK_OUTPUT.lower()
    assert "never a change-log" not in text
    assert "change log" not in text or "checklist" in text
    assert "card" in text
    assert "aside" in text
    assert "pitch" in text


def test_wearing_tokens_drop_no_hat():
    assert "hat" not in notebook.wearing_tokens("thin cardigan, no hat")
    assert "cardigan" in notebook.wearing_tokens("thin cardigan, no hat")


def test_struck_from_wearing_diff():
    session = {"struck": []}
    notebook.record_struck_from_wearing(
        session, prev_wearing="thin cardigan, straw hat",
        new_wearing="thin cardigan",
    )
    struck = notebook.struck_tokens(session)
    assert "hat" in struck or "straw_hat" in struck


def test_filter_weave_tags_drops_struck_hat():
    tags = notebook.filter_weave_tags(
        "thin_cardigan, straw_hat, knit, fabric_folds, lantern",
        wearing="thin cardigan",
        scene="rooftop at dusk",
        beat="sitting on a bench",
        struck={"hat", "straw_hat"},
    )
    low = tags.lower()
    assert "straw_hat" not in low
    assert "cardigan" in low or "thin_cardigan" in low


def test_split_atmosphere_time_moves_dusk():
    mood, place = notebook.split_atmosphere_time("tender dusk", "rooftop")
    assert "dusk" not in mood.lower()
    assert "dusk" in place.lower()
    assert "tender" in mood.lower()


def test_theme_hidden_once_shot_exists():
    session = {
        "inputs": {"theme": "屋上で麦わら帽子"},
        "notebook": {
            "scene": "park", "wearing": "cardigan", "atmosphere": "",
            "frame": "", "beat": "", "rev": 1,
        },
        "mode": "duet",
        "chat": [],
    }
    assert service._theme_for_models(session) == ""
    empty = {
        "inputs": {"theme": "屋上で麦わら帽子"},
        "notebook": notebook.blank(),
        "mode": "duet",
        "chat": [],
    }
    assert "屋上" in service._theme_for_models(empty)


def test_duet_transcript_is_user_turns_not_message_count():
    chat = []
    for i in range(8):
        chat.append({"role": "user", "text": f"指示{i}"})
        chat.append({"role": "muse", "text": f"SAY{i}", "kind": "craft"})
        chat.append({"role": "muse", "text": f"ASIDE{i}", "kind": "banter"})
    session = {"chat": chat, "mode": "duet"}
    text = service._duet_transcript(session, user_turns=4)
    assert "指示4" in text
    assert "指示0" not in text
    assert "ASIDE7" in text


def test_board_images_take_the_latest(monkeypatch):
    session = {
        "session_id": "s1",
        "board": {
            "pending": False,
            "round": 2,
            "images": [
                {"image_id": "old"},
                {"image_id": "new"},
            ],
        },
    }
    shots = [
        str(i.get("image_id") or "") for i in (session["board"]["images"] or [])
        if isinstance(i, dict) and i.get("image_id")
    ]
    assert shots[-1:] == ["new"]
    assert shots[:1] == ["old"]


def test_how_to_speak_still_is_base():
    session = {
        "inputs": {"theme": "屋上"},
        "chat": [{"role": "user", "text": "帽子外して"}],
        "mode": "duet",
        "notebook": {
            "rev": 1, "wearing": "cardigan", "scene": "rooftop",
        },
        "muse_card": "WEARING: thin cardigan",
    }
    prompt = service._duet_user_prompt(session, "帽子外して", prep=False)
    assert "previous take" in prompt.lower() or "底" in prompt
    assert "THEME" not in prompt
    assert "never a change-log" not in prompt.lower()


def test_weave_prompt_has_no_theme_or_transcript():
    import inspect
    src = inspect.getsource(service.weave_craft_if_needed)
    assert "mode=\"weave\"" in src or "mode='weave'" in src


@pytest.mark.asyncio
async def test_talk_then_compile_uses_card_not_tags():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "帽子外して": _scripter_block(
            intent="shot",
            wearing="thin cardigan",
            scene="rooftop at dusk",
            beat="sitting",
            frame="eye level",
            tags="thin_cardigan, straw_hat, lantern",
            craft_scene="Should be ignored on compile.",
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["notebook"]["wearing"] = "thin cardigan, straw hat"
    s["notebook"]["scene"] = "rooftop"
    s["notebook"]["rev"] = 1
    await service.post_duet_chat(db, ollama, s, "帽子外して")
    assert "hat" not in notebook.wearing_tokens(s["notebook"]["wearing"])
    assert not (s.get("craft") or {}).get("tags") or "straw_hat" not in (
        s.get("craft") or {}
    ).get("tags", "")
    struck = notebook.struck_tokens(s)
    assert "hat" in struck or "straw_hat" in struck


@pytest.mark.asyncio
async def test_weave_drops_struck_and_skips_theme():
    db = FakeDb()
    ollama = NotebookOllama(scripts={
        "WEAVE": _scripter_block(
            intent="shot",
            tags="thin_cardigan, straw_hat, lantern, knit, fabric_folds, dusk_sky",
            craft_scene="A thin cardigan in dusk air, fabric folds, long shadows. " * 8,
        ),
    })
    s = await _duet_session(db)
    s["mode"] = "duet"
    s["inputs"]["theme"] = "麦わら帽子と提灯の屋上"
    notebook.apply_patch(s["notebook"], {
        "wearing": "thin cardigan",
        "scene": "rooftop at dusk",
        "beat": "sitting on a bench",
        "frame": "eye level",
    })
    s["struck"] = ["hat", "straw_hat"]
    s["craft_dirty"] = True
    s["craft"] = {"prompt": "1girl, thin_cardigan", "tags": "thin_cardigan", "scene": "x"}
    await service.weave_craft_if_needed(db, ollama, s)
    tags = (s.get("craft") or {}).get("tags") or ""
    assert "straw_hat" not in tags.lower()
    notes = "\n".join(ollama.scripter_prompts)
    assert "THEME" not in notes or "麦わら" not in notes
    assert "CONVERSATION SO FAR" not in notes
