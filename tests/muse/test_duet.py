"""二人芝居 — the Showrunner and the Lead, nobody else.

The crewed studio is a production meeting you watch. This is being in the room
with her, so the rules are different: talking is only talking, the script does
not exist until she is asked to get ready, and when it does exist she reads it
back out loud so there is something to react to.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import crew, service, session_db
from tests.muse.test_service import (  # noqa: E402
    FakeComfy, FakeDb, FakeOllama, FakeSpooler,
)


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


class TalkingOllama(FakeOllama):
    """Answers a conversation turn with a line and nothing else."""

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        crafty = "SAY: わかりました。机の上にコーラの缶、脱ぎっぱなしの上着。\n\n" + (
            "TAGS: sitting, messy_room, cola_can, discarded_jacket, thick_carpet\n\n"
            "SCENE: " + " ".join(["She sits in the small room"] * 30)
        )
        chat = "SAY: えっと……その場所なら、私はたぶん端っこに座っちゃうかも。"
        text = crafty if "撮る画を一つに決めて" in str(prompt) else chat

        async def _stream():
            yield {"type": "token", "text": text}
        return _stream()


async def _duet_session(db, **over):
    session = await service.create_session(db, {
        "theme": "深夜のカラオケで一人", "character_id": "c1",
        "workflow": "w.json", "model": "m", "mode": "duet", **over,
    })
    session["character"] = {"identity_tags": ["1girl", "silver_hair"],
                            "personality": {}, "palette": [], "signature_prop": ""}
    await session_db.save(db, session)
    return session


@pytest.mark.asyncio
async def test_talking_is_only_talking_and_writes_no_script():
    """The eighteen-seat table rewrites the craft every single turn. Here that
    would make conversation cost a full craft pass, and the point of the mode is
    that the two of you can just talk."""
    db, ollama = FakeDb(), TalkingOllama()
    session = await _duet_session(db)

    session = await service.start_duet(db, ollama, session)
    session = await service.post_duet_chat(
        db, ollama, FakeComfy(), FakeSpooler(), session, "もっと散らかってる感じがいいな",
    )

    assert session["craft"]["prompt"] == ""
    assert session["status"] == "chat"
    lines = [m for m in session["chat"] if m["role"] == "muse"]
    assert len(lines) == 2
    # Nobody but her.
    assert {m["muse_id"] for m in lines} == {crew.DEFAULT_MEMBER["actress"]}
    assert not any(m["role"] == "system" for m in session["chat"])


@pytest.mark.asyncio
async def test_getting_ready_is_when_the_script_appears():
    db, ollama = FakeDb(), TalkingOllama()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)

    session = await service.post_duet_chat(
        db, ollama, FakeComfy(), FakeSpooler(), session, "撮影準備して",
    )

    assert "messy_room" in session["craft"]["tags"]
    assert "silver_hair" in session["craft"]["prompt"]
    # She reads the frame back, so there is something to say「これ足して」to.
    assert "コーラの缶" in session["chat"][-1]["text"]
    assert session["chat"][-1]["muse_id"] == crew.DEFAULT_MEMBER["actress"]


@pytest.mark.asyncio
async def test_the_test_shot_renders_what_she_prepared():
    db, ollama, spooler = FakeDb(), TalkingOllama(), FakeSpooler()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)
    session = await service.post_duet_chat(
        db, ollama, FakeComfy(), spooler, session, "撮影準備",
    )

    session = await service.post_duet_chat(
        db, ollama, FakeComfy(), spooler, session, "じゃあ試し撮りして",
    )

    assert len(spooler.jobs) == 1
    assert session["status"] == "boarding"


@pytest.mark.asyncio
async def test_asking_for_a_test_shot_with_no_script_gets_ready_first():
    """Otherwise「試し撮り」on turn one renders an empty prompt."""
    db, ollama, spooler = FakeDb(), TalkingOllama(), FakeSpooler()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)

    session = await service.post_duet_chat(
        db, ollama, FakeComfy(), spooler, session, "試し撮りして",
    )

    assert spooler.jobs == []
    assert session["craft"]["prompt"], "she got ready instead"


@pytest.mark.asyncio
async def test_taking_the_picture_beats_reading_the_word_as_approval():
    """「撮って」is an OK in the crewed studio. In here it means take the shot,
    and going to the final render instead would skip the whole loop."""
    db, ollama, spooler = FakeDb(), TalkingOllama(), FakeSpooler()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)
    session = await service.post_duet_chat(
        db, ollama, FakeComfy(), spooler, session, "撮影準備",
    )

    session = await service.post_duet_chat(
        db, ollama, FakeComfy(), spooler, session, "一枚撮ろう",
    )

    assert session["status"] == "boarding"
    assert session["shoot"] == {}


@pytest.mark.asyncio
async def test_everything_she_is_told_stays_told():
    db, ollama = FakeDb(), TalkingOllama()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)

    session = await service.post_duet_chat(
        db, ollama, FakeComfy(), FakeSpooler(), session, "冬にして。厚着で。",
    )
    assert "冬にして。厚着で。" in session["notes"]

    session = await service.post_duet_chat(
        db, ollama, FakeComfy(), FakeSpooler(), session, "撮影準備",
    )
    prompt = ollama.calls[-1]["prompt"]
    assert "冬にして。厚着で。" in prompt, "a note must outlive the turn answering it"


@pytest.mark.asyncio
async def test_a_duet_session_never_falls_through_to_the_crewed_studio():
    db, ollama, spooler = FakeDb(), TalkingOllama(), FakeSpooler()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "そこ、もう少し明るくできる？",
    )

    assert session["craft"]["prompt"] == "", "no crew wrote anything"
    assert {m["muse_id"] for m in session["chat"] if m["role"] == "muse"} == {
        crew.DEFAULT_MEMBER["actress"],
    }


def test_she_is_told_she_is_the_whole_crew_when_getting_ready():
    text = crew.actress_duet_prompt({"name_ja": "みお"}, mode="prep")
    assert "TEN OR MORE OBJECTS" in text
    assert "ONE CAMERA" in text
    assert "no planner, no camera, no wardrobe, no lighting" in text
    # And she clears the set herself, because nobody else will.
    assert "WHEN THE SHOWRUNNER CHANGES THE SCENE" in text
    assert "NAME THE THINGS that are in" in text


def test_the_talking_prompt_writes_nothing_down():
    text = crew.actress_duet_prompt({"name_ja": "みお"}, mode="talk")
    assert "Nothing is being written down" in text
    assert "TAGS or SCENE" in text
    assert "TEN OR MORE OBJECTS" not in text


def test_talk_prompt_is_not_an_interview_bot():
    """Ask things / get-ready-whenever used to make her a prep-checklist assistant."""
    text = crew.actress_duet_prompt({"name_ja": "みお"}, mode="talk")
    low = text.lower()
    assert "ask things" not in low
    assert "whenever they want" not in low
    assert "SETTLED FACTS" in text
    assert "FORBIDDEN" in text
    assert "Do not interview" in text or "do not interview" in text.lower()
    # Still names the banned phrases so the model knows what to avoid.
    assert "can get ready" in low


def test_talk_prompt_injects_character_voice():
    character = {
        "name_ja": "各務 みお",
        "name": "Mio Kagami",
        "first_person_ja": "私",
        "user_address_ja": "総監督さん",
        "talk_quirks": "マイク前では通る声。オフだと小声。",
        "duet_say_examples": [
            "放送室でマイクに向かってるところがいいです。",
            "ヘッドホンは片耳だけ外しておきます。",
        ],
        "personality": {},
    }
    text = crew.actress_duet_prompt(character, mode="talk")
    assert "総監督さん" in text
    assert "マイク前では通る声" in text
    assert "放送室でマイク" in text
    assert "VOICE" in text


def test_duet_talk_user_prompt_prefers_proposals_over_reasking():
    session = {
        "inputs": {"theme": "放送室"},
        "chat": [
            {"role": "user", "text": "放送室に行こう"},
            {"role": "muse", "text": "わかりました"},
        ],
        "notes": ["放送室に行こう"],
        "craft": {},
    }
    prompt = service._duet_user_prompt(session, "マイク前で椅子に座って", prep=False)
    assert "決まった事実" in prompt
    assert "自分から具体案" in prompt
    assert "get ready" in prompt
    assert "撮る画を一つに決めて" not in prompt


@pytest.mark.asyncio
async def test_the_mode_can_be_set_before_anything_opens():
    """The panel has to know which door to use, and whether to show a casting
    drawer at all, before the session starts."""
    db = FakeDb()
    session = await service.create_session(db, {"theme": "t", "model": "m"})
    assert session["mode"] == ""

    session = await service.patch_inputs(db, session, {"mode": "duet"})
    assert session["mode"] == "duet"
    assert service.is_duet(session)

    session = await service.patch_inputs(db, session, {"theme": "別のお題"})
    assert session["mode"] == "duet", "an unrelated patch must not clear it"

    session = await service.patch_inputs(db, session, {"mode": ""})
    assert not service.is_duet(session)


def test_w_actress_duet_prompt():
    char_a = {"name_ja": "みなも", "personality": {"first_person_ja": "私", "user_address_ja": "総監督"}}
    char_b = {"name_ja": "かほ", "personality": {"first_person_ja": "私", "user_address_ja": "総監督"}}
    text = crew.w_actress_duet_prompt(char_a, char_b, mode="talk")
    assert "W-MUSE" in text
    assert "みなも" in text
    assert "かほ" in text
    assert "2girls" in crew.w_actress_duet_prompt(char_a, char_b, mode="prep")
