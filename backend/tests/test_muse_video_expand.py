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


# What the four writing stages return in a stub run: the instrument pick,
# then draft, acting, camera, environment, music.
WRITE_REPLIES = [
    "Theremin",
    "START: She lies in bed, eyes closed.\n"
    "SEGMENT 0:00-0:02 | wakes\nACTION: An anime girl with red twin tails wakes.\n"
    "SEGMENT 0:02-0:05 | smiles\nACTION: She smiles.",
    "SEGMENT 0:00-0:02 | wakes\nACTION: Her lashes flutter open; she yawns behind her fingers.\n"
    "SEGMENT 0:02-0:05 | smiles\nACTION: She notices the camera, blushes and smiles.",
    "CAMERA 1: close-up, slow push in, eyes in focus\nCAMERA 2: gentle arc left to right",
    "ENVIRONMENT 1: curtains stir\nENVIRONMENT 2: dust glitters in the light",
    "MUSIC 1: a thin theremin line\nMUSIC 2: soft pizzicato joins",
]


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
    assert "left it to you" in ollama.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_a_failed_write_keeps_what_the_model_said(studio):
    # Never settled: the button settles it, and the timetable draft fails twice.
    talk = _talking()
    ollama = _FakeOllama(["SETTLED: yes\nDIRECTION: 雨", "no labels at all", "still nothing"])
    with pytest.raises(vt.VideoTalkError):
        await vt.write(_FakeDb(), ollama, talk)
    assert talk["last_write_failure"]["raw"] == "still nothing"
    assert talk["talk_id"] in studio  # saved, so it can be read afterwards


@pytest.mark.asyncio
async def test_a_failed_draft_is_said_in_the_talk(studio):
    talk = _talking()
    ollama = _FakeOllama(["SETTLED: yes\nDIRECTION: 雨", "x", "y"])
    await vt.chat(_FakeDb(), ollama, talk, "これでいこう")
    kinds = [(r["role"], r.get("kind")) for r in talk["chat"]]
    assert kinds == [("user", None), ("system", "write_failed")]
    assert talk["phase"] == "direction" and talk["board"] == []


def test_instrument_candidates_are_cleaned():
    assert ve.parse_instruments("1. Theremin\n- **Upright bass**\n3) Glockenspiel\ntheremin") == [
        "Theremin", "Upright bass", "Glockenspiel",
    ]


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


class _Pinned(random.Random):
    """The lead mutters, and no spice is drawn."""

    def __init__(self):
        super().__init__(0)

    def choice(self, seq):
        return "A" if seq == "AB" else super().choice(seq)

    def random(self):
        return 0.99


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
    # The mutterer and the spice are drawn; tests that care pin them.
    monkeypatch.setattr(vt, "random", _Pinned())
    return saved


def _pitch_replies(opener="SAY: 総監督さん、4つ考えました……かな"):
    return ["A booth.", *[_idea(k) for k in ve.KINDS], opener]


def test_reply_lines_parse():
    raw = "SAY: A: それ、いいかも\nB: えー、雨の方がいいって\n**ASIDE:** ふふ\nPITCH: 雨 | 晴れ"
    r = vt.parse_reply(raw)
    assert r["say"] == "A: それ、いいかも\nB: えー、雨の方がいいって"
    assert r["aside"] == "ふふ" and r["pitch"] == "雨 | 晴れ"
    assert vt.parse_reply("ただの返事")["say"] == "ただの返事"


@pytest.mark.asyncio
async def test_a_still_from_her_shoot_carries_on_from_it(studio):
    shoot = {
        "session_id": "s1", "character": {**MIO, "character_id": MIO_ID},
        "inputs": {"theme": "radio booth at night"},
    }
    db = _FakeDb({"muse_session_id": "s1"}, {"s1": shoot})
    ollama = _FakeOllama(_pitch_replies())
    talk = await vt.create(db, ollama, sha="final", character_id=MIO_ID, cfg=CFG, short="ほっとした瞬間")
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
    talk = await vt.create(_FakeDb({}), ollama, sha="pier", character_id=MIO_ID, cfg=CFG, short="静かに")
    assert talk["people"] == [] and talk["source_session_id"] == ""
    assert "not you" in ollama.calls[-1]["system"]
    assert "Not known by name" in ollama.calls[1]["prompt"]
    assert "works as" not in ollama.calls[1]["prompt"]


