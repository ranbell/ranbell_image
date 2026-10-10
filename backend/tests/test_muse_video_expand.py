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


# ── HTTP wiring ──────────────────────────────────────────────────────────────

def _client(monkeypatch, session, replies):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.muse import api as muse_api

    async def load(db, sid):
        return session

    async def images(db, shas):
        return [b"png"]

    monkeypatch.setattr(muse_api.service, "load_refine", load)
    monkeypatch.setattr(muse_api, "images_by_sha", images)
    app = FastAPI()
    app.include_router(muse_api.router)
    app.state.db = None
    app.state.ollama = _FakeOllama(replies)
    return TestClient(app), app.state.ollama


def test_ideas_then_write_over_http(monkeypatch):
    session = {
        "inputs": {"locale": "en", "model": "m"},
        "character": MIO,
        "shoot": {"images": [{"image_id": "final"}], "prompt": "Mio at the mic."},
    }
    look = "Mio stands at a microphone in a booth."
    ideas = [_idea(k) for k in ve.KINDS]
    client, ollama = _client(monkeypatch, session, [look, *ideas])

    r = client.post("/api/muse/sessions/s1/video/ideas", json={"sha256": "final"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["still"] == look and body["blind"] is False
    assert [i["kind"] for i in body["ideas"]] == list(ve.KINDS)
    assert body["ideas"][0]["label"] == ve.KINDS["stay"]["en"]

    # A re-pitch sends the reading back; the picture is not looked at again.
    ollama.replies = [*ideas]
    r = client.post(
        "/api/muse/sessions/s1/video/ideas", json={"sha256": "final", "still": look},
    )
    assert r.status_code == 200 and not any(c["images"] for c in ollama.calls[5:])

    ollama.replies = ["Theremin", "SHOT 1 AT 0: Mio turns.\nSOUND: s\nMUSIC: theremin, slow, rising"]
    r = client.post(
        "/api/muse/sessions/s1/video/write",
        json={"still": look, "idea": body["ideas"][0], "duration_s": 5},
    )
    assert r.status_code == 200, r.text
    assert r.json()["prompt"].startswith(ve.H3_OPENING)


def test_ideas_refuse_a_board_image_in_the_session_language(monkeypatch):
    session = {
        "inputs": {"locale": "en"},
        "character": MIO,
        "board": {"images": [{"image_id": "board"}]},
        "shoot": {"images": [{"image_id": "final"}]},
    }
    client, _ = _client(monkeypatch, session, [])
    r = client.post("/api/muse/sessions/s1/video/ideas", json={"sha256": "board"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Pick one of the final stills"
