"""The five acts: brief, look, ask, tighten, shoot.

What these pin is the shape, not the wording. The version this replaced ran
eighteen seats to completion before the Showrunner saw anything — 513 seconds,
and if the direction was wrong you learned that eight minutes late — while the
seats argued about a picture none of them had been shown.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

import pytest

from app.muse import probe, runner, service, session_db

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = (FIXTURES / "board_ok.jpg").read_bytes()
VOID = (FIXTURES / "board_void.jpg").read_bytes()

PLAN = (
    "SAY: 場所と時間を決めます。総監督、これで進めます。\n"
    "PLACE: a corner seat by a tall window\n"
    "HOUR: mid-afternoon\n"
    "LIGHT: even daylight, normal exposure\n"
    "ACTION: resting\n"
    "MUST APPEAR: wooden table, chair, glass mug, napkin\n"
)
ACTRESS = (
    "SAY: このくらいの姿勢が自然だと思います。\n"
    "SUBJECT: a girl at a table\nPOSE: chin on hand\nMOOD: quietly pleased\n"
)
ENRICH = (
    "SAY: カメラを決めます。寄ると手元の温度が出ます。\n"
    "WARDROBE: grey cardigan\nCAMERA: medium shot\nOBJECTS: spoon\n"
)
REDUCE = "SAY: ひとつ切ります。\nREMOVE: spoon\n"
CHECK = "SAY: 見ました。\nREMOVE: deep shadows\nADD: soft daylight\n"

ANSWERS = {"構成": PLAN, "主演": ACTRESS, "加筆": ENRICH, "整理": REDUCE, "試写": CHECK}


class FakeOllama:
    def __init__(self):
        self.unloaded: list[str] = []
        self.calls: list[dict] = []

    def _reply_for(self, system: str) -> str:
        for marker, answer in ANSWERS.items():
            if marker in system:
                return answer
        return PLAN

    def _stream(self, system):
        async def _gen():
            yield {"type": "token", "text": self._reply_for(system)}
        return _gen()

    def generate_text_stream(self, prompt, **kw):
        self.calls.append({"kind": "text", "prompt": prompt, **kw})
        return self._stream(kw.get("system", ""))

    def generate_vlm_stream(self, prompt, images, **kw):
        self.calls.append({"kind": "vlm", "prompt": prompt, "images": images, **kw})
        return self._stream(kw.get("system", ""))

    async def unload(self, model=None):
        self.unloaded.append(model)


class FakeComfy:
    """Renders whatever `frame` is set to, and records what it was asked for."""

    def __init__(self, frame: bytes = GOOD):
        self.frame = frame
        self.rendered: list[dict] = []

    def load_workflow(self, name):
        return {}

    def patchable_fields(self, wf):
        return {"steps": 1, "cfg": 1, "width": 1, "height": 1}

    def patch_workflow(self, wf, pos, neg, *a, **kw):
        self.rendered.append({"positive": pos, "negative": neg, **kw})
        return {}

    async def queue_prompt(self, wf, preview=False):
        return "pid"

    async def stream_progress(self, pid):
        yield {"type": "comfy_output",
               "images": [{"filename": "p.png", "subfolder": "", "type": "output"}]}

    async def fetch_history(self, pid):
        return []

    async def fetch_image(self, filename, subfolder="", type_="output"):
        return self.frame


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
def _stub_environment(monkeypatch):
    async def _cfg(db):
        return {"ollama_num_ctx": 16000}
    monkeypatch.setattr(service, "get_runtime_config", _cfg)

    # WD14 is a real model load; the ledger check is exercised in test_probe.
    async def _tags(db, data, session):
        return ["wooden table", "chair", "glass mug", "napkin"]
    monkeypatch.setattr(service, "_wd14", _tags)


async def _ready(db, **over):
    session = await service.create_session(db, {
        "theme": "a quiet indoor moment", "character_id": "c1",
        "workflow": "w.json", "model": "m", **over,
    })
    session["character"] = {
        "identity_tags": ["navy_hair"], "personality": {"traits": ["quiet"]},
        "palette": [], "signature_prop": "", "subject_tag": "1girl",
    }
    service._rebuild_brief(session)
    await session_db.save(db, session)
    return session


# ── Act 1 + 2 ───────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_brief_is_two_seats_and_two_probes_before_anyone_is_asked():
    """Not eighteen seats and eight minutes."""
    db, llm, comfy = FakeDb(), FakeOllama(), FakeComfy()
    session = await _ready(db)

    session = await service.start_table(db, llm, session, comfy=comfy)

    spoke = [m["muse_id"] for m in session["chat"] if m["role"] == "muse"]
    assert spoke[:2] == ["plan", "actress"]
    assert "enrich" not in spoke and "reduce" not in spoke
    # Two probes: her on white, the room with nobody in it.
    assert len(comfy.rendered) == 2
    assert set(session["probes"]) == {probe.POSE, probe.SETTING}
    assert session["status"] == "chat"


@pytest.mark.asyncio
async def test_the_two_probes_are_actually_separated():
    db, llm, comfy = FakeDb(), FakeOllama(), FakeComfy()
    session = await _ready(db)
    await service.start_table(db, llm, session, comfy=comfy)

    pose, setting = (r["positive"].lower() for r in comfy.rendered)
    assert "white background" in pose and "chin on hand" in pose
    assert "wooden table" not in pose
    assert "no humans" in setting and "wooden table" in setting
    assert "chin on hand" not in setting and "navy_hair" not in setting


@pytest.mark.asyncio
async def test_the_probe_seed_is_fixed_so_rounds_are_comparable():
    """If it moved between rounds there would be no telling whether a change
    helped or the dice did."""
    db, llm, comfy = FakeDb(), FakeOllama(), FakeComfy()
    session = await _ready(db)
    await service.start_table(db, llm, session, comfy=comfy)
    seeds = {r["seed"] for r in comfy.rendered}
    assert len(seeds) == 1
    first = seeds.pop()

    await service.post_chat(db, llm, comfy, FakeSpooler(), session, "もっと明るく")
    assert {r["seed"] for r in comfy.rendered} == {first}


@pytest.mark.asyncio
async def test_probe_bytes_never_reach_the_saved_session():
    """The session is serialised into Qdrant; a few hundred KB of PNG per round
    has no business in the document store."""
    db, llm, comfy = FakeDb(), FakeOllama(), FakeComfy()
    session = await _ready(db)
    await service.start_table(db, llm, session, comfy=comfy)
    saved = db.rows[session["session_id"]]
    assert not any(isinstance(v, bytes) for v in saved.values())
    assert "_probe_bytes" not in saved
    # But the crew can still see them.
    assert service._frames(session)


@pytest.mark.asyncio
async def test_a_probe_never_goes_through_the_spooler():
    """Forty throwaway 512s in the image library is worse than no probe."""
    db, llm, comfy, spooler = FakeDb(), FakeOllama(), FakeComfy(), FakeSpooler()
    session = await _ready(db)
    await service.start_table(db, llm, session, comfy=comfy)
    assert spooler.jobs == []
    assert comfy.rendered, "but it did render"


# ── Act 3 ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_note_becomes_standing_direction_and_re_settles_one_place():
    db, llm, comfy = FakeDb(), FakeOllama(), FakeComfy()
    session = await _ready(db)
    session = await service.start_table(db, llm, session, comfy=comfy)

    session = await service.post_chat(
        db, llm, comfy, FakeSpooler(), session, "屋内にして、椅子に座らせて",
    )
    assert session["notes"] == ["屋内にして、椅子に座らせて"]
    assert "屋内にして" in session["brief"]
    # One place, not two.
    assert session["shot"]["place"] == "a corner seat by a tall window"
    assert session["status"] == "chat"


@pytest.mark.asyncio
async def test_a_note_does_not_start_the_shoot():
    db, llm, comfy, spooler = FakeDb(), FakeOllama(), FakeComfy(), FakeSpooler()
    session = await _ready(db)
    session = await service.start_table(db, llm, session, comfy=comfy)
    await service.post_chat(db, llm, comfy, spooler, session, "もう少し寄って")
    assert spooler.jobs == []


# ── Act 4 ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_ok_tightens_first_and_only_shoots_once_a_board_exists():
    db, llm, comfy, spooler = FakeDb(), FakeOllama(), FakeComfy(), FakeSpooler()
    session = await _ready(db)
    session = await service.start_table(db, llm, session, comfy=comfy)

    session = await service.post_chat(db, llm, comfy, spooler, session, "OK")
    assert [j["func"] for j in spooler.jobs] == [runner.run_board_job]
    spoke = [m["muse_id"] for m in session["chat"] if m["role"] == "muse"]
    assert "enrich" in spoke and "reduce" in spoke

    session["board"]["images"] = [{"image_id": "sha0"}]
    session["board"]["pending"] = False
    session = await service.post_chat(db, llm, comfy, spooler, session, "OK")
    assert spooler.jobs[-1]["func"] is runner.run_shoot_job


@pytest.mark.asyncio
async def test_a_passing_probe_stops_the_loop_early():
    db, llm, comfy, spooler = FakeDb(), FakeOllama(), FakeComfy(GOOD), FakeSpooler()
    session = await _ready(db)
    session = await service.start_table(db, llm, session, comfy=comfy)
    before = len(comfy.rendered)

    session = await service.refine(db, llm, comfy, spooler, session)
    # One merged probe, then out: the numbers passed.
    assert len(comfy.rendered) - before == 1
    assert session["craft"]["round"] == 1


@pytest.mark.asyncio
async def test_a_failing_probe_runs_the_cap_and_says_what_it_could_not_fix():
    """Never ship a quiet failure."""
    db, llm, comfy, spooler = FakeDb(), FakeOllama(), FakeComfy(VOID), FakeSpooler()
    session = await _ready(db, probe_max_rounds=2)
    session = await service.start_table(db, llm, session, comfy=comfy)

    session = await service.refine(db, llm, comfy, spooler, session)
    assert session["craft"]["round"] == 2
    said = " ".join(m["text"] for m in session["chat"] if m["role"] == "system")
    assert "直りませんでした" in said
    assert "empty black" in said or "too dark" in said
    # And it still put the board up rather than stalling.
    assert spooler.jobs[-1]["func"] is runner.run_board_job


@pytest.mark.asyncio
async def test_the_checker_is_handed_the_measurements_and_the_frame():
    db, llm, comfy, spooler = FakeDb(), FakeOllama(), FakeComfy(VOID), FakeSpooler()
    session = await _ready(db, probe_max_rounds=1)
    session = await service.start_table(db, llm, session, comfy=comfy)
    llm.calls.clear()
    await service.refine(db, llm, comfy, spooler, session)

    seeing = [c for c in llm.calls if c.get("images")]
    assert seeing, "the checker answered without being shown the render"
    assert any("facts, not opinions" in c["prompt"] for c in seeing)
    assert any("VERDICT: FAIL" in c["prompt"] for c in seeing)


# ── wiring that must not regress ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_only_the_lead_is_handed_her_inner_life():
    db = FakeDb()
    session = await _ready(db)
    session["character"]["personality"] = {
        "traits": ["quiet"], "inner": ["a private thing she never says"],
        "likes": ["a thing she likes"],
    }
    service._rebuild_brief(session)
    assert "a private thing she never says" in service._brief_for(session, "actress")
    assert "a private thing she never says" not in service._brief_for(session, "enrich")
    assert "quiet" in service._brief_for(session, "enrich")


@pytest.mark.asyncio
async def test_a_blind_model_is_reported_rather_than_silently_degraded():
    class Blind(FakeOllama):
        def generate_vlm_stream(self, prompt, images, **kw):
            self.calls.append({"kind": "vlm", "prompt": prompt, "images": images})

            async def _empty():
                yield {"type": "token", "text": "  "}
            return _empty()

    db, llm, comfy = FakeDb(), Blind(), FakeComfy()
    session = await _ready(db)
    session = await service.start_table(db, llm, session, comfy=comfy)

    said = " ".join(m["text"] for m in session["chat"] if m["role"] == "system")
    assert "絵を読めない" in said
    assert session["shot"], "the crew kept working"


@pytest.mark.asyncio
async def test_no_comfy_still_briefs_rather_than_failing():
    db, llm = FakeDb(), FakeOllama()
    session = await _ready(db)
    session = await service.start_table(db, llm, session, comfy=None)
    assert session["shot"]["place"]
    assert session["status"] == "chat"


@pytest.mark.asyncio
async def test_the_table_refuses_missing_inputs():
    db = FakeDb()
    session = await service.create_session(db, {"theme": "t"})
    with pytest.raises(service.MuseError) as err:
        await service.start_table(db, FakeOllama(), session)
    assert "character" in str(err.value)


@pytest.mark.asyncio
async def test_cancelling_a_board_clears_it():
    db, spooler = FakeDb(), FakeSpooler()
    session = await _ready(db)
    session["board"] = {"job_id": "job-1", "images": [], "pending": True}
    session = await service.cancel_draft(db, spooler, session)
    assert spooler.cancelled == ["job-1"]
    assert session["board"] == {}


@pytest.mark.asyncio
async def test_unload_vlm_is_opt_in():
    db, llm, comfy, spooler = FakeDb(), FakeOllama(), FakeComfy(), FakeSpooler()
    session = await _ready(db, unload_vlm=True)
    session = await service.start_table(db, llm, session, comfy=comfy)
    llm.unloaded.clear()
    await service.request_board(db, comfy, spooler, session, ollama=llm)
    assert llm.unloaded == ["m"]


@pytest.mark.asyncio
async def test_the_old_refine_chain_is_removed():
    db = FakeDb()
    session = await _ready(db)
    with pytest.raises(service.MuseError) as err:
        await service.run_refine(db, FakeOllama(), FakeComfy(), FakeSpooler(), session, [0])
    assert "廃止" in str(err.value)


def test_is_approve_accepts_natural_ok_phrases():
    assert service._is_approve("OK")
    assert service._is_approve("本番")
    assert service._is_approve("Ok 本番よろしく")
    assert service._is_approve("進めて")
    assert not service._is_approve("OKじゃない、もっと可愛く")
    assert not service._is_approve("ボード出して")
    assert not service._is_approve("服をもっと派手に")


def test_the_workflows_last_image_is_the_one_worth_keeping():
    assert runner.finished_image(["raw", "upscaled"]) == "upscaled"


@pytest.mark.asyncio
async def test_the_model_comes_off_the_card_before_anything_renders():
    """They do not share. Measured: a 26B MoE holds 12.5GB of a 15.6GB card and
    ComfyUI OOMs on what is left. This is the seam where they take turns."""
    db, llm, comfy = FakeDb(), FakeOllama(), FakeComfy()
    session = await _ready(db)
    await service.start_table(db, llm, session, comfy=comfy)
    assert llm.unloaded, "a probe rendered with the LLM still resident"


@pytest.mark.asyncio
async def test_sharing_the_card_is_opt_in_not_the_default():
    db, llm, comfy = FakeDb(), FakeOllama(), FakeComfy()
    session = await _ready(db, unload_vlm=False)
    await service.start_table(db, llm, session, comfy=comfy)
    assert llm.unloaded == []


@pytest.mark.asyncio
async def test_a_probe_holds_the_gpu_the_way_a_render_job_does():
    """Probes are awaited inline rather than submitted, so they have to take the
    generation resource by hand or they can land on the card mid-board."""
    held: list[str] = []

    class Gpu:
        def acquire(self):
            import contextlib

            @contextlib.asynccontextmanager
            async def _ctx():
                held.append("in")
                yield
                held.append("out")
            return _ctx()

    class ResourceSpooler(FakeSpooler):
        def resource_for(self, lane):
            return Gpu()

    db, llm, comfy, spooler = FakeDb(), FakeOllama(), FakeComfy(), ResourceSpooler()
    session = await _ready(db)
    await service.start_table(db, llm, session, comfy=comfy, spooler=spooler)
    assert held == ["in", "out", "in", "out"], held
