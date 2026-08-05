"""What the two steps actually submit, and what they do with what comes back.

These are the wiring facts that no unit test above covers and that cost real
generations to get wrong: the drafts are one batched job rather than four, the
model is dropped from VRAM before the render, each chosen draft becomes its own
chain seeded from the draft's own seed, and a workflow that ends in an upscale
does not get its finished picture thrown away.
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
            yield {"type": "token", "text": "TAGS: standing, rooftop\n\nSCENE: STAGE A PROMPT"}
        return _stream()

    async def unload(self, model=None):
        self.unloaded.append(model)


class FakeDb:
    """Just enough of the Qdrant wrapper for session save/load."""
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
    # Patched through the module object rather than by dotted string: another
    # suite stubs `app.runtime_config` in sys.modules, and resolving the name
    # again picks up that stub instead of what this module imported.
    monkeypatch.setattr(service, "get_runtime_config", _cfg)


async def _ready_session(db):
    session = await service.create_session(db, {
        "theme": "on a rooftop", "character_id": "c1",
        "workflow": "w.json", "model": "m",
    })
    session["character"] = {"identity_tags": ["1girl", "blue_hair"],
                            "personality": {}, "palette": [], "signature_prop": ""}
    service._rebuild_brief(session)
    await session_db.save(db, session)
    return session


@pytest.mark.asyncio
async def test_draft_submits_one_job_and_frees_the_card_first():
    db, spooler, ollama = FakeDb(), FakeSpooler(), FakeOllama()
    session = await _ready_session(db)

    session = await service.run_draft(db, ollama, FakeComfy(), spooler, session)

    assert len(spooler.jobs) == 1, "every draft comes from one batched render"
    job = spooler.jobs[0]
    assert job["func"] is runner.run_draft_job
    # The runner reads the prompt and the seed back out of the session, so there
    # is one copy of what is being drawn rather than two that can disagree.
    assert job["session_id"] == session["session_id"]
    # A 26B model and a multi-image latent do not fit on one 16GB card.
    assert ollama.unloaded == ["m"]

    assert "STAGE A PROMPT" in session["draft"]["prompt"]
    assert "blue_hair" in session["draft"]["prompt"]  # identity lock stapled on
    assert session["draft"]["pose_intent"]
    assert session["draft"]["pending"] is True
    assert session["draft"]["seed"] > 0
    assert session["draft"]["job_id"]


def test_the_workflows_last_image_is_the_one_worth_keeping():
    # Workflows here often end in an upscale or a detailer, which is a second
    # output node. Taking the first sha kept the raw sampler output and dropped
    # the finished picture the graph went on to write.
    assert runner.finished_image(["raw", "upscaled"]) == "upscaled"
    assert runner.finished_image(["only"]) == "only"


@pytest.mark.asyncio
async def test_draft_refuses_to_run_with_anything_missing():
    db = FakeDb()
    session = await service.create_session(db, {"theme": "t"})
    with pytest.raises(service.MuseError) as err:
        await service.run_draft(db, FakeOllama(), FakeComfy(), FakeSpooler(), session)
    assert "character" in str(err.value)


@pytest.mark.asyncio
async def test_each_chosen_draft_becomes_its_own_chain_at_the_draft_seed():
    db, spooler = FakeDb(), FakeSpooler()
    session = await _ready_session(db)
    session["draft"] = {
        "seed": 4242, "job_id": "job-1",
        "prompt": "TAGS: standing\n\nSCENE: She waits on the roof.",
        "pose_intent": "She waits on the roof.",
        "images": [
            {"index": 0, "image_id": "sha0"}, {"index": 1, "image_id": "sha1"},
            {"index": 2, "image_id": ""},
        ],
    }
    await session_db.save(db, session)

    session = await service.run_refine(
        db, FakeOllama(), FakeComfy(), spooler, session, [1, 0, 2],
    )

    # Index 2 has not landed yet, so it cannot be sent on.
    assert session["selected"] == [1, 0]
    assert [c["source_image_id"] for c in session["chains"]] == ["sha1", "sha0"]
    assert all(c["seed"] == 4242 for c in session["chains"])
    # Same seed and same canvas throughout: the only thing that changes between
    # a draft and its stages is the prompt. Default refine_stages is B+C.
    assert [s["stage"] for s in session["chains"][0]["stages"]] == [
        "reinforce", "cinematic",
    ]
    assert session["chains"][0]["pose_intent"] == "She waits on the roof."
    assert len(spooler.jobs) == 2
    assert {j["chain_index"] for j in spooler.jobs} == {0, 1}


@pytest.mark.asyncio
async def test_refine_needs_a_draft_that_finished():
    db = FakeDb()
    session = await _ready_session(db)
    session["draft"] = {"images": [{"index": 0, "image_id": ""}]}
    with pytest.raises(service.MuseError):
        await service.run_refine(db, FakeOllama(), FakeComfy(), FakeSpooler(),
                                 session, [0])


@pytest.mark.asyncio
async def test_cancelling_a_draft_clears_it_so_stage_a_can_be_run_again():
    db, spooler = FakeDb(), FakeSpooler()
    session = await _ready_session(db)
    session["draft"] = {"job_id": "job-1", "images": [], "pending": True}
    session["chains"] = [{"stages": []}]

    session = await service.cancel_draft(db, spooler, session)

    assert spooler.cancelled == ["job-1"]
    assert session["draft"] == {}
    assert session["chains"] == []
    assert session["status"] == "draft"


@pytest.mark.asyncio
async def test_images_keep_arriving_until_the_job_says_it_has_stopped():
    db = FakeDb()
    session = await _ready_session(db)
    session["draft"] = {"job_id": "job-1", "seed": 7, "images": [], "pending": True}
    await session_db.save(db, session)
    sid = session["session_id"]

    for sha in ("a", "b", "c", "d", "e"):
        await session_db.attach_draft_image(db, sid, sha, {"seed": 7})
    s = await session_db.load(db, sid)
    # Five images from a batch of four is a workflow with two output nodes, not
    # an error — and it is still running as far as anyone here knows.
    assert len(s["draft"]["images"]) == 5
    assert s["draft"]["pending"] is True

    await session_db.finish_draft(db, sid)
    s = await session_db.load(db, sid)
    assert s["draft"]["pending"] is False
    assert s["status"] == "drafted"


@pytest.mark.asyncio
async def test_finishing_does_not_resurrect_a_draft_that_was_cancelled():
    # cancel_draft clears the draft; the job then unwinds and reports in. Putting
    # an empty draft back would leave the panel with a finished step and no
    # pictures, and no way to press the button again.
    db, spooler = FakeDb(), FakeSpooler()
    session = await _ready_session(db)
    session["draft"] = {"job_id": "job-1", "images": [], "pending": True}
    session = await service.cancel_draft(db, spooler, session)

    await session_db.finish_draft(db, session["session_id"], error="cancelled")
    s = await session_db.load(db, session["session_id"])
    assert s["draft"] == {}
    assert s["status"] == "draft"


@pytest.mark.asyncio
async def test_refine_stages_can_stop_early_but_not_go_further():
    db, spooler = FakeDb(), FakeSpooler()
    session = await _ready_session(db)
    session["inputs"]["refine_stages"] = 2
    session["draft"] = {"seed": 1, "images": [{"index": 0, "image_id": "sha0"}]}
    session = await service.run_refine(db, FakeOllama(), FakeComfy(), spooler,
                                       session, [0])
    assert [s["stage"] for s in session["chains"][0]["stages"]] == [
        "reinforce", "cinematic",
    ]