@pytest.mark.asyncio
async def test_a_second_actress_reuses_the_reading(studio):
    first = await vt.create(_FakeDb({}), _FakeOllama(["A pier.", "SAY: どうする？"]), sha="pier", character_id=MIO_ID, cfg=CFG)
    ollama = _FakeOllama(["SAY: A: へえ\nB: いいじゃん"])
    talk = await vt.create(
        _FakeDb({}), ollama, sha="pier", character_id=MIO_ID, partner_id=RIN_ID,
        cfg=CFG, reuse=first,
    )
    assert not any(c["images"] for c in ollama.calls)  # the picture was not read again
    assert [r["name"] for r in talk["chat"]] == ["各務 みお", "緒方 りん"]


@pytest.mark.asyncio
async def test_without_a_wish_she_asks_and_the_first_answer_shapes_the_ideas(studio):
    ollama = _FakeOllama(["A bed.", "SAY: 総監督さん、どんな動画にしますか？"])
    talk = await vt.create(_FakeDb({}), ollama, sha="bed", character_id=MIO_ID, cfg=CFG)
    assert talk["ideas"] == [] and len(ollama.calls) == 2  # read the picture, then asked
    assert "ask what kind of video" in ollama.calls[1]["prompt"]

    ollama.replies = [*[_idea(k) for k in ve.KINDS], "SAY: じゃあ、こんなのはどう？"]
    await vt.chat(_FakeDb({}), ollama, talk, "寝起きのぼーっとした可愛らしさで")
    idea_calls = ollama.calls[2:6]
    assert all("寝起きのぼーっとした可愛らしさで" in c["prompt"] for c in idea_calls)
    assert [i["kind"] for i in talk["ideas"]] == list(ve.KINDS)
    # Still talking it over: no timetable, nothing written.
    assert talk["phase"] == "direction" and talk["board"] == [] and not talk.get("prompts")


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
        "phase": "direction", "direction": "", "board": [], "chat": [], "prompts": [],
    }


BOARD = [
    {"start": 0, "end": 2, "title": "wakes", "action": "An anime girl with red twin tails wakes."},
    {"start": 2, "end": 5, "title": "smiles", "action": "She smiles."},
]


def _on_timeline():
    talk = _talking()
    talk.update({"phase": "timeline", "direction": "寝起きの可愛さ", "board": [dict(s) for s in BOARD]})
    return talk


@pytest.mark.asyncio
async def test_an_unsettled_direction_keeps_talking(studio):
    talk = _talking()
    ollama = _FakeOllama(["SETTLED: no\nDIRECTION:", "SAY: 雨、いいですね。でも…\nASIDE: 濡れたくないな"])
    await vt.chat(_FakeDb(), ollama, talk, "雨はどう？")
    assert talk["phase"] == "direction" and talk["board"] == []
    assert [r.get("kind") for r in talk["chat"]] == [None, None, "aside"]
    # Her side of it: the writers' notes, not a timetable.
    assert "WRITERS' NOTES" in ollama.calls[1]["prompt"] and "TIMETABLE" not in ollama.calls[1]["prompt"]


@pytest.mark.asyncio
async def test_settling_the_direction_drafts_the_timetable(studio):
    talk = _talking()
    ollama = _FakeOllama([
        "SETTLED: yes\nDIRECTION: 寝起きの可愛さを、ゆっくり見せる",
        WRITE_REPLIES[1],
        "SAY: じゃあ最初の2秒から\nPITCH: ゆっくり目を開ける | ぱっと起きる",
    ])
    await vt.chat(_FakeDb(), ollama, talk, "それでいこう")
    assert talk["phase"] == "timeline"
    assert talk["direction"] == "寝起きの可愛さを、ゆっくり見せる"
    assert [s["title"] for s in talk["board"]] == ["wakes", "smiles"]
    assert [r.get("kind") for r in talk["chat"]] == [None, "settled", None, "pitch"]
    assert talk["chat"][-1]["options"] == ["ゆっくり目を開ける", "ぱっと起きる"]
    # Now she talks over the timetable, with the direction in her brief.
    assert "TIMETABLE" in ollama.calls[2]["prompt"]
    assert "寝起きの可愛さを、ゆっくり見せる" in ollama.calls[2]["system"]
    assert not talk.get("prompts")


