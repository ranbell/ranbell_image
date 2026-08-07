"""Chat studio wiring: table → note → board → OK → shoot.

BCD refine is gone. The showrunner chats; the crew revises craft; a board asks
「これでいい？」; OK submits the final shoot.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import crew, identity, runner, service, session_db


class FakeSpooler:
    def __init__(self):
        self.jobs: list[dict] = []
        self.cancelled: list[str] = []

    def submit(self, lane, title, func, meta=None, **kw):
        self.jobs.append({"lane": lane, "title": title, "func": func,
                          "meta": meta or {}, **kw})
        return f"job-{len(self.jobs)}"

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        return True


class FakeComfy:
    def load_workflow(self, name):
        return {}

    def patchable_fields(self, wf):
        return {"steps": 1, "cfg": 1, "width": 1, "height": 1}


class FakeOllama:
    def __init__(self):
        self.unloaded: list[str] = []
        self.calls: list[dict] = []

    def generate_text_stream(self, prompt, **kw):
        self.calls.append(kw)

        async def _stream():
            yield {"type": "think", "text": "deliberating"}
            yield {
                "type": "token",
                "text": (
                    "SAY: Director, the beat is locked.\n\n"
                    "TAGS: standing, indoor\n\n"
                    "SCENE: STAGE A PROMPT"
                ),
            }
        return _stream()

    async def unload(self, model=None):
        self.unloaded.append(model)


class FakeDb:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self._qc = self

    async def upsert(self, collection_name, points):
        for p in points:
            self.rows[str(p.id)] = dict(p.payload)

    async def retrieve(self, collection_name, ids, with_payload=True):
        class _P:
            def __init__(self, payload):
                self.payload = payload
                self.id = payload["session_id"]
        return [_P(self.rows[i]) for i in ids if i in self.rows]


@pytest.fixture(autouse=True)
def _no_runtime_config(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


async def _ready_session(db, **over):
    session = await service.create_session(db, {
        "theme": "a quiet indoor moment", "character_id": "c1",
        "workflow": "w.json", "model": "m",
        "crew_preset": "classic",
        **over,
    })
    session["character"] = {"identity_tags": ["1girl", "blue_hair"],
                            "personality": {}, "palette": [], "signature_prop": ""}
    service._rebuild_brief(session)
    await session_db.save(db, session)
    return session


@pytest.mark.asyncio
async def test_table_builds_craft_and_chat_without_submitting_comfy():
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db)

    session = await service.start_table(db, ollama, session)

    assert spooler.jobs == []
    assert session["status"] == "chat"
    assert "STAGE A PROMPT" in session["craft"]["prompt"]
    assert "blue_hair" in session["craft"]["prompt"]
    assert session["craft"]["pose_intent"] == "STAGE A PROMPT"
    assert "blue_hair" not in session["craft"]["pose_intent"]
    # System open + each muse + wrap ask (+ light banter by default).
    assert any(m["role"] == "muse" for m in session["chat"])
    assert any(m.get("kind") == "craft" for m in session["chat"])
    assert ollama.unloaded == []
    assert len(ollama.calls) >= 2  # classic crew + actress + finisher


@pytest.mark.asyncio
async def test_board_submits_one_job_from_craft():
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db)
    session = await service.start_table(db, ollama, session)

    session = await service.request_board(db, FakeComfy(), spooler, session, ollama=ollama)

    assert len(spooler.jobs) == 1
    job = spooler.jobs[0]
    assert job["func"] is runner.run_board_job
    assert job["session_id"] == session["session_id"]
    assert session["board"]["pending"] is True
    assert session["board"]["seed"] > 0
    assert session["board"]["job_id"]
    assert session["status"] == "boarding"
    # The LLM comes off the card before the render: it is still holding VRAM
    # otherwise, and a full-size latent then has nowhere to go.
    assert ollama.unloaded == ["m"]


@pytest.mark.asyncio
async def test_thin_craft_is_densified_before_board():
    """Board must not ship a tweet-length SCENE — Finisher packs density first."""
    db, spooler = FakeDb(), FakeSpooler()

    class DenseOllama(FakeOllama):
        def generate_text_stream(self, prompt, **kw):
            self.calls.append(kw)
            dense_scene = (
                "She sits at a wooden desk by a tall window, weight on her right elbow, "
                "shoulders soft, braid falling forward as afternoon light cuts across the "
                "grain. A ceramic cup, open notebook, pencil, stacked books, desk lamp, "
                "curtain fold, window latch, potted plant, chair back and scattered papers "
                "fill the near space so the room feels lived-in. She looks toward the viewer "
                "with a small attentive expression, one hand resting near the page as if mid "
                "thought, while soft shadow holds under her chin and the camera stays a "
                "slight low medium shot that keeps face and hands readable without emptying "
                "the desk clutter that makes the moment specific to this theme."
            )
            # pad to clear 140 words
            dense_scene = dense_scene + " " + " ".join(["detail"] * 40)
            text = (
                "SAY: 密度上げました。のっぺりさせません。\n\n"
                "TAGS: masterpiece, best_quality, sitting, leaning_on_table, "
                "wooden_desk, window, notebook, pencil, books, desk_lamp, curtain, "
                "potted_plant, papers, cup, from_side, slightly_from_below, "
                "medium_shot, upper_body, light_blush, smile, hand_up, "
                "looking_at_viewer, depth_of_field, soft_lighting\n\n"
                f"SCENE: {dense_scene}"
            )

            async def _stream():
                yield {"type": "token", "text": text}
            return _stream()

    ollama = DenseOllama()
    session = await _ready_session(db, banter_mode="off")
    session["craft"] = {
        "prompt": "1girl, aqua_hair, sitting, smile, She sits.",
        "scene": "She sits.",
        "tags": "sitting, smile",
        "pose_intent": "She sits.",
    }
    session["status"] = "chat"
    session["brief"] = "x"
    await session_db.save(db, session)

    session = await service.request_board(
        db, FakeComfy(), spooler, session, ollama=ollama,
    )
    assert identity.word_count(session["board"]["prompt"]) >= 160
    assert "dens" in " ".join(
        m["text"].lower() for m in session["chat"] if m.get("role") == "system"
    ) or any("密度" in m["text"] for m in session["chat"])


@pytest.mark.asyncio
async def test_chat_ok_shoots_with_board_seed():
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db)
    session = await service.start_table(db, ollama, session)
    session["board"] = {"seed": 4242, "images": [{"image_id": "sha0"}], "pending": False}
    await session_db.save(db, session)

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "OK",
    )

    assert len(spooler.jobs) == 1
    assert spooler.jobs[0]["func"] is runner.run_shoot_job
    assert session["shoot"]["seed"] == 4242
    assert session["status"] == "shooting"


@pytest.mark.asyncio
async def test_chat_note_revises_craft_without_comfy():
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db)
    session = await service.start_table(db, ollama, session)
    n_calls = len(ollama.calls)

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "もっと寄って、服をコートに",
    )

    assert spooler.jobs == []
    assert session["status"] == "chat"
    assert len(ollama.calls) > n_calls
    assert any(m["role"] == "user" and "コート" in m["text"] for m in session["chat"])


@pytest.mark.asyncio
async def test_chat_board_keyword_requests_board():
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db)
    session = await service.start_table(db, ollama, session)

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "ボード出して",
    )

    assert len(spooler.jobs) == 1
    assert spooler.jobs[0]["func"] is runner.run_board_job


@pytest.mark.asyncio
async def test_refine_is_removed():
    db = FakeDb()
    session = await _ready_session(db)
    with pytest.raises(service.MuseError) as err:
        await service.run_refine(db, FakeOllama(), FakeComfy(), FakeSpooler(),
                                 session, [0])
    assert "廃止" in str(err.value) or "removed" in str(err.value).lower()


@pytest.mark.asyncio
async def test_table_refuses_missing_inputs():
    db = FakeDb()
    session = await service.create_session(db, {"theme": "t"})
    with pytest.raises(service.MuseError) as err:
        await service.start_table(db, FakeOllama(), session)
    assert "character" in str(err.value)


@pytest.mark.asyncio
async def test_cancelling_a_board_clears_it():
    db, spooler = FakeDb(), FakeSpooler()
    session = await _ready_session(db)
    session["board"] = {"job_id": "job-1", "images": [], "pending": True}
    session["craft"] = {"prompt": "x"}

    session = await service.cancel_draft(db, spooler, session)

    assert spooler.cancelled == ["job-1"]
    assert session["board"] == {}
    assert session["status"] == "chat"


@pytest.mark.asyncio
async def test_board_images_keep_arriving_until_finish():
    db = FakeDb()
    session = await _ready_session(db)
    session["board"] = {"job_id": "job-1", "seed": 7, "images": [], "pending": True}
    session["status"] = "boarding"
    await session_db.save(db, session)
    sid = session["session_id"]

    for sha in ("a", "b", "c", "d", "e"):
        await session_db.attach_board_image(db, sid, sha, {"seed": 7})
    s = await session_db.load(db, sid)
    assert len(s["board"]["images"]) == 5
    assert s["board"]["pending"] is True

    await session_db.finish_board(db, sid)
    s = await session_db.load(db, sid)
    assert s["board"]["pending"] is False
    assert s["status"] == "awaiting_ok"


@pytest.mark.asyncio
async def test_finishing_does_not_resurrect_a_cancelled_board():
    db, spooler = FakeDb(), FakeSpooler()
    session = await _ready_session(db)
    session["board"] = {"job_id": "job-1", "images": [], "pending": True}
    session = await service.cancel_draft(db, spooler, session)

    await session_db.finish_board(db, session["session_id"], error="cancelled")
    s = await session_db.load(db, session["session_id"])
    assert s["board"] == {}
    assert s["status"] == "chat"


@pytest.mark.asyncio
async def test_unload_vlm_is_opt_in_on_board():
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db)
    session["inputs"]["unload_vlm"] = True
    session = await service.start_table(db, ollama, session)
    ollama.unloaded.clear()

    session = await service.request_board(
        db, FakeComfy(), spooler, session, ollama=ollama,
    )

    assert ollama.unloaded == ["m"]


def test_the_workflows_last_image_is_the_one_worth_keeping():
    assert runner.finished_image(["raw", "upscaled"]) == "upscaled"
    assert runner.finished_image(["only"]) == "only"


def test_is_approve_accepts_natural_ok_phrases():
    assert service._is_approve("OK")
    assert service._is_approve("本番")
    assert service._is_approve("Ok 本番よろしく")
    assert service._is_approve("よし撮って！")
    assert not service._is_approve("OKじゃない、もっと可愛く")
    assert not service._is_approve("ボード出して")
    assert not service._is_approve("服をもっと派手に")


def test_pick_responders_is_fixed_desk_not_keyword_router():
    """Mood/situation words must not change the cast — VLM reads the note."""
    crew_ids = [
        "beat", "spine", "lens", "wardrobe", "gaffer",
        "actress", "faces", "hook", "finisher",
    ]
    a = service._pick_responders("服をコートにして", crew_ids)
    b = service._pick_responders("トーンを変えて", crew_ids)
    c = service._pick_responders("雰囲気をもっと出して", crew_ids)
    d = service._pick_responders("画角を寄せて", crew_ids)
    assert a == b == c == d
    assert a[0] == "actress"
    assert a[-1] == "finisher"
    assert len(a) <= 5  # cap craft + finisher for Ollama
    # No keyword→muse pattern table — note text must not be inspected.
    import inspect
    src = inspect.getsource(service._pick_responders)
    assert "re.search" not in src
    assert "pairs" not in src
    assert "want" not in src


@pytest.mark.asyncio
async def test_showrunner_comment_reruns_a_short_turn():
    """The loop the Showrunner is testing: note → specialists revise → chat again."""
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db, banter_mode="off")  # craft-only for speed
    session = await service.start_table(db, ollama, session)
    n_chat = len(session["chat"])

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session,
        "もう少し落ち着いた雰囲気にして",
    )

    assert session["status"] == "chat"
    assert spooler.jobs == []  # comment is LLM turns, not Comfy
    assert any(
        m["role"] == "user" and "落ち着いた" in m["text"] for m in session["chat"]
    )
    spoken = [m.get("muse_id") for m in session["chat"][n_chat:] if m.get("role") == "muse"]
    assert "actress:cast" in spoken
    assert "finisher:maku" in spoken
    assert len(session["chat"]) > n_chat
    # Craft was touched by at least one responder (FakeOllama always rewrites).
    assert session["craft"]["prompt"]


@pytest.mark.asyncio
async def test_light_banter_mode_fires_fewer_side_calls_than_full():
    db, ollama_light, ollama_full = FakeDb(), FakeOllama(), FakeOllama()
    s_light = await _ready_session(db, banter_mode="light", crew_preset="classic")
    s_light = await service.start_table(db, ollama_light, s_light)
    light_banter = sum(1 for m in s_light["chat"] if m.get("kind") == "banter")

    db2 = FakeDb()
    s_full = await _ready_session(db2, banter_mode="full", crew_preset="classic")
    s_full = await service.start_table(db2, ollama_full, s_full)
    full_banter = sum(1 for m in s_full["chat"] if m.get("kind") == "banter")

    assert light_banter < full_banter
    assert light_banter >= 1


@pytest.mark.asyncio
async def test_choosing_a_preset_replaces_the_crew_it_does_not_merge():
    """A new session already carries crew_ids, and both were read from the
    merged inputs, so the ids always won and picking a preset did nothing."""
    db = FakeDb()
    session = await service.create_session(db, {})

    session = await service.patch_inputs(db, session, {"crew_preset": "flat"})
    flat = list(session["inputs"]["crew_ids"])
    # Both crews light and shoot; they send different people to do it.
    assert "ink:ipponsen" in flat and "gaffer:gyakkou" not in flat

    session = await service.patch_inputs(db, session, {"crew_preset": "photoreal"})
    real = list(session["inputs"]["crew_ids"])
    assert real != flat
    assert "lens:pinto" in real and "ink:atsunuri" in real


@pytest.mark.asyncio
async def test_toggling_a_seat_keeps_the_rest_of_the_crew():
    db = FakeDb()
    session = await service.create_session(db, {})
    session = await service.patch_inputs(db, session, {"crew_preset": "standard"})
    kept = [i for i in session["inputs"]["crew_ids"] if i != "gaffer:gyakkou"]

    session = await service.patch_inputs(db, session, {"crew_ids": kept})
    assert "gaffer:gyakkou" not in session["inputs"]["crew_ids"]
    assert session["inputs"]["crew_ids"] == kept


@pytest.mark.asyncio
async def test_the_crew_decides_the_look_when_the_showrunner_did_not():
    db = FakeDb()
    session = await service.create_session(db, {})
    session = await service.patch_inputs(db, session, {"crew_preset": "flat"})
    flat_look = service._style(session)

    session = await service.patch_inputs(db, session, {"crew_preset": "photoreal"})
    real_look = service._style(session)

    assert flat_look != real_look
    assert "flat" in flat_look and "semi-realistic" in real_look

    session = await service.patch_inputs(db, session, {"style": "watercolour storybook"})
    assert service._style(session) == "watercolour storybook"


class PlanningOllama(FakeOllama):
    """Answers the planner in labelled lines and everyone else in craft."""

    PLAN = (
        "SAY: 場所と時間、決めますね。\n\n"
        "PLACE: a narrow upstairs room\n"
        "HOUR: late afternoon\n"
        "LIGHT: even daylight from one window, mid-key, normal exposure\n"
        "ACTION: she has just sat down\n"
        "MUST APPEAR: low_table, cushion, window, curtain, mug, rug\n"
    )

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({**kw, "prompt": prompt})
        plan = "settle the situation" in (kw.get("system") or "").lower() \
            or "PLAN (WHERE, WHEN" in (kw.get("system") or "")

        async def _stream():
            yield {"type": "token", "text": self.PLAN if plan else (
                "SAY: Director, the beat is locked.\n\n"
                "TAGS: standing, indoor\n\n"
                "SCENE: STAGE A PROMPT"
            )}
        return _stream()

    def generate_vlm_stream(self, prompt, images, **kw):
        self.calls.append({**kw, "prompt": prompt, "images": images})
        return self.generate_text_stream(prompt, **kw)


class SeeingDb(FakeDb):
    """Resolves a board sha to a real file on disk."""

    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.looked_up: list[list[str]] = []

    async def get_by_sha256s(self, shas):
        self.looked_up.append(list(shas))
        return [{"path": self.path, "sha256": s} for s in shas]


@pytest.fixture
def board_file(tmp_path):
    from PIL import Image
    p = tmp_path / "board.png"
    Image.new("RGB", (896, 1152), (40, 60, 90)).save(p)
    return str(p)


@pytest.mark.asyncio
async def test_the_planner_settles_the_place_before_anyone_describes_it():
    db, ollama = FakeDb(), PlanningOllama()
    session = await _ready_session(db, crew_preset="trio", banter_mode="off")

    session = await service.start_table(db, ollama, session)

    assert session["plan"]["place"] == "a narrow upstairs room"
    assert "low_table" in session["plan"]["must_appear"]
    # It is re-stated to every seat, so a chain of rewrites cannot relocate it.
    assert "PLACE: a narrow upstairs room" in session["brief"]
    assert "PLACE: a narrow upstairs room" in session["brief_lite"]
    # And it spoke in chat, so the Showrunner can veto the place.
    assert any(m.get("muse_id") == "plan:madori" for m in session["chat"])
    # The planner does not write craft.
    assert "STAGE A PROMPT" in session["craft"]["prompt"]


@pytest.mark.asyncio
async def test_a_showrunner_note_becomes_standing_direction():
    """The bug this exists for: a note reached only the turn that answered it,
    so the original theme outvoted it on every later call and never rendered."""
    db, spooler, ollama = FakeDb(), FakeSpooler(), PlanningOllama()
    session = await _ready_session(db, crew_preset="trio", banter_mode="off")
    session = await service.start_table(db, ollama, session)

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "屋内にして、椅子に座らせて",
    )
    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "もっと明るく",
    )

    assert session["notes"] == ["屋内にして、椅子に座らせて", "もっと明るく"]
    for note in session["notes"]:
        assert note in session["brief"]
        assert note in session["brief_lite"]


@pytest.mark.asyncio
async def test_a_note_re_settles_the_plan_rather_than_appending_to_it():
    db, spooler, ollama = FakeDb(), FakeSpooler(), PlanningOllama()
    session = await _ready_session(db, crew_preset="trio", banter_mode="off")
    session = await service.start_table(db, ollama, session)
    plans_before = sum(1 for e in session["timeline"] if e["step"] == "plan")

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "別の場所にして",
    )

    assert sum(1 for e in session["timeline"] if e["step"] == "plan") > plans_before


@pytest.mark.asyncio
async def test_the_crew_is_shown_the_board_when_answering_a_note(board_file):
    db, spooler, ollama = SeeingDb(board_file), FakeSpooler(), PlanningOllama()
    session = await _ready_session(db, crew_preset="trio", banter_mode="off")
    session = await service.start_table(db, ollama, session)
    session["board"] = {
        "seed": 7, "round": 1, "pending": False,
        "images": [{"index": 0, "image_id": "sha0"}],
    }
    await session_db.save(db, session)
    ollama.calls.clear()

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "背景が違う",
    )

    seen = [c for c in ollama.calls if c.get("images")]
    assert seen, "the crew answered a note about the board without looking at it"
    # Downscaled for the VLM rather than shipped at render size.
    assert all(len(img) > 0 for c in seen for img in c["images"])
    assert any("露出" in c["prompt"] or "exposure" in c["prompt"] for c in seen)


@pytest.mark.asyncio
async def test_no_board_means_no_images_and_no_screening_note():
    db, spooler, ollama = SeeingDb("/nonexistent"), FakeSpooler(), PlanningOllama()
    session = await _ready_session(db, crew_preset="trio", banter_mode="off")
    session = await service.start_table(db, ollama, session)
    ollama.calls.clear()

    await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "もう少し寄って",
    )

    assert not any(c.get("images") for c in ollama.calls)
    assert db.looked_up == []


@pytest.mark.asyncio
async def test_an_unreadable_board_does_not_stop_the_table(board_file):
    db, spooler, ollama = SeeingDb("/nonexistent/board.png"), FakeSpooler(), PlanningOllama()
    session = await _ready_session(db, crew_preset="trio", banter_mode="off")
    session = await service.start_table(db, ollama, session)
    session["board"] = {
        "seed": 7, "round": 1, "pending": False,
        "images": [{"index": 0, "image_id": "sha0"}],
    }
    await session_db.save(db, session)

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "背景が違う",
    )
    assert session["status"] == "chat"
    assert not any(c.get("images") for c in ollama.calls)


@pytest.mark.asyncio
async def test_banter_never_reaches_the_prompt_that_writes_the_picture():
    """Heckles carry no craft and every seat is told to charm in them. Fed back
    into the craft turn they were a loop with nothing damping it."""
    session = {"chat": [
        {"role": "muse", "kind": "craft", "name": "A", "text": "craft line"},
        {"role": "muse", "kind": "banter", "name": "B", "text": "heckle line"},
        {"role": "user", "kind": "user", "name": "総監督", "text": "user line"},
    ]}
    craft_only = service._recent_talk(session, kinds=("craft",))
    everything = service._recent_talk(session)

    assert "craft line" in craft_only
    assert "heckle line" not in craft_only
    assert "heckle line" in everything
    assert "user line" not in everything


@pytest.mark.asyncio
async def test_only_the_acting_seats_read_her_inner_life():
    db = FakeDb()
    session = await _ready_session(db)
    session["character"]["personality"] = {
        "traits": ["quiet"], "inner": ["a private thing she never says"],
        "likes": ["a thing she likes"],
    }
    service._rebuild_brief(session)

    acting = service._brief_for(session, "actress:cast")
    lighting = service._brief_for(session, "gaffer:gyakkou")
    assert "a private thing she never says" in acting
    assert "a private thing she never says" not in lighting
    assert "quiet" in lighting


@pytest.mark.asyncio
async def test_a_blind_model_is_reported_rather_than_silently_degraded(board_file):
    class BlindOllama(PlanningOllama):
        def generate_vlm_stream(self, prompt, images, **kw):
            self.calls.append({**kw, "prompt": prompt, "images": images})

            async def _empty():
                yield {"type": "token", "text": "  "}
            return _empty()

    db, spooler, ollama = SeeingDb(board_file), FakeSpooler(), BlindOllama()
    session = await _ready_session(db, crew_preset="trio", banter_mode="off")
    session = await service.start_table(db, ollama, session)
    session["board"] = {
        "seed": 7, "round": 1, "pending": False,
        "images": [{"index": 0, "image_id": "sha0"}],
    }
    await session_db.save(db, session)

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "背景が違う",
    )

    said = " ".join(m["text"] for m in session["chat"] if m["role"] == "system")
    assert "絵を読めない" in said or "could not read" in said
    assert session["craft"]["prompt"], "the table kept moving"


def test_the_small_room_is_the_lead_the_director_and_the_planner():
    from app.muse import crew
    roles = [crew.role_of(i) for i in crew.resolve_crew(preset="trio")]
    assert roles == ["plan", "beat", "actress", "finisher"]
    assert crew.role_of(crew.resolve_crew(preset="quartet")[2]) == "lens"


# ── the ledger ──────────────────────────────────────────────────────────────
def test_the_ledger_names_the_seat_that_added_each_tag():
    """A run ended in `(neck_tension:1.4)`, a school blazer and an extreme
    close-up, and the only way to find out who asked for them was to guess from
    the chat. The session kept the final craft and nothing else."""
    session = {"session_id": "s", "inputs": {"locale": "ja"}}
    first = service.record_ledger(
        session, muse_id="spine:bane", name="振付「バネ」",
        before="", after="singing, weight_shift, (neck_tension:1.4)",
    )
    assert first["added"] == ["singing", "weight_shift", "neck_tension"]
    assert first["dropped"] == []

    second = service.record_ledger(
        session, muse_id="lens:pinto", name="撮影「ピント」",
        before="singing, weight_shift, (neck_tension:1.4)",
        after="singing, (neck_tension:1.35), extreme_close-up, macro_lens",
    )
    # Emphasis is not a different tag — re-weighting is neither an add nor a drop.
    assert second["added"] == ["extreme_close-up", "macro_lens"]
    assert second["dropped"] == ["weight_shift"]
    assert [e["muse_id"] for e in session["ledger"]] == ["spine:bane", "lens:pinto"]


def test_a_seat_that_changed_nothing_leaves_no_ledger_row():
    session = {"session_id": "s", "inputs": {}}
    assert service.record_ledger(
        session, muse_id="gate:mon", name="監修「門」",
        before="a, b", after="b, a",
    ) is None
    assert not session.get("ledger")


# ── the plan takes its own props back ───────────────────────────────────────
def _relocating_session(craft_tags: str) -> dict:
    return {
        "session_id": "s",
        "inputs": {"locale": "ja", "crew_preset": "standard", "framing": "auto"},
        "character": {"identity_tags": ["silver_hair"]},
        "craft": {"tags": craft_tags, "scene": "She sings.", "prompt": ""},
        "plan": {},
    }


def test_moving_the_shoot_takes_the_old_places_props_with_it():
    """CARRY tells every seat to KEEP setting objects once they exist, which is a
    ratchet with no release. A note that moved the shoot left the old location's
    props in the craft and the Showrunner cleared them out by hand every time."""
    session = _relocating_session(
        "singing, microphone_stand, stage_monitor, amplifier, drum_kit, "
        "wireless_microphone, lyric_monitor, spilled_cola, crumpled_napkin",
    )
    session["plan"] = {
        "place": "karaoke booth",
        "must_appear": ["wireless_microphone", "lyric_monitor", "small_table"],
    }
    struck = service.strike_dropped_props(session, {
        "place": "live house stage",
        "must_appear": [
            "microphone", "microphone_stand", "stage_monitor", "amplifier",
            "drum_kit", "cables",
        ],
    })

    assert set(struck) == {
        "microphone_stand", "stage_monitor", "amplifier", "drum_kit", "cables",
    }
    tags = session["craft"]["tags"]
    for gone in ("microphone_stand", "stage_monitor", "amplifier", "drum_kit"):
        assert gone not in tags, tags
    # "microphone" survives as `wireless_microphone` — the planner got more
    # specific, it did not throw the microphone away.
    assert "wireless_microphone" in tags
    # The art department's floor dressing is not on the ledger and is the part
    # of the picture that works. It stays.
    assert "spilled_cola" in tags and "crumpled_napkin" in tags
    # The seats that write next have to clear the prose too.
    assert session["struck"] == struck
    assert session["ledger"][-1]["dropped"]


def test_a_plan_that_only_gains_props_strikes_nothing():
    session = _relocating_session("singing, tambourine")
    session["plan"] = {"must_appear": ["tambourine", "plastic_cup"]}
    assert service.strike_dropped_props(session, {"must_appear": ["tambourine"]}) == []
    assert "tambourine" in session["craft"]["tags"]
    assert "struck" not in session


def test_the_first_plan_of_a_session_strikes_nothing():
    session = _relocating_session("singing")
    session["plan"] = {"must_appear": ["microphone"]}
    assert service.strike_dropped_props(session, {}) == []


# ── the Lead talks more, and differently each time ──────────────────────────
def test_the_lead_gets_a_real_share_of_the_heckles():
    """A full eighteen-seat session gave her three lines. `previous` — whoever
    happened to speak last — took nearly every slot, and she sat third in the
    fallback list that ran when it did not."""
    cast = crew.resolve_crew(preset="standard")
    lead = crew.DEFAULT_MEMBER["actress"]
    session = {"session_id": "s", "inputs": {"banter_mode": "light"}}

    picks = []
    previous = None
    for i, mid in enumerate(cast):
        if crew.role_of(mid) in ("plan", *crew.BANTER_ONLY):
            continue
        picks.append(service._pick_banter_reactor(
            session, cast, current=mid, previous=previous, index=i,
        ))
        previous = mid

    spoke = [p for p in picks if p]
    assert spoke, "light mode must still produce banter"
    assert picks.count(lead) >= 3, picks
    # And it is not only her — the table still argues with itself.
    assert len({p for p in spoke}) >= 3, spoke


def test_the_lead_is_never_asked_to_heckle_herself():
    cast = crew.resolve_crew(preset="standard")
    lead = crew.DEFAULT_MEMBER["actress"]
    session = {"session_id": "s", "inputs": {"banter_mode": "light"}}
    for i in range(len(cast)):
        assert service._pick_banter_reactor(
            session, cast, current=lead, previous=None, index=i,
        ) != lead


def test_the_lead_is_handed_a_different_move_each_time_she_speaks():
    session = {
        "session_id": "s", "inputs": {"locale": "ja"},
        "character": {"name_ja": "みお"},
        "chat": [],
    }
    lead = crew.DEFAULT_MEMBER["actress"]
    seen = []
    for _ in range(4):
        text = service._banter_prompt(
            session, speaker_id=lead, about_id="lens:pinto", about_text="寄ります",
        )
        stance = text.split("今回の返し方: ")[1].split("\n")[0]
        seen.append(stance)
        session["chat"].append(
            {"muse_id": lead, "kind": "banter", "text": "…", "role": "muse"},
        )
    assert len(set(seen)) == 4, seen
    assert all(s in crew.ACTRESS_STANCES for s in seen)


def test_a_staff_seat_gets_no_stance_line():
    session = {"session_id": "s", "inputs": {"locale": "ja"}, "chat": []}
    text = service._banter_prompt(
        session, speaker_id="hook:kugizuke", about_id="lens:pinto", about_text="寄ります",
    )
    assert "今回の返し方" not in text


# ── act one: three seats and a still ────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_table_opens_on_three_seats_and_shoots_a_still():
    """Eighteen seats talking with no picture anywhere is how「カラオケボックスで
    歌っている」became a live house: twenty turns of prose agreeing with each
    other, and the Showrunner then deleting props for the rest of the session."""
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db, crew_preset="standard")

    session = await service.start_table(
        db, ollama, session, comfy=FakeComfy(), spooler=spooler,
    )

    assert session["table_stage"] == "brief"
    spoke = {m["muse_id"] for m in session["chat"] if m.get("kind") == "craft"}
    roles = {crew.role_of(m) for m in spoke}
    # Wardrobe joined the opening (it dresses her before the camera frames her).
    assert roles <= {"plan", "wardrobe", "actress", "lens"}, roles
    assert {"wardrobe", "actress", "lens"} <= roles
    # And a still is on the way, as one frame rather than four.
    assert len(spooler.jobs) == 1
    assert spooler.jobs[0]["func"] is runner.run_board_job
    assert session["board"]["still"] is True
    assert session["status"] == "boarding"


@pytest.mark.asyncio
async def test_with_no_renderer_the_whole_table_still_meets_at_once():
    """Legacy callers and tests hand no comfy/spooler. There is no still to wait
    for, so waiting for one would hang the read-through forever."""
    db, ollama = FakeDb(), FakeOllama()
    session = await _ready_session(db, crew_preset="standard")

    session = await service.start_table(db, ollama, session)

    assert session["table_stage"] == "full"
    assert session["status"] == "chat"
    roles = {
        crew.role_of(m["muse_id"]) for m in session["chat"]
        if m.get("kind") == "craft"
    }
    assert "gaffer" in roles and "finisher" in roles


@pytest.mark.asyncio
async def test_the_first_note_after_the_still_convenes_the_rest_of_the_crew():
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db, crew_preset="standard")
    session = await service.start_table(
        db, ollama, session, comfy=FakeComfy(), spooler=spooler,
    )
    session["board"]["pending"] = False
    await session_db.save(db, session)

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session,
        "もっと狭い部屋で、一人カラオケの感じにして",
    )

    assert session["table_stage"] == "full"
    roles = {
        crew.role_of(m["muse_id"]) for m in session["chat"]
        if m.get("kind") == "craft"
    }
    assert "gaffer" in roles and "finisher" in roles
    # The note is standing direction from here on, not a remark about one turn.
    assert "もっと狭い部屋で、一人カラオケの感じにして" in session["notes"]
    # It did not sneak a second render in on the way.
    assert len(spooler.jobs) == 1
    assert session["status"] == "chat"


@pytest.mark.asyncio
async def test_ok_straight_off_the_still_gathers_the_crew_before_shooting():
    """Otherwise a bare OK ships a prompt three seats wrote."""
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db, crew_preset="standard")
    session = await service.start_table(
        db, ollama, session, comfy=FakeComfy(), spooler=spooler,
    )
    session["board"]["pending"] = False
    await session_db.save(db, session)

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "OK",
    )

    assert session["table_stage"] == "full"
    assert session["status"] == "shooting"
    assert [j["func"] for j in spooler.jobs][-1] is runner.run_shoot_job
    # "OK" is not creative direction and must not become a standing order.
    assert "OK" not in (session.get("notes") or [])


@pytest.mark.asyncio
async def test_the_full_table_only_ever_meets_once():
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db, crew_preset="standard")
    session = await service.start_table(
        db, ollama, session, comfy=FakeComfy(), spooler=spooler,
    )
    session["board"]["pending"] = False
    await session_db.save(db, session)

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "青い照明にして",
    )
    first = len([m for m in session["chat"] if m.get("kind") == "craft"])
    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session, "もう少し明るく",
    )
    second = len([m for m in session["chat"] if m.get("kind") == "craft"])

    # The second note goes to the short responder desk, not another full read.
    assert second - first <= 6, (first, second)


@pytest.mark.asyncio
async def test_a_planner_that_omits_the_ledger_does_not_empty_the_room():
    """Observed on a real run. The planner answered PLACE / HOUR / LIGHT /
    ACTION and no MUST APPEAR, which is a line it did not retype — not a room
    that has been emptied. Read as an empty ledger it struck all twelve props
    from a karaoke booth, including the wireless microphone the Showrunner had
    asked for by name in the very note that triggered the re-plan."""
    db = FakeDb()

    class TerseplanOllama(FakeOllama):
        def generate_text_stream(self, prompt, **kw):
            self.calls.append(kw)

            async def _stream():
                yield {"type": "token", "text": (
                    "SAY: 場所は変えずに中身を整理します。\n"
                    "PLACE: A private karaoke booth, leaning over a low table.\n"
                    "HOUR: 2:00 AM, mid-winter.\n"
                    "LIGHT: Dim; blue glow from the monitor.\n"
                    "ACTION: Singing into a wireless microphone.\n"
                )}
            return _stream()

    ollama = TerseplanOllama()
    session = await _ready_session(db, banter_mode="off")
    session["plan"] = {
        "place": "A private karaoke booth.",
        "must_appear": ["wireless_microphone", "karaoke_monitor", "tambourine"],
    }
    session["craft"] = {
        "prompt": "1girl, singing", "scene": "She sings.",
        "tags": "singing, wireless_microphone, karaoke_monitor, tambourine",
        "pose_intent": "",
    }
    await session_db.save(db, session)

    await service._run_plan_turn(
        db, ollama, session, cfg={}, note="マイクはワイヤレスのハンドマイクだよ",
    )

    assert session["plan"]["must_appear"] == [
        "wireless_microphone", "karaoke_monitor", "tambourine",
    ]
    assert not session.get("struck")
    assert "wireless_microphone" in session["craft"]["tags"]


# ── casting stays editable ──────────────────────────────────────────────────
def test_a_seat_cast_after_the_read_through_gets_a_pass_before_it_opines():
    """The drawer used to freeze the moment the table opened. Now that
    「今日は照明いいや」works mid-session, so does bringing lighting back — and it
    has never read the script."""
    cast = crew.resolve_crew(preset="standard")
    session = {
        "session_id": "s", "inputs": {},
        "spoken": [m for m in cast if crew.role_of(m) != "gaffer"],
    }
    fresh = service.newcomers(session, cast)
    assert fresh == [crew.DEFAULT_MEMBER["gaffer"]]

    # And it leads the responder desk, because a seat cast halfway through is
    # usually cast because of the note that follows.
    responders = fresh + [
        m for m in service._pick_responders("照明を戻して", cast) if m not in fresh
    ]
    assert responders[0] == crew.DEFAULT_MEMBER["gaffer"]


def test_a_crew_that_has_all_spoken_has_nobody_catching_up():
    cast = crew.resolve_crew(preset="standard")
    session = {"session_id": "s", "inputs": {}, "spoken": list(cast)}
    assert service.newcomers(session, cast) == []


def test_swapping_a_whole_preset_mid_session_does_not_queue_a_dozen_turns():
    cast = crew.resolve_crew(preset="everyone")
    session = {"session_id": "s", "inputs": {}, "spoken": []}
    assert len(service.newcomers(session, cast)) == service.MAX_CATCHUP


@pytest.mark.asyncio
async def test_writing_craft_is_what_marks_a_seat_as_having_spoken():
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db, crew_preset="standard")
    session = await service.start_table(
        db, ollama, session, comfy=FakeComfy(), spooler=spooler,
    )
    spoken = {crew.role_of(m) for m in session["spoken"]}
    assert spoken == {"wardrobe", "actress", "lens"}, spoken
    # The planner does not write craft, so it is not on the list and never
    # queues itself for a catch-up pass.
    assert "plan" not in spoken


@pytest.mark.asyncio
async def test_the_planner_no_longer_keeps_a_clothing_line():
    """The planner used to carry a WEARING line forward; clothes are Wardrobe's
    now, so a stray one is dropped while the room's ledger is still kept."""
    db = FakeDb()

    class TersePlan(FakeOllama):
        def generate_text_stream(self, prompt, **kw):
            self.calls.append(kw)

            async def _stream():
                yield {"type": "token", "text": (
                    "PLACE: A quiet room.\nHOUR: Evening.\nLIGHT: One lamp.\n"
                    "ACTION: Sitting.\nMUST APPEAR: lamp, chair, rug\n"
                )}
            return _stream()

    session = await _ready_session(db, banter_mode="off")
    session["plan"] = {"place": "A quiet room", "wearing": "what the theme asked for",
                       "must_appear": ["lamp", "chair"]}
    session["craft"] = {"tags": "sitting", "scene": "x", "prompt": "", "pose_intent": ""}
    await session_db.save(db, session)

    await service._run_plan_turn(db, TersePlan(), session, cfg={})
    assert "wearing" not in session["plan"]  # dropped, not carried forward
    # The room ledger is still settled (that carry-forward stays).
    assert session["plan"]["must_appear"] == ["lamp", "chair", "rug"]
