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


# ── Video talk (video_talk) ──────────────────────────────────────────────────

from backend.app.muse import video_talk as vt


class _FakeDb:
    def __init__(self, meta: dict | None = None):
        self.meta = meta or {}
        self.saved = 0

    async def get_by_sha256s(self, shas):
        return [{"sha256": shas[0], **self.meta}] if self.meta is not None else []


def _session(**extra):
    return {
        "session_id": "s1",
        "inputs": {"locale": "ja", "model": "m", "theme": "radio booth at night"},
        "character": {**MIO, "character_id": "c007", "name_ja": "各務 みお"},
        "shoot": {"images": [{"image_id": "final"}]},
        "chat": [],
        **extra,
    }


@pytest.fixture
def quiet_store(monkeypatch):
    async def save(db, session, publish=True):
        return session

    async def images(db, shas):
        return [b"png"]

    monkeypatch.setattr(vt.session_db, "save", save)
    monkeypatch.setattr(vt, "images_by_sha", images)
    monkeypatch.setattr(vt.talk.events, "publish", lambda *a, **k: None)


def test_reply_lines_parse():
    raw = "SAY: A: それ、いいかも\nB: えー、雨の方がいいって\nPICK: Shift\nBEATS: She looks up / Rain starts\nWRITE: no"
    r = vt.parse_reply(raw)
    assert r["say"] == "A: それ、いいかも\nB: えー、雨の方がいいって"
    assert r["pick"] == "shift" and r["write"] == ""
    assert r["beats"] == "She looks up / Rain starts"
    assert vt.parse_reply("ただの返事")["say"] == "ただの返事"


@pytest.mark.asyncio
async def test_a_still_from_her_shoot_carries_on_from_it():
    session = _session()
    source, people = await vt.photo_people(_FakeDb({"muse_session_id": "s1"}), session, "x")
    assert source == "shoot" and people[0]["name"] == "Mio Kagami"
    # The board and the final count as hers even without the image's record.
    assert (await vt.photo_people(_FakeDb({}), session, "final"))[0] == "shoot"


@pytest.mark.asyncio
async def test_a_photo_from_elsewhere_has_nobody_named_unless_recorded():
    source, people = await vt.photo_people(_FakeDb({}), _session(), "elsewhere")
    assert (source, people) == ("photo", [])
    assert "Not known by name" in ve._who(people)


@pytest.mark.asyncio
async def test_start_pitches_in_her_voice_then_shows_the_cards(quiet_store):
    session = _session()
    ollama = _FakeOllama([
        "A booth.",                       # look
        *[_idea(k) for k in ve.KINDS],     # one idea per kind
        "SAY: 総監督さん、4つ考えました……かな\nPICK: reveal",
    ])
    await vt.start(_FakeDb({"muse_session_id": "s1"}), ollama, session, sha="final")
    talk_call = ollama.calls[-1]
    assert "your own shoot" in talk_call["system"]
    assert "VOICE (how she actually talks" in talk_call["system"]  # her voice block
    assert "radio booth at night" in ollama.calls[1]["prompt"]  # the shoot's context
    kinds = [r.get("meta", {}).get("kind") for r in session["chat"]]
    assert kinds == ["video_start", "say", "video_ideas"]
    assert session["video"]["chosen"] == "reveal"
    assert session["opened"] is True
    assert vt.public(session)["active"] is True


@pytest.mark.asyncio
async def test_a_photo_talk_says_the_people_are_not_her(quiet_store):
    session = _session(shoot={})
    ollama = _FakeOllama(["Two people on a pier.", *[_idea(k) for k in ve.KINDS], "SAY: へえ"])
    await vt.start(_FakeDb({}), ollama, session, sha="pier")
    assert "not you" in ollama.calls[-1]["system"]
    assert "works as" not in ollama.calls[1]["prompt"]


def _talking(session):
    session["video"] = {
        "active": True, "still": "A booth.", "people": ve.cast(session), "duration_s": 5,
        "ideas": [{"kind": "shift", "label": "x", "title": "t", "story": "s", "beats": ["a"]}],
        "chosen": "shift", "started_at": 0,
    }
    return session


@pytest.mark.asyncio
async def test_a_changed_idea_is_shown_before_it_is_written(quiet_store):
    session = _talking(_session())
    ollama = _FakeOllama([
        "SAY: じゃあ雨で！\nPICK: shift\nSTORY: 雨が降り出す\nBEATS: She looks up / Rain falls\nWRITE: yes",
    ])
    await vt.chat(_FakeDb(), ollama, session, "雨にして")
    idea = session["video"]["ideas"][0]
    assert idea["beats"] == ["She looks up", "Rain falls"] and idea["revised"]
    kinds = [r.get("meta", {}).get("kind") for r in session["chat"]]
    assert kinds == ["video", "say", "video_ideas"]