@pytest.mark.asyncio
async def test_the_timetable_is_written_only_when_asked(studio):
    talk = _on_timeline()
    ollama = _FakeOllama([
        "no",
        "SEGMENT 0:00-0:02 | wakes\nACTION: An anime girl with red twin tails wakes.\n"
        "SEGMENT 0:02-0:05 | caught\nACTION: She notices the camera and blushes.",
        # She thinks it is time; she does not decide that (2026-10-11).
        "SAY: いいですね、書きましょうか\nWRITE: yes",
    ])
    await vt.chat(_FakeDb(), ollama, talk, "最後はカメラに気づいて照れる感じに")
    assert talk["board"][1]["title"] == "caught"
    assert not talk.get("prompts")
    assert [r.get("kind") for r in talk["chat"]] == [None, "board", None]

    ollama.replies = ["yes", "NO CHANGE", *WRITE_REPLIES[:1], *WRITE_REPLIES[2:], "SAY: できました\nPITCH: a | b"]
    await vt.chat(_FakeDb(), ollama, talk, "いいね、撮影文にして")
    assert talk["prompts"][-1]["prompt"].startswith("0:00 - 0:02 | wakes")
    assert [r.get("kind") for r in talk["chat"][-3:]] == [None, "written", None]


def test_the_later_words_outrank_the_settled_direction():
    ctx = ve.write_context(
        still="A rink.", people=[], duration_s=5, direction="震える手から始める",
        intent="- 両手を引かれて滑り出す\n- 震えの表現はいらない",
    )
    # Absolute, the direction outranked "no trembling" (Akari, 2026-10-11).
    assert "(absolute)" not in ctx.split("What the director wants")[0]
    assert "later words or the timetable say otherwise, those win" in ctx
    assert "a later line wins over an earlier one" in ctx


@pytest.mark.asyncio
async def test_quoted_lines_and_a_duet_line_after_the_aside(studio, monkeypatch):
    talk = _talking()
    talk["partner_character"] = {**RIN, "character_id": RIN_ID, "name_ja": "緒方 りん"}
    await vt.chat(_FakeDb(), _FakeOllama([
        "SETTLED: no",
        "SAY: A: 「……勢いよく、ね。」\nASIDE: 脚がまだプルプル\nB: 「よーし！いくよ！」\nPITCH: 速く | もっと速く",
    ]), talk, "もっと勢いよく")
    said = [(r["name"], r["text"], r.get("kind")) for r in talk["chat"] if r["role"] == "assistant" and r.get("kind") != "pitch"]
    assert said == [
        ("各務 みお", "……勢いよく、ね。", None),
        ("緒方 りん", "よーし！いくよ！", None),
        ("各務 みお", "脚がまだプルプル", "aside"),
    ]


def test_spice_is_drawn_now_and_then_and_never_twice_running():
    talk = _talking()
    rng = random.Random(3)
    picks = [vt.draw_spice(talk, rng) for _ in range(200)]
    assert 60 < picks.count("") < 140
    assert all(a != b or not a for a, b in zip(picks, picks[1:]))
    assert "duet" not in picks  # only with two of them
    assert set(picks) - {""} == set(vt.SPICES)


@pytest.mark.asyncio
async def test_a_plain_line_may_carry_a_spice_and_stage_directions_go(studio, monkeypatch):
    talk = _on_timeline()
    monkeypatch.setattr(vt, "draw_spice", lambda t, rng=None: "curious")
    ollama = _FakeOllama(["no", "NO CHANGE", "SAY: えっ、笑うの？\n***\n(The Showrunner looks at the screen.)\nASIDE: ふふ"])
    await vt.chat(_FakeDb(), ollama, talk, "最後は笑顔に")
    assert "THIS TIME, ALSO: Ask the Showrunner something about themselves" in ollama.calls[2]["prompt"]
    said = [r["text"] for r in talk["chat"] if r["role"] == "assistant"]
    assert said == ["えっ、笑うの？", "ふふ"]


