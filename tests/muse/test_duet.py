"""主演撮り (lead shoot) — one or two Muses with the Showrunner, no crew.

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

from app.muse import brief as brief_mod
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


class GarmentSwapOllama(FakeOllama):
    """Two prep turns: the first settles on pants, the second — after the
    Showrunner asks for a skirt — writes fresh TAGS that (realistically,
    imperfectly) still carry the old "pants" alongside the new "long_skirt",
    but a COSTUME/GARMENTS block that correctly names only the new one. The
    strike clerk is told to remove nothing, so only the structural GARMENTS
    diff (`strike_dropped_costume`, Bug 4 / Phase 2) can take "pants" back
    out of the craft.
    """

    def __init__(self):
        super().__init__()
        self.preps = 0

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")

        async def _stream(text):
            yield {"type": "token", "text": text}

        if "script supervisor's clerk" in system:
            # Only an explicit "パンツはやめて" (stop with the pants) reads as a
            # removal — "スカートにして" (make it a skirt) is left for the
            # structural GARMENTS diff to catch, on purpose (see the garment
            # swap test below).
            if "パンツはやめて" in str(prompt):
                return _stream("REMOVE: pants\nRESTORE: none")
            return _stream("REMOVE: none\nRESTORE: none")

        if "撮る画を一つに決めて" in str(prompt):
            self.preps += 1
            first = self.preps == 1
            tags = (
                "sitting, messy_room, white_shirt, pants" if first else
                "sitting, messy_room, white_shirt, pants, long_skirt"
            )
            bottom = "pants" if first else "long_skirt"
            text = (
                "SAY: わかりました、こんな感じです。\n\n"
                f"TAGS: {tags}\n\n"
                "SCENE: " + " ".join(["She sits in the small room"] * 30) + "\n\n"
                "COSTUME:\n"
                "SILHOUETTE: relaxed loungewear\n"
                "LAYERS: base + top\n"
                "COLOURWAY: white, navy\n"
                "PATTERN: solid\n"
                "FABRIC: cotton\n"
                "CONDITION: worn-in\n"
                "HERO: white_shirt\n"
                f"GARMENTS: top=white_shirt / bottom={bottom} / feet=none / extras=none"
            )
            return _stream(text)
        return _stream("SAY: えっと、そうですね。")


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
        db, ollama, session, "もっと散らかってる感じがいいな",
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

    session = await service.duet_prep_stage(db, ollama, session)

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
    session = await service.duet_prep_stage(db, ollama, session)

    session = await service.request_board(
        db, FakeComfy(), spooler, session, ollama=ollama,
    )

    assert len(spooler.jobs) == 1
    assert session["status"] == "boarding"


@pytest.mark.asyncio
async def test_typed_stage_words_never_auto_trigger_a_render():
    """Prep, test shot and approve are buttons now (`duet_prep_stage`,
    `request_board`, `approve_and_shoot`) — typed text, however phrased, is
    always conversation and never itself moves the shoot forward a stage."""
    db, ollama, spooler = FakeDb(), TalkingOllama(), FakeSpooler()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)

    session = await service.post_duet_chat(
        db, ollama, session, "試し撮りして。撮影準備もお願い。OK、本番でいこう。",
    )

    assert spooler.jobs == []
    assert session["craft"]["prompt"] == "", "text alone must never build a script"

    with pytest.raises(service.MuseError):
        await service.request_board(db, FakeComfy(), spooler, session, ollama=ollama)


@pytest.mark.asyncio
async def test_approval_sounding_chat_never_shoots_after_prep():
    """「本番」「決定」「撮って」read as approval nowhere anymore — only pressing
    the final button (`approve_and_shoot`) submits the render."""
    db, ollama, spooler = FakeDb(), TalkingOllama(), FakeSpooler()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)
    session = await service.duet_prep_stage(db, ollama, session)

    session = await service.post_duet_chat(
        db, ollama, session, "一枚撮ろう。本番でいこう。",
    )

    assert spooler.jobs == []
    assert session["status"] != "boarding"
    assert session["shoot"] == {}


@pytest.mark.asyncio
async def test_everything_she_is_told_stays_told():
    db, ollama = FakeDb(), TalkingOllama()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)

    session = await service.post_duet_chat(
        db, ollama, session, "冬にして。厚着で。",
    )
    assert "冬にして。厚着で。" in session["notes"]

    session = await service.duet_prep_stage(db, ollama, session)
    prompt = ollama.calls[-1]["prompt"]
    assert "冬にして。厚着で。" in prompt, "a note must outlive the turn answering it"


@pytest.mark.asyncio
async def test_a_carried_out_refusal_drops_out_of_the_next_preps_orders():
    """Bug 5: without `carried_out`/`removed_now` threaded through, every note
    ever spoken piled up in `orders_block` forever. Wiring `take_note` (Bug 4/6
    Phase 1) into duet fixes it the same way the crewed studio already works."""
    db, ollama = FakeDb(), GarmentSwapOllama()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)
    session = await service.duet_prep_stage(db, ollama, session)

    session = await service.post_duet_chat(db, ollama, session, "パンツはやめて")
    assert session.get("carried_out"), "the strike pass must record what it resolved"

    orders = brief_mod.orders_block(
        list(session.get("notes") or []),
        carried_out=list(session.get("carried_out") or []),
        removed_now=list(session.get("just_banned") or []),
        restored_now=list(session.get("just_restored") or []),
    )
    assert "パンツはやめて" not in orders, "a resolved note must not haunt the standing orders"


@pytest.mark.asyncio
async def test_costume_change_structurally_drops_the_old_garment():
    """Bug 4: "change pants to a skirt" must actually remove pants from the
    craft. The strike clerk here finds nothing to remove (a plausible LLM
    judgment call), so this only passes because of the structural GARMENTS
    diff (`strike_dropped_costume`) now wired into duet's prep turns."""
    db, ollama = FakeDb(), GarmentSwapOllama()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)

    session = await service.duet_prep_stage(db, ollama, session)
    assert "pants" in session["craft"]["tags"]

    session = await service.post_duet_chat(db, ollama, session, "スカートにして")
    session = await service.duet_prep_stage(db, ollama, session)

    assert "long_skirt" in session["craft"]["tags"]
    assert "pants" not in session["craft"]["tags"], (
        "the model's own TAGS line still had pants — only the GARMENTS-slot "
        "diff can be relied on to strike it"
    )


