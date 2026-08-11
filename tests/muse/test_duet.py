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
from app.muse import crew, facets, service, session_db
from tests.muse.test_service import (  # noqa: E402
    FakeComfy, FakeDb, FakeOllama, FakeSpooler,
)


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


# A whole shot, in the parts the prep turn now writes.
_OPENING = """SAY: わかりました。机の上にコーラの缶、脱ぎっぱなしの上着。
PLACE TAGS: messy_room, indoors
PLACE: A small cluttered room, she is on the floor by the low table.
HOUR TAGS: night
HOUR: Late at night.
LIGHT TAGS: lamplight
LIGHT: One warm lamp in the corner.
PROPS TAGS: cola_can, thick_carpet, low_table, magazine
PROPS: A cola can on the table, a thick carpet, magazines everywhere.
COSTUME TAGS: white_shirt, pants
COSTUME: She wears a loose white shirt and pants.
POSE TAGS: sitting
POSE: She sits on the carpet with her weight back on one hand.
EXPRESSION TAGS: smile
EXPRESSION: A small tired smile.
CAMERA TAGS: from_front, upper_body
CAMERA: A level shot from the front, waist up.

COSTUME:
SILHOUETTE: relaxed loungewear
LAYERS: a single shirt
COLOURWAY: white, navy
PATTERN: solid
FABRIC: cotton
CONDITION: worn-in
HERO: the white shirt
GARMENTS: top=white_shirt / bottom=pants / feet=none / extras=none
"""


class TalkingOllama(FakeOllama):
    """Answers a conversation turn with a line, and a prep turn with a shot."""

    def __init__(self, routes=None):
        super().__init__()
        self.routes = routes or {}

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")
        text = "SAY: えっと……その場所なら、私はたぶん端っこに座っちゃうかも。"
        if "eight parts" in system:
            text = next(
                (v for k, v in self.routes.items() if k in str(prompt)),
                "FACETS: none\nSTANDING: none",
            )
        elif "YOU ARE THE WHOLE CREW TODAY" in system:
            text = _OPENING

        async def _stream():
            yield {"type": "token", "text": text}
        return _stream()


class GarmentSwapOllama(FakeOllama):
    """Two prep turns: the first settles on pants, the second — after the
    Showrunner asks for a skirt — rewrites the costume part.

    The interesting half is that the second turn still writes `pants` into its
    own COSTUME TAGS line, exactly as a real model does. It does not matter.
    The costume facet is replaced whole, and GARMENTS is the one garment
    authority, so the old bottom cannot ride along beside the new one.
    """

    def __init__(self):
        super().__init__()
        self.preps = 0

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        system = str(kw.get("system") or "")

        async def _stream(text):
            yield {"type": "token", "text": text}

        if "eight parts" in system:
            if "スカート" in str(prompt):
                return _stream("FACETS: costume\nCOSTUME: 下はスカート")
            return _stream("FACETS: none\nSTANDING: none")
        if "script supervisor's clerk" in system:
            if "パンツはやめて" in str(prompt):
                return _stream("REMOVE: pants\nRESTORE: none")
            return _stream("REMOVE: none\nRESTORE: none")

        if "YOU ARE THE WHOLE CREW TODAY" in system:
            self.preps += 1
            if self.preps == 1:
                return _stream(_OPENING)
            return _stream(
                "SAY: 下はスカートにしました。\n"
                # The model's own line still names the old garment. GARMENTS
                # below is what the outfit actually is.
                "COSTUME TAGS: white_shirt, pants, long_skirt\n"
                "COSTUME: She wears a white shirt and a long skirt.\n\n"
                "COSTUME:\n"
                "SILHOUETTE: relaxed loungewear\n"
                "LAYERS: shirt over a skirt\n"
                "COLOURWAY: white, navy\n"
                "PATTERN: solid\n"
                "FABRIC: cotton\n"
                "CONDITION: worn-in\n"
                "HERO: the long skirt\n"
                "GARMENTS: top=white_shirt / bottom=long_skirt / feet=none / extras=none"
            )
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

    # The shot exists in parts, and the craft is the view of it everything
    # downstream still reads.
    assert "messy_room" in facets.table_of(session)["place"]["tags"]
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
    """A note outlives the turn that answered it — as direction on the part it
    was about, not as its own words replayed forever."""
    db = FakeDb()
    ollama = TalkingOllama(routes={"冬": "FACETS: hour\nHOUR: 真冬の夜"})
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)
    session = await service.duet_prep_stage(db, ollama, session)

    session = await service.post_duet_chat(db, ollama, session, "冬にして。厚着で。")
    assert "冬にして。厚着で。" in session["notes"]
    assert session["directives"]["hour"]["text"] == "真冬の夜"

    session = await service.duet_prep_stage(db, ollama, session)
    prompt = ollama.calls[-1]["prompt"]
    assert "真冬の夜" in prompt, "direction must outlive the turn answering it"


@pytest.mark.asyncio
async def test_a_refusal_is_still_carried_out_and_still_stops_being_read():
    """A note that names no part of the shot is the other kind of instruction —
    a standing refusal — and the strike clerk still owns it. Once carried out
    its words drop out of what every turn re-reads, which is what stopped the
    crew discussing a thing that had been vetoed."""
    db, ollama = FakeDb(), GarmentSwapOllama()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)
    session = await service.duet_prep_stage(db, ollama, session)

    session = await service.post_duet_chat(db, ollama, session, "パンツはやめて")
    assert session["banned"] == ["pants"]
    assert session.get("carried_out"), "the strike pass must record what it resolved"
    assert "pants" not in session["craft"]["tags"]

    # A note that names no part rewrites nothing, so there is no prep turn to
    # inspect — the refusal was carried out as state. The next turn that DOES
    # write craft must not be handed the refused noun.
    session = await service.post_duet_chat(db, ollama, session, "スカートにして")
    session = await service.duet_prep_stage(db, ollama, session)
    prep = ollama.calls[-1]["prompt"]
    assert "パンツはやめて" not in prep
    assert "pants" not in prep
    # And it cannot be written back in, whatever the turn says.
    assert "pants" not in session["craft"]["tags"]


@pytest.mark.asyncio
async def test_a_costume_change_replaces_the_outfit_in_every_place_it_lives():
    """"Change the pants to a skirt" used to need a structural GARMENTS diff to
    take the pants back out, because the craft was one flat bag and the model's
    own tag line still named them.

    It still names them. It no longer matters: the costume facet is replaced
    whole, and GARMENTS is the one authority for what the outfit is. And the
    check the old version could not make — that the old garment is gone from the
    eight descriptive lines every later turn re-reads as LOCKED — is here too.
    """
    db, ollama = FakeDb(), GarmentSwapOllama()
    session = await _duet_session(db)
    session = await service.start_duet(db, ollama, session)

    session = await service.duet_prep_stage(db, ollama, session)
    assert "pants" in session["craft"]["tags"]

    session = await service.post_duet_chat(db, ollama, session, "スカートにして")
    session = await service.duet_prep_stage(db, ollama, session)

    assert "long_skirt" in session["craft"]["tags"]
    assert "pants" not in session["craft"]["tags"]
    assert "pants" not in brief_mod.costume_block(session["costume"])
    for value in facets.table_of(session)["costume"]["fields"].values():
        assert "pants" not in str(value).lower()
    # The room was not in the answer, so it cannot have been disturbed by it.
    assert "cola_can" in session["craft"]["tags"]


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