@pytest.mark.asyncio
async def test_lines_after_the_one_line_fields_are_speech(studio):
    talk = _talking()
    talk["partner_character"] = {**RIN, "character_id": RIN_ID, "name_ja": "緒方 りん"}
    await vt.chat(_FakeDb(), _FakeOllama([
        "SETTLED: no",
        "SAY: A: 決まりね。\nPITCH: 強く引かれる | 手が滑る\n無理やりだなぁ、もう……。\nASIDE: 緒方 りん)\n（重たいなぁ……。",
    ]), talk, "どう？")
    pitch = next(r for r in talk["chat"] if r.get("kind") == "pitch")
    assert pitch["options"] == ["強く引かれる", "手が滑る"]
    aside = next(r for r in talk["chat"] if r.get("kind") == "aside")
    assert aside["text"] == "重たいなぁ……。"
    said = " ".join(r["text"] for r in talk["chat"] if r["role"] == "assistant" and not r.get("kind"))
    assert "無理やりだなぁ" in said


@pytest.mark.asyncio
async def test_mutters_are_marked_in_the_history_and_the_partner_gets_a_turn(studio, monkeypatch):
    talk = _talking()
    talk["partner_character"] = {**RIN, "character_id": RIN_ID, "name_ja": "緒方 りん"}
    talk["chat"] = [{"role": "assistant", "kind": "aside", "name": "各務 みお", "text": "膝が痛い"}]
    monkeypatch.setattr(vt.random, "choice", lambda seq: "B" if seq == "AB" else seq[0])  # B mutters
    ollama = _FakeOllama(["SETTLED: no", "SAY: A: うん\nB: いくよ\nASIDE: 重たいなぁ"])
    await vt.chat(_FakeDb(), ollama, talk, "どう？")
    prompt = ollama.calls[1]["prompt"]
    assert "各務 みお (muttering to herself): 膝が痛い" in prompt
    assert "it is B (緒方 りん): start the ASIDE with B:" in prompt
    aside = [r for r in talk["chat"] if r.get("kind") == "aside"][-1]
    assert (aside["name"], aside["text"]) == ("緒方 りん", "重たいなぁ")


@pytest.mark.asyncio
async def test_a_no_is_kept_for_the_whole_clip_and_reaches_the_polish(studio):
    talk = _on_timeline()
    await vt.chat(_FakeDb(), _FakeOllama([
        "no",
        "SEGMENT 0:00-0:02 | wakes\nACTION: She wakes.\nSEGMENT 0:02-0:05 | smiles\nACTION: She smiles.\n"
        "**AVOID:** trembling anywhere",
        "SAY: 震えは無しで",
    ]), talk, "震えの表現はいらない")
    assert talk["avoid"] == "trembling anywhere"
    assert talk["board"][-1]["action"] == "She smiles."  # not swallowed into the action
    assert "does not want anywhere in the clip (absolute)" in vt._context(talk)
    assert "trembling anywhere" in vt._context(talk)
    assert ve.parse_avoid("SEGMENT 0:00-0:05 | x\nACTION: y\nAVOID: none") == ""
    assert ve.parse_avoid("SEGMENT 0:00-0:05 | x\nACTION: y") is None


@pytest.mark.asyncio
async def test_an_acknowledgement_twice_running_is_dropped(studio):
    talk = _talking()
    talk["chat"] = [{"role": "assistant", "name": "各務 みお", "text": "わかりました。では指先から。"}]
    await vt.chat(_FakeDb(), _FakeOllama(["SETTLED: no", "SAY: ……わかりました。震えは削ります。"]), talk, "震えはいらない")
    assert talk["chat"][-1]["text"] == "震えは削ります。"
    talk["chat"].append({"role": "assistant", "name": "各務 みお", "text": "笑顔、ですか。"})
    await vt.chat(_FakeDb(), _FakeOllama(["SETTLED: no", "SAY: わかりました。そうします。"]), talk, "笑顔で")
    assert talk["chat"][-1]["text"] == "わかりました。そうします。"  # once is her