def test_prep_closing_instruction_reinforces_costume_and_props_too():
    """Bug 6: the reminder to reflect the Showrunner's latest instruction
    used to single out place/camera/pose and omit costume/props, which
    biased the rewrite toward keeping whatever outfit was improvised early."""
    prompt = service._duet_user_prompt({"inputs": {}, "chat": [], "notes": [],
                                        "craft": {}}, "スカートにして", prep=True)
    assert "衣装や小物を変えていたら" in prompt
    assert "パンツをスカートに直す" in prompt


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
    # And she clears the set herself when direction changes — newest wins.
    assert "WHEN THE SHOWRUNNER CHANGES ANYTHING" in text
    assert "newest words beat" in text.lower()
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
    assert "how each turn works" not in low
    assert "settled facts" not in low
    assert "newest line wins" in low
    assert "do not interview" in low
    assert "can get ready" in low


def test_talk_prompt_accepts_revisions_not_only_clothes():
    text = crew.actress_duet_prompt({"name_ja": "みお"}, mode="talk")
    low = text.lower()
    assert "drop the old choice" in low
    assert "refusing to change" in low
    assert "echo instruction headings" in low


def test_prep_prompt_overrides_sticky_previous_craft():
    text = crew.actress_duet_prompt({"name_ja": "みお"}, mode="prep")
    assert "変える必要のないところは変えない" not in text
    assert "board image" in text.lower() or "old take" in text.lower()
    assert "newest words beat" in text.lower()


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


def test_duet_talk_user_prompt_prefers_latest_over_sticky():
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
    assert "いちばん新しい発言が勝つ" in prompt
    assert "自分から具体案" in prompt
    assert "get ready" in prompt
    assert "撮る画を一つに決めて" not in prompt


def test_duet_prep_user_prompt_rewrites_against_previous_craft():
    session = {
        "inputs": {"theme": "放送室"},
        "chat": [],
        "notes": ["教室じゃなくて放送室", "後ろから"],
        "craft": {"prompt": "classroom, wooden_desk, covering_mouth"},
    }
    prompt = service._duet_user_prompt(session, "撮影準備", prep=True)
    assert "変える必要のないところは変えない" not in prompt
    assert "矛盾する" in prompt
    assert "TAGS/SCENE にも必ず反映" in prompt
    assert "classroom, wooden_desk" in prompt


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