@pytest.mark.asyncio
async def test_switching_to_another_idea_is_not_going_ahead(quiet_store):
    session = _talking(_session())
    session["video"]["ideas"].append({"kind": "cut", "label": "y", "title": "u", "story": "s", "beats": ["b"]})
    ollama = _FakeOllama(["SAY: じゃあ別の場所へ！\nPICK: cut\nWRITE: yes"])
    await vt.chat(_FakeDb(), ollama, session, "もっと動きがほしい")
    assert session["video"]["chosen"] == "cut"
    assert session["chat"][-1]["meta"]["kind"] == "say"


@pytest.mark.asyncio
async def test_she_goes_ahead_when_told(quiet_store):
    session = _talking(_session())
    ollama = _FakeOllama([
        "SAY: いきましょう\nPICK: shift\nWRITE: yes",
        "Theremin",
        "SHOT 1 AT 0: Mio looks up as rain falls.\nSOUND: rain\nMUSIC: theremin, slow, rising",
    ])
    await vt.chat(_FakeDb(), ollama, session, "これでいこう")
    assert session["chat"][-1]["meta"]["kind"] == "video_prompt"
    assert session["chat"][-1]["meta"]["prompt"].startswith(ve.H3_OPENING)


@pytest.mark.asyncio
async def test_duet_lines_without_a_say_label_still_reach_the_chat(quiet_store):
    session = _talking(_session(partner_character={**RIN, "character_id": "c006", "name_ja": "緒方 りん"}))
    ollama = _FakeOllama([
        "A: (各務 みお) [shift]みたいに雨、どうかな B:(緒方 りん) えー、走りたい！\nPICK: shift",
    ])
    await vt.chat(_FakeDb(), ollama, session, "どれがいい？")
    said = [r["text"] for r in session["chat"] if r.get("meta", {}).get("kind") == "say"]
    assert said == ["「t」みたいに雨、どうかな", "えー、走りたい！"]


def test_instrument_lines_are_short():
    assert ve.parse_instruments("Theremin\nA girl with dark hair, wide eyes, and an apron.") == ["Theremin"]


# ── HTTP wiring ──────────────────────────────────────────────────────────────

def _client(monkeypatch, session, replies, meta=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.muse import api as muse_api

    async def load(db, sid):
        return session

    async def save(db, s, publish=True):
        return s

    async def images(db, shas):
        return [b"png"]

    monkeypatch.setattr(muse_api.service, "load_refine", load)
    monkeypatch.setattr(vt.session_db, "save", save)
    monkeypatch.setattr(vt, "images_by_sha", images)
    monkeypatch.setattr(vt.talk.events, "publish", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(muse_api.router)
    app.state.db = _FakeDb(meta)
    app.state.ollama = _FakeOllama(replies)
    return TestClient(app), app.state.ollama


def test_start_chat_write_stop_over_http(monkeypatch):
    session = _session()
    session["inputs"]["locale"] = "en"
    client, ollama = _client(monkeypatch, session, [
        "A booth.", *[_idea(k) for k in ve.KINDS], "SAY: Four ideas!",
    ], meta={"muse_session_id": "s1"})

    r = client.post("/api/muse/sessions/s1/video/start", json={"sha256": "final"})
    assert r.status_code == 200, r.text
    assert r.json()["video"]["active"] is True
    cards = [row for row in r.json()["chat"] if row.get("meta", {}).get("kind") == "video_ideas"]
    assert [c["kind"] for c in cards[0]["meta"]["ideas"]] == list(ve.KINDS)

    ollama.replies = ["SAY: The moment goes on, then.\nPICK: stay"]
    r = client.post("/api/muse/sessions/s1/video/chat", json={"message": "the first one"})
    assert r.status_code == 200 and r.json()["video"]["chosen"] == "stay"

    ollama.replies = ["Theremin", "SHOT 1 AT 0: Mio turns.\nSOUND: s\nMUSIC: theremin, slow, rising"]
    r = client.post("/api/muse/sessions/s1/video/write", json={"kind": "stay"})
    assert r.status_code == 200, r.text
    assert r.json()["chat"][-1]["meta"]["prompt"].startswith(ve.H3_OPENING)

    r = client.post("/api/muse/sessions/s1/video/stop")
    assert r.status_code == 200 and r.json()["video"]["active"] is False


def test_start_needs_an_actress(monkeypatch):
    session = _session(character={})
    session["inputs"]["locale"] = "en"
    client, _ = _client(monkeypatch, session, [])
    r = client.post("/api/muse/sessions/s1/video/start", json={"sha256": "x"})
    assert r.status_code == 400 and r.json()["detail"] == "Pick an actress first"


def test_source_lookup_finds_the_shoot(monkeypatch):
    from backend.app.muse import api as muse_api

    async def load(db, sid):
        return {"session_id": sid}

    monkeypatch.setattr(muse_api.session_db, "load", load)
    client, _ = _client(monkeypatch, _session(), [], meta={"muse_session_id": "s9", "character_id": "c007"})
    r = client.get("/api/muse/video/source/abc")
    assert r.json() == {"session_id": "s9", "character_id": "c007", "partner_character_id": ""}