@pytest.mark.asyncio
async def test_a_camera_move_asked_for_rides_on_the_timetable_into_the_prompt(studio):
    talk = _on_timeline()
    await vt.chat(_FakeDb(), _FakeOllama([
        "no",
        "SEGMENT 0:00-0:02 | wakes\nACTION: An anime girl with red twin tails wakes.\n"
        "SEGMENT 0:02-0:05 | smiles\nACTION: She smiles.\nCAMERA: circles a quarter turn to her left\nAVOID: none",
        "SAY: ぐるっとね",
    ]), talk, "最後はカメラがぐるっと回り込む感じで")
    assert talk["board"][1]["camera"] == "circles a quarter turn to her left"
    assert [r.get("kind") for r in talk["chat"]][:2] == [None, "board"]  # a camera-only change is a change
    assert vt.view(talk)["board"][1]["camera"] == "circles a quarter turn to her left"

    ollama = _FakeOllama(["yes", "NO CHANGE", "Theremin",
                          WRITE_REPLIES[2], "CAMERA 1: starts waist up; holds still; ends waist up",
                          WRITE_REPLIES[4], WRITE_REPLIES[5], "SAY: できた"])
    await vt.chat(_FakeDb(), ollama, talk, "撮影文にして")
    camera_call = next(c for c in ollama.calls if c["system"] == ve._CAMERA_SYSTEM)
    assert "CAMERA: circles a quarter turn to her left" in camera_call["prompt"]
    # The camera stage answered for segment 1 only: the plan stands for 2.
    assert "Camera Work: circles a quarter turn to her left" in talk["prompts"][-1]["prompt"]


@pytest.mark.asyncio
async def test_the_style_is_read_when_settling_and_reaches_the_camera(studio):
    talk = _talking()
    ollama = _FakeOllama([
        "SETTLED: yes\nSTYLE: flow\nDIRECTION: 走る二人を、止まらないカメラで追う",
        WRITE_REPLIES[1], "SAY: いこう",
    ])
    await vt.chat(_FakeDb(), ollama, talk, "それでいこう")
    assert talk["style"] == "flow"
    assert talk["direction"] == "走る二人を、止まらないカメラで追う"
    assert "How it is shot (moving, no cuts)" in ollama.calls[1]["prompt"]  # the draft
    assert "in 4 segments" in ollama.calls[1]["prompt"]
    assert vt.view(talk)["style"] == "flow"

    ollama = _FakeOllama([*WRITE_REPLIES[:1], *WRITE_REPLIES[2:]])
    await vt.write(_FakeDb(), ollama, talk)
    camera = next(c for c in ollama.calls if c["system"] == ve._CAMERA_SYSTEM)
    assert "no segment holds still" in camera["prompt"]


@pytest.mark.asyncio
async def test_a_style_picked_on_screen_stands(studio):
    talk = _talking()
    await vt.set_style(_FakeDb(), talk, "cuts")
    assert talk["chat"][-1]["text"] == "🎥 撮り方：カット割り"
    await vt.chat(_FakeDb(), _FakeOllama([
        "SETTLED: yes\nSTYLE: calm\nDIRECTION: 静かに", WRITE_REPLIES[1], "SAY: うん",
    ]), talk, "それで")
    assert talk["style"] == "cuts"
    with pytest.raises(vt.VideoTalkError):
        await vt.set_style(_FakeDb(), talk, "wild")


def test_old_talks_are_quiet_ones():
    assert vt.style_of({}) == "calm"
    assert vt.style_of({"style": "nonsense"}) == "calm"


def test_pitch_options_keep_their_inner_quotes():
    talk = _talking()
    vt._post_reply(talk, {"say": "どっち？", "pitch": "瞳に宿る感情を「驚き」にする | 「覚悟」にする"})
    assert talk["chat"][-1]["options"] == ["瞳に宿る感情を「驚き」にする", "「覚悟」にする"]
    vt._post_reply(talk, {"say": "どっち？", "pitch": "「ゆっくり」 | 'fast'"})
    assert talk["chat"][-1]["options"] == ["ゆっくり", "fast"]


