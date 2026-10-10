"""Still -> look -> one idea per kind -> H3 prompt. Stub model, no session write."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.muse import video_expand as ve

MIO = {
    "name": "Mio Kagami",
    "identity_tags": ["silver_hair", "short_hair", "hair_behind_ear", "blue_eyes"],
    "signature_prop": "headphones",
    "personality": {
        "occupation": "late-night radio host",
        "charm": "Recognise her by her voice and she goes rigid.",
        "likes": ["rainy nights"],
        "appearance": {"hair": "a blunt bob she cut herself", "habit": "presses one headphone cup"},
    },
}
RIN = {"name": "Rin Ogata", "identity_tags": ["red_hair", "red_eyes"]}


class _FakeOllama:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[dict] = []

    async def _reply(self, prompt, images, kwargs):
        self.calls.append({"prompt": prompt, "images": images, **kwargs})
        text = self.replies.pop(0)
        if text:
            yield {"type": "token", "text": text}

    def generate_vlm_stream(self, prompt, images, **kwargs):
        return self._reply(prompt, images, kwargs)

    def generate_text_stream(self, prompt, **kwargs):
        return self._reply(prompt, None, kwargs)


def _idea(title: str) -> str:
    return json.dumps({"title": title, "story": "s", "beats": ["She looks up."]})


def test_only_finished_stills_are_eligible():
    session = {
        "board": {"images": [{"sha256": "board"}]},
        "shoot": {"images": [{"image_id": "final"}, "loose"]},
    }
    assert ve.shoot_shas(session) == ["final", "loose"]


def test_cast_reads_hair_and_eyes_from_tags_and_keeps_the_partner():
    people = ve.cast({"character": MIO, "partner_character": RIN})
    assert [p["name"] for p in people] == ["Mio Kagami", "Rin Ogata"]
    # The appearance prose is character writing, not a hair colour.
    # Colour only: a length tag can disagree with the still.
    assert people[0]["hair"] == "silver hair"
    assert people[0]["eyes"] == "blue eyes"
    assert people[1]["hair"] == "red hair"
    assert ve.cast({"character": {}, "partner_character": {}}) == []


@pytest.mark.asyncio
async def test_look_falls_back_to_the_shoot_prompt_when_blind():
    # Empty for the picture, empty again without it.
    ollama = _FakeOllama(["", ""])
    text, blind = await ve.look(
        ollama, image=b"png", people=ve.cast({"character": MIO}), model="m",
        num_ctx=None, fallback="Mio at the microphone.",
    )
    assert (text, blind) == ("Mio at the microphone.", True)


@pytest.mark.asyncio
async def test_each_idea_call_sees_only_its_own_kind():
    ollama = _FakeOllama([_idea(k) for k in ve.KINDS])
    out = await ve.ideas(
        ollama, still="A booth.", people=ve.cast({"character": MIO}),
        short="night city", duration_s=5, locale="ja", model="m", num_ctx=None,
    )
    assert [i["kind"] for i in out] == list(ve.KINDS)
    for call, kind in zip(ollama.calls, ve.KINDS):
        assert ve.KINDS[kind]["brief"] in call["system"]
        others = [k for k in ve.KINDS if k != kind]
        assert not any(ve.KINDS[k]["brief"] in call["system"] for k in others)
        assert "night city" in call["prompt"]
        assert call["think"] is False


@pytest.mark.asyncio
async def test_every_idea_is_grounded_but_gets_a_different_hook_and_never_the_charm():
    ollama = _FakeOllama([_idea(k) for k in ve.KINDS])
    await ve.ideas(
        ollama, still="A booth.", people=ve.cast({"character": MIO}),
        short="", duration_s=5, locale="ja", model="m", num_ctx=None,
        rng=random.Random(1),
    )
    hooks = []
    for call in ollama.calls:
        # Without her occupation the world goes wrong (a darkroom read as a bakery).
        assert "works as: late-night radio host" in call["prompt"]
        # The charm line takes over whichever idea gets it.
        assert "goes rigid" not in call["prompt"]
        seen = [h for h, label in ve.HOOKS.items() if f"{label}:" in call["prompt"]]
        assert len(seen) <= 1
        hooks += seen
    assert len(hooks) == len(set(hooks))


def test_hooks_are_drawn_afresh():
    draws = {tuple(ve.draw_hooks(tuple(ve.KINDS), random.Random(i)).values()) for i in range(20)}
    assert len(draws) > 1


@pytest.mark.asyncio
async def test_a_failed_idea_is_reported_not_dropped():
    ollama = _FakeOllama(["not json", _idea("b"), _idea("c"), _idea("d")])
    out = await ve.ideas(
        ollama, still="A booth.", people=ve.cast({"character": MIO}),
        short="", duration_s=5, locale="ja", model="m", num_ctx=None,
    )
    assert len(out) == len(ve.KINDS)
    assert out[0]["error"] and "beats" not in out[0]
    assert out[1]["title"] == "b"
    assert "left no note" in ollama.calls[0]["prompt"]


def test_layout_writes_the_fixed_lines_in_code():
    text = ve.layout({
        "shots": [
            {"at": 0, "text": "Mio stands at the mic."},
            {"at": 2.5, "text": "The camera cuts to a night street."},
            {"at": 99, "text": "a rooftop."},
        ],
        "sound": "click, then steps",
        "music": "",
    }, duration_s=5)
    assert text.startswith(ve.H3_OPENING + "\n\nintegrated_multimodal_description: [Shot 1] Mio")
    assert "[Shot 2] At 00:02.500, the camera cuts to a night street." in text
    assert "the camera cuts to the camera cuts to" not in text.lower()
    # A time past the end is pulled back inside the clip, after shot 2.
    assert "[Shot 3] At 00:03.750, the camera cuts to a rooftop." in text
    assert text.endswith("overall_soundscape: click, then steps\nnon_diegetic_music: none")


def test_a_repeated_shot_and_nested_sound_are_folded():
    text = ve.layout({"shots": [
        {"at": 0, "text": "She turns."},
        {"at": 0, "text": "She turns.", "sound": "a click", "music": "cello, slow, rising"},
    ]}, duration_s=5)
    assert "[Shot 2]" not in text
    assert "overall_soundscape: a click" in text
    assert "non_diegetic_music: cello, slow, rising" in text


def test_a_cut_inside_a_paragraph_becomes_its_own_shot():
    text = ve.layout({"shots": [
        {"at": 0, "text": "The camera pans to the booth. The camera cuts to a close-up of Mio."},
    ]}, duration_s=5)
    assert "[Shot 1] The camera pans to the booth. [Shot 2] At 00:02.500, the camera cuts to a close-up of Mio." in text


def test_a_dangling_cut_at_the_end_of_a_shot_is_dropped():
    text = ve.layout({"shots": [
        {"at": 0, "text": "Steam fills the frame. the camera cuts to"},
        {"at": 3, "text": "a bakery oven."},
    ]}, duration_s=5)
    assert "[Shot 1] Steam fills the frame. [Shot 2] At 00:03.000, the camera cuts to a bakery oven." in text


def test_one_continuous_take_is_one_shot():
    text = ve.layout({"shots": [{"at": 0, "text": "She turns."}]}, duration_s=10)
    assert "[Shot 2]" not in text


def test_labelled_lines_parse_and_wrap():
    raw = (
        "SHOT 1 AT 0: Mio stands at the mic.\n"
        "She smiles.\n"
        "**SHOT 2 AT 2.5s:** a night street.\n"
        "SOUND: a click, then steps\n"
        "MUSIC: theremin, slow, rising"
    )
    data = ve.parse_shots(raw)
    assert data["shots"] == [
        {"at": "0", "text": "Mio stands at the mic. She smiles."},
        {"at": "2.5", "text": "a night street."},
    ]
    assert data["sound"] == "a click, then steps"
    assert data["music"] == "theremin, slow, rising"


def test_a_shot_is_laid_out_from_its_five_parts():
    raw = (
        "SHOT 1 AT 0: Mio stands at the microphone in a dim booth\n"
        "LOOK: Mio Kagami, long silver hair falling past her shoulders, blue eyes, a cream knit cardigan\n"
        "FACE: her eyes lift to the window, a small breath escapes\n"
        "BODY: she slowly lets go of the microphone\n"
        "LIGHT: grey rain light from the left window\n"
        "CAMERA: static, small, slow, holding on her stillness\n"
        "SOUND: rain on glass\nMUSIC: theremin, slow, rising"
    )
    text = ve.layout(ve.parse_shots(raw), duration_s=5)
    shot = text.split("[Shot 1] ")[1].split("\n")[0]
    order = ["dim booth.", "cardigan.", "microphone.", "escapes.", "window.", "stillness."]
    assert [shot.index(w) for w in order] == sorted(shot.index(w) for w in order)


def test_a_shot_header_written_mid_line_starts_a_shot():
    raw = (
        "SHOT 1 AT 0: A girl sits on a bed. BODY: she pans toward the clock. SHOT 2 AT 2: a close-up of the clock\n"
        "BODY: its hands frozen\nSOUND: ticking\nMUSIC: metronome, steady"
    )
    data = ve.parse_shots(raw)
    assert [s["at"] for s in data["shots"]] == ["0", "2"]
    assert data["shots"][0]["body"] == "she pans toward the clock."
    assert "[Shot 2] At 00:02.000, the camera cuts to a close-up of the clock. its hands frozen." in ve.layout(data, duration_s=5)


@pytest.mark.parametrize("raw", [
    "**SHOT 1 AT 0:** a bed\nBODY: she wakes\n**SHOT 2** AT 2: a window\nBODY: rain\nSOUND: s\nMUSIC: m",
    "### SHOT 1 AT 0: a bed\n- BODY: she wakes\n### SHOT 2 (AT 2s): a window\n- BODY: rain\nSOUND: s\nMUSIC: m",
    "SHOT 1 AT 0:\na bed\nBODY: she wakes\nSHOT 2 AT 2：a window\nBODY: rain\nSOUND: s\nMUSIC: m",
])
def test_shot_labels_survive_markdown_and_drift(raw):
    data = ve.parse_shots(raw)
    assert [s["at"] for s in data["shots"]] == ["0", "2"]
    text = ve.layout(data, duration_s=5)
    assert "[Shot 1] a bed. she wakes." in text
    assert "[Shot 2] At 00:02.000, the camera cuts to a window. rain." in text


def test_parts_without_a_shot_line_are_shot_one():
    data = ve.parse_shots("LOOK: silver hair\nBODY: she turns\nSOUND: s\nMUSIC: m")
    assert data["shots"] == [{"at": "0", "text": "", "look": "silver hair", "body": "she turns"}]
    assert "[Shot 1] silver hair. she turns." in ve.layout(data, duration_s=5)


@pytest.mark.asyncio
async def test_a_failed_write_keeps_what_the_model_said(studio):
    talk = _talking()
    ollama = _FakeOllama(["Theremin", "no labels at all", "still nothing"])
    with pytest.raises(vt.VideoTalkError):
        await vt.write(_FakeDb(), ollama, talk, "shift")
    assert talk["last_write_failure"]["raw"] == "still nothing"
    assert talk["talk_id"] in studio  # saved, so it can be read afterwards


@pytest.mark.asyncio
async def test_a_failed_write_in_the_talk_keeps_her_reply(studio):
    talk = _talking()
    ollama = _FakeOllama(["SAY: いきましょう\nPICK: shift\nWRITE: yes", "Theremin", "x", "y"])
    await vt.chat(_FakeDb(), ollama, talk, "これでいこう")
    kinds = [(r["role"], r.get("kind")) for r in talk["chat"]]
    assert kinds == [("user", None), ("assistant", None), ("system", "write_failed")]


def test_the_writer_hears_what_was_agreed_in_their_words():
    prompt = ve.write_prompt(
        still="A bed.", people=[], idea={"kind": "stay", "beats": ["She wakes."], "story": "寝起きのぼーっとした可愛らしさ"},
        duration_s=5,
    )
    assert "寝起きのぼーっとした可愛らしさ" in prompt
    assert "about 440 English words" in prompt
    # A longer clip is never asked for less.
    words = [ve._words(sec) for sec in ve.DURATIONS]
    assert words == sorted(words)


def test_instrument_candidates_are_cleaned():
    assert ve.parse_instruments("1. Theremin\n- **Upright bass**\n3) Glockenspiel\ntheremin") == [
        "Theremin", "Upright bass", "Glockenspiel",
    ]


@pytest.mark.asyncio
async def test_write_picks_the_instrument_first_and_retries_once():
    good = "SHOT 1 AT 0: She turns.\nSOUND: s\nMUSIC: theremin, slow, rising"
    ollama = _FakeOllama(["Theremin\nGlockenspiel\nUpright bass", "nope", good])
    out = await ve.write(
        ollama, still="A booth.", people=ve.cast({"character": MIO}),
        idea={"kind": "stay", "title": "t", "beats": ["She turns."]}, duration_s=5,
        model="m", num_ctx=None, rng=random.Random(0),
    )
    assert "[Shot 1] She turns." in out["prompt"]
    assert out["instrument"] in {"Theremin", "Glockenspiel", "Upright bass"}
    # The instrument call knows her work; the writer is told what to play.
    assert "works as: late-night radio host" in ollama.calls[0]["prompt"]
    assert f"The music is played on: {out['instrument']}" in ollama.calls[1]["prompt"]
    assert "could not be used" in ollama.calls[2]["prompt"]

    with pytest.raises(ve.VideoError):
        await ve.write(
            _FakeOllama(["", "nope", "still nope"]), still="A booth.",
            people=ve.cast({"character": MIO}), idea={"beats": ["x"]},
            duration_s=5, model="m", num_ctx=None,
        )


# ── Video studio (video_talk) ────────────────────────────────────────────────

from backend.app.muse import video_talk as vt

MIO_ID = "c007"
RIN_ID = "c006"
CHARS = {
    MIO_ID: {**MIO, "name_ja": "各務 みお"},
    RIN_ID: {**RIN, "name_ja": "緒方 りん"},
}
CFG = {"muse_model": "m", "ollama_num_ctx": 16384}


class _FakeDb:
    """Image records by sha; Muse sessions by id."""

    def __init__(self, meta: dict | None = None, sessions: dict | None = None):
        self.meta = meta or {}
        self.sessions = sessions or {}

    async def get_by_sha256s(self, shas):
        return [{"sha256": shas[0], **self.meta}]


@pytest.fixture
def studio(monkeypatch):
    saved = {}

    async def save(db, talk):
        saved[talk["talk_id"]] = talk
        return talk

    async def load_session(db, sid):
        return db.sessions.get(sid)

    async def images(db, shas):
        return [b"png"]

    async def character(db, cid):
        if cid not in CHARS:
            raise KeyError(cid)
        return {**CHARS[cid], "character_id": cid, "board": {"portrait": f"face-{cid}"}}

    async def load_memory(db, talk):
        talk.setdefault("memories", [])

    monkeypatch.setattr(vt.video_db, "save", save)
    monkeypatch.setattr(vt.session_db, "load", load_session)
    monkeypatch.setattr(vt, "images_by_sha", images)
    monkeypatch.setattr(vt, "_character", character)
    monkeypatch.setattr(vt.persona, "load_memory", load_memory)
    return saved


def _pitch_replies(opener="SAY: 総監督さん、4つ考えました……かな"):
    return ["A booth.", *[_idea(k) for k in ve.KINDS], opener]


def test_reply_lines_parse():
    raw = "SAY: A: それ、いいかも\nB: えー、雨の方がいいって\nPICK: Shift\nBEATS: She looks up / Rain starts\nWRITE: no"
    r = vt.parse_reply(raw)
    assert r["say"] == "A: それ、いいかも\nB: えー、雨の方がいいって"
    assert r["pick"] == "shift" and r["write"] == ""
    assert r["beats"] == "She looks up / Rain starts"
    assert vt.parse_reply("ただの返事")["say"] == "ただの返事"


@pytest.mark.asyncio
async def test_a_still_from_her_shoot_carries_on_from_it(studio):
    shoot = {
        "session_id": "s1", "character": {**MIO, "character_id": MIO_ID},
        "inputs": {"theme": "radio booth at night"},
    }
    db = _FakeDb({"muse_session_id": "s1"}, {"s1": shoot})
    ollama = _FakeOllama(_pitch_replies())
    talk = await vt.create(db, ollama, sha="final", character_id=MIO_ID, cfg=CFG)
    assert talk["source_session_id"] == "s1"
    assert "your own shoot" in ollama.calls[-1]["system"]
    assert "radio booth at night" in ollama.calls[1]["prompt"]  # the shoot's context
    assert "radio booth at night" in ollama.calls[-1]["prompt"]
    assert talk["chat"][-1]["name"] == "各務 みお"
    assert talk["talk_id"] in studio


@pytest.mark.asyncio
async def test_another_actress_on_her_shoot_does_not_claim_it(studio):
    shoot = {"session_id": "s1", "character": {**MIO, "character_id": MIO_ID}, "inputs": {}}
    db = _FakeDb({"muse_session_id": "s1"}, {"s1": shoot})
    ollama = _FakeOllama(_pitch_replies("SAY: みおさんの撮影ですね"))
    await vt.create(db, ollama, sha="final", character_id=RIN_ID, cfg=CFG)
    assert "not with you" in ollama.calls[-1]["system"]
    assert "Mio Kagami" in ollama.calls[-1]["system"]


@pytest.mark.asyncio
async def test_a_photo_from_elsewhere_names_nobody_and_is_not_her(studio):
    ollama = _FakeOllama(_pitch_replies("SAY: へえ"))
    talk = await vt.create(_FakeDb({}), ollama, sha="pier", character_id=MIO_ID, cfg=CFG)
    assert talk["people"] == [] and talk["source_session_id"] == ""
    assert "not you" in ollama.calls[-1]["system"]
    assert "Not known by name" in ollama.calls[1]["prompt"]
    assert "works as" not in ollama.calls[1]["prompt"]


@pytest.mark.asyncio
async def test_a_second_actress_reuses_the_reading(studio):
    first = await vt.create(_FakeDb({}), _FakeOllama(_pitch_replies()), sha="pier", character_id=MIO_ID, cfg=CFG)
    ollama = _FakeOllama([*[_idea(k) for k in ve.KINDS], "SAY: A: へえ\nB: いいじゃん"])
    talk = await vt.create(
        _FakeDb({}), ollama, sha="pier", character_id=MIO_ID, partner_id=RIN_ID,
        cfg=CFG, reuse=first,
    )
    assert not any(c["images"] for c in ollama.calls)  # the picture was not read again
    assert [r["name"] for r in talk["chat"]] == ["各務 みお", "緒方 りん"]


@pytest.mark.asyncio
async def test_no_model_says_so(studio):
    with pytest.raises(vt.VideoTalkError):
        await vt.create(_FakeDb({}), _FakeOllama([]), sha="x", character_id=MIO_ID, cfg={})


def _talking():
    return {
        "talk_id": "t1", "inputs": {"locale": "ja", "model": "m", "num_ctx": None},
        "character": {**MIO, "character_id": MIO_ID, "name_ja": "各務 みお"},
        "partner_character": {}, "still": "A booth.", "people": [], "duration_s": 5,
        "ideas": [{"kind": "shift", "label": "x", "title": "t", "story": "s", "beats": ["a"]}],
        "chosen": "shift", "chat": [], "prompts": [],
    }


@pytest.mark.asyncio
async def test_a_changed_idea_is_shown_before_it_is_written(studio):
    talk = _talking()
    ollama = _FakeOllama([
        "SAY: じゃあ雨で！\nPICK: shift\nSTORY: 雨が降り出す\nBEATS: She looks up / Rain falls\nWRITE: yes",
    ])
    await vt.chat(_FakeDb(), ollama, talk, "雨にして")
    idea = talk["ideas"][0]
    assert idea["beats"] == ["She looks up", "Rain falls"] and idea["revised"] == 1
    assert talk["prompts"] == []
    assert talk["chat"][-1]["kind"] == "revised"


@pytest.mark.asyncio
async def test_switching_to_another_idea_is_not_going_ahead(studio):
    talk = _talking()
    talk["ideas"].append({"kind": "cut", "label": "y", "title": "u", "story": "s", "beats": ["b"]})
    await vt.chat(_FakeDb(), _FakeOllama(["SAY: じゃあ別の場所へ！\nPICK: cut\nWRITE: yes"]), talk, "もっと動き")
    assert talk["chosen"] == "cut" and talk["prompts"] == []


@pytest.mark.asyncio
async def test_she_goes_ahead_when_told(studio):
    talk = _talking()
    ollama = _FakeOllama([
        "SAY: いきましょう\nPICK: shift\nWRITE: yes",
        "Theremin",
        "SHOT 1 AT 0: Mio looks up as rain falls.\nSOUND: rain\nMUSIC: theremin, slow, rising",
    ])
    await vt.chat(_FakeDb(), ollama, talk, "これでいこう")
    assert talk["prompts"][-1]["prompt"].startswith(ve.H3_OPENING)
    assert talk["chat"][-1]["kind"] == "written"


@pytest.mark.asyncio
async def test_duet_lines_without_a_say_label_still_reach_the_chat(studio):
    talk = _talking()
    talk["partner_character"] = {**RIN, "character_id": RIN_ID, "name_ja": "緒方 りん"}
    ollama = _FakeOllama([
        "A: (各務 みお) [shift]みたいに雨、どうかな<br>B:(緒方 りん) えー、走りたい！\nPICK: shift",
    ])
    await vt.chat(_FakeDb(), ollama, talk, "どれがいい？")
    said = [(r["name"], r["text"]) for r in talk["chat"] if r["role"] == "assistant"]
    assert said == [("各務 みお", "「t」みたいに雨、どうかな"), ("緒方 りん", "えー、走りたい！")]


@pytest.mark.asyncio
async def test_a_trailing_slash_is_not_part_of_her_line(studio):
    talk = _talking()
    await vt.chat(_FakeDb(), _FakeOllama(["SAY: すごく好みです。 /\nねえ、どうする？"]), talk, "どう？")
    said = [r["text"] for r in talk["chat"] if r["role"] == "assistant"]
    assert said == ["すごく好みです。\nねえ、どうする？"]


@pytest.mark.asyncio
async def test_her_diary_is_not_brought_into_the_studio(studio):
    talk = _talking()
    talk.update({
        "caught": {"ids": ["d1"], "summary": "木漏れ日の道を歩いた日"},
        "diary_memories": ["木漏れ日の道で、総監督に見られた気がした。"],
        "memories": ["前回の撮影：雨の路地"],
        "bond": {"distance": "少し打ち解けてきた"},
    })
    ollama = _FakeOllama(["SAY: 可愛らしく、ですね"])
    await vt.chat(_FakeDb(), ollama, talk, "どんな感じがいいかな")
    system = ollama.calls[0]["system"]
    assert "木漏れ日" not in system and "雨の路地" not in system
    assert "少し打ち解けてきた" in system


def test_instrument_lines_are_short():
    assert ve.parse_instruments("Theremin\nA girl with dark hair, wide eyes, and an apron.") == ["Theremin"]


def test_the_screen_view_carries_faces_and_cards():
    talk = _talking()
    talk["character"]["board"] = {"portrait": "face"}
    v = vt.view(talk)
    assert v["character"]["portrait"] == "face"
    assert v["ideas"][0]["title"] == "t" and "beats" not in v["ideas"][0]


# ── HTTP wiring ──────────────────────────────────────────────────────────────

def _client(monkeypatch, studio, replies, meta=None, sessions=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.muse import video_api

    async def load(db, talk_id):
        return studio.get(talk_id)

    async def cfg(request):
        return CFG

    monkeypatch.setattr(video_api.video_db, "load", load)
    monkeypatch.setattr(video_api, "_cfg", cfg)
    app = FastAPI()
    app.include_router(video_api.router)
    app.state.db = _FakeDb(meta, sessions)
    app.state.ollama = _FakeOllama(replies)
    return TestClient(app), app.state.ollama


def test_create_chat_write_repitch_over_http(monkeypatch, studio):
    client, ollama = _client(monkeypatch, studio, _pitch_replies("SAY: Four ideas!"))
    r = client.post("/api/muse/video/talks", json={"sha256": "pier", "character_id": MIO_ID, "locale": "en"})
    assert r.status_code == 200, r.text
    tid = r.json()["talk_id"]
    assert [i["kind"] for i in r.json()["ideas"]] == list(ve.KINDS)

    ollama.replies = ["SAY: The moment goes on, then.\nPICK: stay"]
    r = client.post(f"/api/muse/video/talks/{tid}/chat", json={"message": "the first one", "duration_s": 10})
    assert r.status_code == 200 and r.json()["chosen"] == "stay" and r.json()["duration_s"] == 10

    ollama.replies = ["Theremin", "SHOT 1 AT 0: Mio turns.\nSOUND: s\nMUSIC: theremin, slow, rising"]
    r = client.post(f"/api/muse/video/talks/{tid}/write", json={"kind": "stay"})
    assert r.status_code == 200, r.text
    assert r.json()["prompts"][-1]["prompt"].startswith(ve.H3_OPENING)

    ollama.replies = [*[_idea(k) for k in ve.KINDS], "SAY: New ones!"]
    r = client.post(f"/api/muse/video/talks/{tid}/repitch")
    assert r.status_code == 200 and r.json()["chosen"] == ""


def test_unknown_actress_is_a_400(monkeypatch, studio):
    client, _ = _client(monkeypatch, studio, [])
    r = client.post("/api/muse/video/talks", json={"sha256": "x", "character_id": "nobody"})
    assert r.status_code == 400


def test_source_lookup_finds_the_shoot(monkeypatch, studio):
    client, _ = _client(
        monkeypatch, studio, [], meta={"muse_session_id": "s9", "character_id": MIO_ID},
        sessions={"s9": {"session_id": "s9"}},
    )
    async def last(db):
        return "big:26b"

    from backend.app.muse import video_api
    monkeypatch.setattr(video_api, "_last_muse_model", last)
    r = client.get("/api/muse/video/source/abc")
    assert r.json() == {
        "session_id": "s9", "character_id": MIO_ID, "partner_character_id": "",
        "session_model": "", "last_muse_model": "big:26b",
    }


@pytest.mark.asyncio
async def test_the_chosen_model_reads_the_picture_and_talks(studio):
    ollama = _FakeOllama(_pitch_replies())
    talk = await vt.create(_FakeDb({}), ollama, sha="x", character_id=MIO_ID, cfg=CFG, model="big:26b")
    assert talk["inputs"]["model"] == "big:26b"
    assert {c["model"] for c in ollama.calls} == {"big:26b"}
    assert vt.view(talk)["model"] == "big:26b"


# ── Making room on the card before a pitch ───────────────────────────────────

def _comfy(handler):
    import httpx

    from backend.app.ai.comfy import ComfyUIClient

    c = ComfyUIClient()
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_comfy_is_left_alone_while_it_renders():
    seen = []

    def handler(request):
        import httpx
        seen.append(request.url.path)
        return httpx.Response(200, json={"queue_running": [["x"]], "queue_pending": []})

    assert await _comfy(handler).free_vram(wait_s=0.1) is False
    assert "/free" not in seen


@pytest.mark.asyncio
async def test_comfy_frees_and_waits_until_the_memory_moves(monkeypatch):
    import asyncio as aio

    import httpx

    async def no_sleep(_):
        return None

    monkeypatch.setattr(aio, "sleep", no_sleep)
    state = {"free": 1 << 30, "freed": False}

    def handler(request):
        path = request.url.path
        if path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if path == "/free":
            state["freed"] = True
            return httpx.Response(200)
        if state["freed"]:
            state["free"] = 14 << 30  # takes effect on the next look
        return httpx.Response(200, json={"devices": [{"vram_free": state["free"]}]})

    assert await _comfy(handler).free_vram(wait_s=5) is True


class _Room:
    def __init__(self, loaded):
        self._loaded = loaded
        self.freed = 0

    async def loaded(self):
        return self._loaded

    async def free_vram(self):
        self.freed += 1
        return True


@pytest.mark.asyncio
async def test_room_is_made_only_when_the_model_is_not_on_the_gpu():
    from types import SimpleNamespace

    from backend.app.muse import video_api

    def request(loaded):
        room = _Room(loaded)
        return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(comfy=room, ollama=room))), room

    req, room = request([{"name": "m", "size": 100, "size_vram": 95}])
    await video_api.make_room(req, "m")
    assert room.freed == 0

    req, room = request([{"name": "m", "size": 100, "size_vram": 2}])
    await video_api.make_room(req, "m")
    assert room.freed == 1

    req, room = request([])  # not loaded yet: the load is about to happen
    await video_api.make_room(req, "m")
    assert room.freed == 1


# ── Studio search ────────────────────────────────────────────────────────────

def _archived(talk_id, *, prompt="", title="", story="", chat="", updated=0.0, name="Mio Kagami"):
    return {
        "talk_id": talk_id, "sha256": f"sha-{talk_id}", "updated_at": updated,
        "character": {"character_id": MIO_ID, "name": name, "name_ja": "各務 みお"},
        "partner_character": {},
        "ideas": [{"kind": "stay", "title": title, "story": story, "beats": ["x"]}],
        "prompts": [{"kind": "stay", "title": title, "prompt": prompt}] if prompt else [],
        "chat": [{"role": "user", "text": chat}] if chat else [],
        "short": "",
    }


def test_search_finds_words_in_prompts_and_says_which_prompt():
    talks = [
        _archived("a", prompt="Rain streaks the studio window. MUSIC: theremin", title="雨の残響", updated=2),
        _archived("b", prompt="A girl runs along a pier.", title="桟橋", updated=3),
    ]
    out = vt.search(talks, "rain")
    assert [r["talk_id"] for r in out] == ["a"]
    assert out[0]["hits"][0]["field"] == "prompt"
    assert out[0]["hits"][0]["prompt_index"] == 0
    assert "Rain streaks" in out[0]["hits"][0]["snippet"]


def test_search_ignores_width_and_case_and_needs_every_word():
    talks = [
        _archived("a", title="雨の残響", story="ＲＥＣの赤灯が消える", updated=1),
        _archived("b", title="雨上がり", story="虹が出る", updated=2),
    ]
    assert [r["talk_id"] for r in vt.search(talks, "rec")] == ["a"]
    assert [r["talk_id"] for r in vt.search(talks, "雨")] == ["b", "a"]  # newest first
    assert [r["talk_id"] for r in vt.search(talks, "雨　虹")] == ["b"]   # full-width space splits
    assert vt.search(talks, "   ") == []


def test_search_reaches_names_and_the_talk():
    talks = [_archived("a", title="t", chat="もっと派手にして")]
    assert vt.search(talks, "派手")[0]["hits"][0]["field"] == "chat"
    assert vt.search(talks, "みお")[0]["hits"][0]["field"] == "name"


def test_search_over_http(monkeypatch, studio):
    client, _ = _client(monkeypatch, studio, [])
    from backend.app.muse import video_api

    async def everything(db):
        return [_archived("a", prompt="Rain falls", title="雨")]

    monkeypatch.setattr(video_api.video_db, "all_talks", everything)
    assert client.get("/api/muse/video/search?q=").json() == {"talks": []}
    r = client.get("/api/muse/video/search?q=rain").json()
    assert r["talks"][0]["talk_id"] == "a"


# ── The card is freed for every render ───────────────────────────────────────

@pytest.mark.asyncio
async def test_every_queued_render_frees_the_llm_first():
    import httpx

    order = []

    def handler(request):
        order.append(request.url.path)
        return httpx.Response(200, json={"prompt_id": "p1"})

    comfy = _comfy(handler)

    async def unload():
        order.append("unload")

    comfy.before_queue = unload
    assert await comfy.queue_prompt({"1": {}}) == "p1"
    assert order == ["unload", "/prompt"]


@pytest.mark.asyncio
async def test_a_failed_unload_never_holds_a_render_back():
    import httpx

    comfy = _comfy(lambda request: httpx.Response(200, json={"prompt_id": "p2"}))

    async def broken():
        raise RuntimeError("ollama is down")

    comfy.before_queue = broken
    assert await comfy.queue_prompt({"1": {}}) == "p2"


@pytest.mark.asyncio
async def test_unload_all_drops_every_resident_model():
    from backend.app.ai.ollama import OllamaClient

    client = OllamaClient(base_url="http://ollama")
    dropped = []

    async def loaded():
        return [{"name": "big:26b"}, {"model": "embed:small"}]

    async def unload(name=None):
        dropped.append(name)

    client.loaded = loaded
    client.unload = unload
    assert await client.unload_all() == ["big:26b", "embed:small"]
    assert dropped == ["big:26b", "embed:small"]


def test_release_unloads_and_never_fails(monkeypatch, studio):
    client, ollama = _client(monkeypatch, studio, [])

    async def unload_all():
        return ["big:26b"]

    ollama.unload_all = unload_all
    assert client.post("/api/muse/video/release").json() == {"unloaded": ["big:26b"]}

    async def broken():
        raise RuntimeError("ollama is down")

    ollama.unload_all = broken
    r = client.post("/api/muse/video/release")
    assert r.status_code == 200 and r.json()["unloaded"] == []
