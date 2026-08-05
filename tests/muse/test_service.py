"""Chat studio wiring: table → note → board → OK → shoot.

BCD refine is gone. The showrunner chats; the crew revises craft; a board asks
「これでいい？」; OK submits the final shoot.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import runner, service, session_db


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
                    "SAY: Director, the beat is waiting on the roof.\n\n"
                    "TAGS: standing, rooftop\n\n"
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
        "theme": "on a rooftop", "character_id": "c1",
        "workflow": "w.json", "model": "m",
        "crew_preset": "lightning",
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
    assert len(ollama.calls) >= 2  # lightning crew + actress + finisher


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
    assert ollama.unloaded == []


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


def test_pick_responders_routes_outfit_notes_to_wardrobe():
    crew_ids = ["beat", "spine", "lens", "wardrobe", "gaffer", "actress", "finisher"]
    got = service._pick_responders("服をコートにして", crew_ids)
    assert "wardrobe" in got
    assert got[-1] == "finisher"


def test_pick_responders_routes_charm_notes_to_actress():
    crew_ids = ["beat", "wardrobe", "faces", "hook", "actress", "finisher"]
    got = service._pick_responders("もっと可愛く、魅力出して", crew_ids)
    assert "actress" in got
    assert "faces" in got or "hook" in got
    assert got[-1] == "finisher"
    assert len(got) <= 5  # cap craft + finisher for Ollama


@pytest.mark.asyncio
async def test_showrunner_comment_reruns_a_short_turn():
    """The loop the Showrunner is testing: note → specialists revise → chat again."""
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db, banter_mode="off")  # craft-only for speed
    session = await service.start_table(db, ollama, session)
    before = session["craft"]["prompt"]
    n_chat = len(session["chat"])

    session = await service.post_chat(
        db, ollama, FakeComfy(), spooler, session,
        "もっと可愛くして。ビキニにして魅力出して",
    )

    assert session["status"] == "chat"
    assert spooler.jobs == []  # comment is LLM turns, not Comfy
    assert any(m["role"] == "user" and "ビキニ" in m["text"] for m in session["chat"])
    spoken = [m.get("muse_id") for m in session["chat"][n_chat:] if m.get("role") == "muse"]
    assert "wardrobe" in spoken or "actress" in spoken
    assert "finisher" in spoken
    assert len(session["chat"]) > n_chat
    # Craft was touched by at least one responder (FakeOllama always rewrites).
    assert session["craft"]["prompt"]


@pytest.mark.asyncio
async def test_light_banter_mode_fires_fewer_side_calls_than_full():
    db, ollama_light, ollama_full = FakeDb(), FakeOllama(), FakeOllama()
    s_light = await _ready_session(db, banter_mode="light", crew_preset="lightning")
    s_light = await service.start_table(db, ollama_light, s_light)
    light_banter = sum(1 for m in s_light["chat"] if m.get("kind") == "banter")

    db2 = FakeDb()
    s_full = await _ready_session(db2, banter_mode="full", crew_preset="lightning")
    s_full = await service.start_table(db2, ollama_full, s_full)
    full_banter = sum(1 for m in s_full["chat"] if m.get("kind") == "banter")

    assert light_banter < full_banter
    assert light_banter >= 1