@pytest.mark.asyncio
async def test_a_board_written_back_unchanged_is_not_announced(studio):
    talk = _on_timeline()
    same = "\n".join(f"SEGMENT 0:0{s['start']}-0:0{s['end']} | {s['title']}\nACTION: {s['action']}" for s in BOARD)
    await vt.chat(_FakeDb(), _FakeOllama(["no", same, "SAY: うん"]), talk, "どう思う？")
    assert "board" not in [r.get("kind") for r in talk["chat"]]


@pytest.mark.asyncio
async def test_duet_lines_without_a_say_label_still_reach_the_chat(studio):
    talk = _talking()
    talk["partner_character"] = {**RIN, "character_id": RIN_ID, "name_ja": "緒方 りん"}
    ollama = _FakeOllama([
        "SETTLED: no",
        "A: (各務 みお) [shift]みたいに雨、どうかな<br>B:(緒方 りん) えー、走りたい！",
    ])
    await vt.chat(_FakeDb(), ollama, talk, "どれがいい？")
    said = [(r["name"], r["text"]) for r in talk["chat"] if r["role"] == "assistant"]
    assert said == [("各務 みお", "「t」みたいに雨、どうかな"), ("緒方 りん", "えー、走りたい！")]

    talk["chat"] = []
    await vt.chat(_FakeDb(), _FakeOllama(["SETTLED: no", "SAY: A: 力が入っちゃいそう。B: 待って待って！"]), talk, "もっと！")
    said = [(r["name"], r["text"]) for r in talk["chat"] if r["role"] == "assistant"]
    assert said == [("各務 みお", "力が入っちゃいそう。"), ("緒方 りん", "待って待って！")]


@pytest.mark.asyncio
async def test_a_trailing_slash_is_not_part_of_her_line(studio):
    talk = _talking()
    await vt.chat(_FakeDb(), _FakeOllama(["SETTLED: no", "SAY: すごく好みです。 /\nねえ、どうする？"]), talk, "どう？")
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
    ollama = _FakeOllama(["SETTLED: no", "SAY: 可愛らしく、ですね"])
    await vt.chat(_FakeDb(), ollama, talk, "どんな感じがいいかな")
    system = ollama.calls[1]["system"]
    assert "木漏れ日" not in system and "雨の路地" not in system
    assert "少し打ち解けてきた" in system


@pytest.mark.asyncio
async def test_her_aside_loses_the_brackets_and_names_written_in(studio):
    talk = _talking()
    talk["partner_character"] = {**RIN, "character_id": RIN_ID, "name_ja": "緒方 りん"}
    for aside, who, text in [
        ("(A: 嘘じゃないけど……)", "各務 みお", "嘘じゃないけど……"),
        ("(緒方 りん) 腕が痛い", "緒方 りん", "腕が痛い"),
        ("（ちょっと照れる）", "各務 みお", "ちょっと照れる"),
        ("(東野 はづキ) ……膝すりむかないかな", "各務 みお", "……膝すりむかないかな"),
        ("（B: 「よーし、仕留めてやるんだから。」）", "緒方 りん", "よーし、仕留めてやるんだから。"),
    ]:
        talk["chat"] = []
        await vt.chat(_FakeDb(), _FakeOllama(["SETTLED: no", f"SAY: A: うん\nASIDE: {aside}"]), talk, "どう？")
        row = next(r for r in talk["chat"] if r.get("kind") == "aside")
        assert (row["name"], row["text"]) == (who, text)


def test_instrument_lines_are_short():
    assert ve.parse_instruments("Theremin\nA girl with dark hair, wide eyes, and an apron.") == ["Theremin"]
    assert ve.parse_instruments("[Instrument 1]\n[Instrument 2]\nInstrument 3\nCelesta") == ["Celesta"]


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


def test_create_chat_settle_write_over_http(monkeypatch, studio):
    client, ollama = _client(monkeypatch, studio, ["A pier.", "SAY: What shall we make of it?"])
    r = client.post("/api/muse/video/talks", json={"sha256": "pier", "character_id": MIO_ID, "locale": "en"})
    assert r.status_code == 200, r.text
    tid = r.json()["talk_id"]
    assert r.json()["ideas"] == []  # she asks first

    ollama.replies = [*[_idea(k) for k in ve.KINDS], "SAY: Four ideas!"]
    r = client.post(f"/api/muse/video/talks/{tid}/chat", json={"message": "something sleepy"})
    assert [i["kind"] for i in r.json()["ideas"]] == list(ve.KINDS)

    ollama.replies = ["SETTLED: no", "SAY: Sleepy how?"]
    r = client.post(f"/api/muse/video/talks/{tid}/chat", json={"message": "the first one", "duration_s": 10})
    assert r.status_code == 200 and r.json()["phase"] == "direction" and r.json()["duration_s"] == 10

    # The button settles it as the talk stands.
    ollama.replies = ["SETTLED: yes\nDIRECTION: Sleepy and slow.", WRITE_REPLIES[1], "SAY: From the top, then."]
    r = client.post(f"/api/muse/video/talks/{tid}/settle")
    assert r.status_code == 200, r.text
    assert r.json()["phase"] == "timeline" and r.json()["direction"] == "Sleepy and slow."
    # Drafted for the 10 s the talk was set to.
    assert r.json()["board"][-1]["end"] == 10

    ollama.replies = [*WRITE_REPLIES[:1], *WRITE_REPLIES[2:]]
    r = client.post(f"/api/muse/video/talks/{tid}/write", json={})
    assert r.status_code == 200, r.text
    assert r.json()["prompts"][-1]["prompt"].startswith("0:00 - 0:0")
    assert "stages" not in r.json()["prompts"][-1]  # kept on the record, not sent to the screen

    r = client.post(f"/api/muse/video/talks/{tid}/style", json={"style": "flow"})
    assert r.status_code == 200 and r.json()["style"] == "flow"
    assert client.post(f"/api/muse/video/talks/{tid}/style", json={"style": "x"}).status_code == 400

    r = client.post(f"/api/muse/video/talks/{tid}/phase", json={"phase": "direction"})
    assert r.status_code == 200 and r.json()["phase"] == "direction"
    assert r.json()["board"]  # kept until a new one is drafted

    ollama.replies = [*[_idea(k) for k in ve.KINDS], "SAY: New ones!"]
    r = client.post(f"/api/muse/video/talks/{tid}/repitch")
    assert r.status_code == 200 and [i["kind"] for i in r.json()["ideas"]] == list(ve.KINDS)


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


# ── The four-stage writer ────────────────────────────────────────────────────

@pytest.mark.parametrize("head", [
    "SEGMENT 0:00-0:02 | wakes",
    "**SEGMENT 0:00 - 0:02 | wakes**",
    "### 0:00–0:02 ｜ wakes",
    "- SEGMENT 0:00~0:02: wakes",
])
def test_segment_headers_survive_drift(head):
    segs = ve.parse_segments(f"{head}\nACTION: she wakes\nand stretches\n0:02-0:05 | smiles\nACTION: she smiles")
    assert [(s["start"], s["end"], s["title"]) for s in segs] == [(0, 2, "wakes"), (2, 5, "smiles")]
    assert segs[0]["action"] == "she wakes and stretches"


def test_a_segment_without_an_action_is_not_a_segment():
    assert ve.parse_segments("SEGMENT 0:00-0:02 | wakes\nSEGMENT 0:02-0:05 | x\nACTION: y") == [
        {"start": 2, "end": 5, "title": "x", "action": "y"},
    ]


def test_layout_shares_out_bad_times_and_writes_the_pasted_shape():
    segs = [
        {"start": 0, "end": 9, "title": "a", "action": "one", "camera": "push in", "music": "guitar"},
        {"start": 9, "end": 9, "title": "b", "action": "two"},
    ]
    text = ve.layout(segs, duration_s=5)
    assert text == (
        "0:00 - 0:02 | a\nVisual Action: one\nCamera Work: push in\nMusic: guitar\n\n"
        "0:02 - 0:05 | b\nVisual Action: two"
    )
    with pytest.raises(ValueError):
        ve.layout([], duration_s=5)


def test_numbered_lines_go_to_their_segment():
    assert ve.parse_numbered("CAMERA 2: b\nCAMERA 1: a\nMUSIC 1: x", "CAMERA", 3) == ["a", "b", ""]
    assert ve.parse_numbered("**CAMERA:** a\nCAMERA: b", "CAMERA", 2) == ["a", "b"]


@pytest.mark.asyncio
async def test_the_four_stages_each_do_one_job():
    ollama = _FakeOllama(list(WRITE_REPLIES))
    out = await ve.write(
        ollama, still="A bed.", people=[], idea={"kind": "stay", "beats": ["She wakes."], "story": "可愛い寝起き"},
        duration_s=5, model="m", num_ctx=None, rng=random.Random(0),
        intent="- 寝起きの可愛らしさ", persona="</start REFERENCE ONLY>\nhabit: yawns\n</end REFERENCE ONLY>",
    )
    draft, acting, camera, env, music = ollama.calls[1:6]
    assert [c["system"] for c in (draft, acting, camera, env, music)] == [
        ve._DRAFT_SYSTEM, ve._ACTING_SYSTEM, ve._CAMERA_SYSTEM, ve._ENV_SYSTEM, ve._SCORE_SYSTEM,
    ]
    # What the director asked for opens every stage.
    assert all(c["prompt"].startswith("What the director wants (absolute)") for c in (draft, acting, camera, env, music))
    # Her persona reaches the acting stage only.
    assert "REFERENCE ONLY" in acting["prompt"]
    assert not any("REFERENCE ONLY" in c["prompt"] for c in (draft, camera, env, music))
    assert "The music starts on: Theremin" in music["prompt"]
    # The acting rewrite is what ships; camera and music only add their lines.
    assert out["prompt"] == (
        "0:00 - 0:02 | wakes\nVisual Action: Her lashes flutter open; she yawns behind her fingers.\n"
        "Camera Work: close-up, slow push in, eyes in focus\nEnvironment: curtains stir\n"
        "Music: a thin theremin line\n\n"
        "0:02 - 0:05 | smiles\nVisual Action: She notices the camera, blushes and smiles.\n"
        "Camera Work: gentle arc left to right\nEnvironment: dust glitters in the light\n"
        "Music: soft pizzicato joins"
    )
    assert set(out["stages"]) == {"draft", "acting", "camera", "environment", "music"}


@pytest.mark.asyncio
async def test_a_failed_later_stage_keeps_the_draft():
    replies = list(WRITE_REPLIES)
    replies[2] = "the acting stage lost the format"  # acting
    replies[3] = ""  # camera came back empty
    out = await ve.write(
        _FakeOllama(replies), still="A bed.", people=[], idea={"beats": ["x"]},
        duration_s=5, model="m", num_ctx=None,
    )
    assert "Visual Action: An anime girl with red twin tails wakes." in out["prompt"]
    assert "Camera Work" not in out["prompt"]
    assert "Music: a thin theremin line" in out["prompt"]


@pytest.mark.asyncio
async def test_the_draft_is_retried_once_then_says_so():
    with pytest.raises(ve.VideoError) as err:
        await ve.write(
            _FakeOllama(["Theremin", "no labels", "still none"]), still="A bed.", people=[],
            idea={"beats": ["x"]}, duration_s=5, model="m", num_ctx=None,
        )
    assert err.value.raw == "still none"


def test_her_reference_is_fenced_and_says_not_to_copy():
    block = ve.reference_block({**MIO, "personality": {
        **MIO["personality"], "traits": ["shy_offstage", "playful"],
    }})
    assert block.startswith("** This is Mio Kagami's background.")
    assert "Do not put these words into the prompt" in block
    assert "</start REFERENCE ONLY>" in block and block.endswith("</end REFERENCE ONLY>")
    assert "habit: presses one headphone cup" in block
    assert ve.reference_block({}) == ""


def test_persona_goes_only_to_who_is_in_the_picture():
    talk = _talking()
    talk["character"] = {**talk["character"], "personality": {"appearance": {"habit": "presses one headphone cup"}}}
    talk["people_ids"] = [MIO_ID]
    assert "presses one headphone cup" in vt.persona_of(talk)
    talk["people_ids"] = []  # a photograph of someone else
    assert vt.persona_of(talk) == ""
