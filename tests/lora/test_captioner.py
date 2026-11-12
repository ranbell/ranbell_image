"""The two caption steps and the job that walks a bucket, with the LLM stubbed."""
from __future__ import annotations

import pytest

from app.lora import bucket as bk
from app.lora import captioner
from app.spooler.models import CancelToken, ProgressReporter


class FakeLlm:
    def __init__(self, vlm=None, text=None):
        self.vlm = vlm or (lambda prompt: "A woman with red hair stands in a sunny field.")
        self.text = text or (lambda prompt: "A woman stands in a sunny field.")
        self.calls: list[tuple[str, dict]] = []

    async def generate_vlm_stream(self, prompt, images, **kw):
        self.calls.append(("vlm", kw))
        yield {"type": "think", "text": "hmm"}
        for word in self.vlm(prompt).split(" "):
            yield {"type": "token", "text": word + " "}

    async def generate_text_stream(self, prompt, **kw):
        self.calls.append(("text", kw))
        self.last_text_prompt = prompt
        yield {"type": "token", "text": self.text(prompt)}


class FakeDb:
    def __init__(self, docs):
        self.docs = docs

    async def get(self, sha):
        return self.docs.get(sha)


@pytest.fixture
def store(monkeypatch):
    """Buckets kept in a dict instead of Qdrant."""
    data: dict[str, dict] = {}

    async def load(db, bucket_id):
        b = data.get(bucket_id)
        return None if b is None else __import__("copy").deepcopy(b)

    async def save(db, bucket):
        data[bucket["id"]] = __import__("copy").deepcopy(bucket)
        return bucket

    async def model(db):
        return "test-vlm"

    monkeypatch.setattr(bk, "load", load)
    monkeypatch.setattr(bk, "save", save)
    monkeypatch.setattr(captioner, "_caption_model", model)
    return data


def _reporter():
    from app.spooler.models import Job, JobLane
    return ProgressReporter(Job(id="eval-1", lane=JobLane.EVALUATION, title="t"), lambda *a: None)


def _cancel(set_: bool = False):
    import asyncio
    ev = asyncio.Event()
    if set_:
        ev.set()
    return CancelToken(ev, [])


def test_answer_is_cleaned_to_one_line():
    raw = '<think>x</think>\n**Caption:** "A woman.\n\n- She smiles."'
    assert captioner.clean_answer(raw) == "A woman. She smiles."


def test_describe_prompt_carries_hints_as_recommendations():
    p = captioner.describe_prompt(stored_prompt="1girl, red hair", tags=["smile", "field"])
    assert "Krea 2" in p
    assert "Recommendations, not instructions" in p
    assert "1girl, red hair" in p and "smile, field" in p


def test_strip_prompt_lists_every_target():
    p = captioner.strip_prompt("A woman with red hair.", ["red hair", "freckles"])
    assert "- red hair\n- freckles" in p
    assert p.rstrip().endswith("A woman with red hair.")


@pytest.mark.asyncio
async def test_empty_answer_says_the_model_may_not_read_images():
    llm = FakeLlm(vlm=lambda p: "")
    with pytest.raises(captioner.CaptionError, match="read images"):
        await captioner.describe(llm, b"img", model="m")


@pytest.mark.asyncio
async def test_thinking_is_streamed_and_off():
    llm = FakeLlm()
    await captioner.describe(llm, b"img", model="m")
    kind, kw = llm.calls[0]
    assert kind == "vlm" and kw["think"] is False and kw["model"] == "m"


@pytest.mark.asyncio
async def test_no_targets_means_no_strip_call():
    llm = FakeLlm()
    assert await captioner.strip(llm, "A woman here.", [], model="m") == "A woman here."
    assert llm.calls == []


@pytest.mark.asyncio
async def test_job_walks_every_picture_to_done(store, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"png")
    db = FakeDb({
        "a": {"path": str(img), "positive_prompt": "red hair girl", "wd14_tags": ["red_hair"]},
        "b": {"path": "/nope", "dup_paths": [{"path": str(img)}]},
        "c": {"path": "/missing"},
    })
    b = bk.new_bucket("x", slug="x", trigger="rnbl", targets=["red hair"])
    bk.add_items(b, ["a", "b", "c"])
    store[b["id"]] = b
    llm = FakeLlm()

    captioner._active.claim(b["id"], None)
    result = await captioner.run_lora_caption(
        _reporter(), _cancel(), db=db, ollama=llm, bucket_id=b["id"],
    )

    items = {it["sha256"]: it for it in store[b["id"]]["items"]}
    assert items["a"]["status"] == bk.DONE
    assert items["a"]["full_caption"].startswith("A woman with red hair")
    assert items["a"]["caption"] == "rnbl, A woman stands in a sunny field."
    assert items["b"]["status"] == bk.DONE           # read from its duplicate
    assert items["c"]["status"] == bk.ERROR and "missing" in items["c"]["error"]
    assert result == {"bucket_id": b["id"], "captioned": 2, "errors": 1}
    assert not captioner.is_captioning(b["id"])
    assert "- red hair" in llm.last_text_prompt


@pytest.mark.asyncio
async def test_job_stops_on_cancel_and_frees_the_bucket(store):
    # The class captioner raises; another suite reloads app.spooler.models,
    # so importing it here can give a different class object.
    JobCancelled = captioner.JobCancelled

    b = bk.new_bucket("x", slug="x")
    bk.add_items(b, ["a"])
    store[b["id"]] = b
    cancel = _cancel(True)
    captioner._active.claim(b["id"], None)
    with pytest.raises(JobCancelled):
        await captioner.run_lora_caption(
            _reporter(), cancel, db=FakeDb({}), ollama=FakeLlm(), bucket_id=b["id"],
        )
    assert not captioner.is_captioning(b["id"])
    assert store[b["id"]]["items"][0]["status"] == bk.PENDING_DESCRIBE


def test_one_job_per_bucket():
    submitted = []

    class Spooler:
        def submit(self, lane, title, func, meta=None, **kw):
            submitted.append((lane, title))
            return f"eval-{len(submitted)}"

    b = bk.new_bucket("x", slug="x")
    assert captioner.ensure_job(Spooler(), db=None, ollama=None, bucket=b) is None  # nothing pending
    bk.add_items(b, ["a"])
    try:
        assert captioner.ensure_job(Spooler(), db=None, ollama=None, bucket=b) == "eval-1"
        assert captioner.ensure_job(Spooler(), db=None, ollama=None, bucket=b) is None
        assert submitted[0][0].value == "eval"
    finally:
        captioner._active.drop(b["id"])


@pytest.mark.asyncio
async def test_startup_resumes_every_bucket_with_waiting_pictures(monkeypatch):
    submitted = []

    class Spooler:
        def submit(self, lane, title, func, meta=None, **kw):
            submitted.append(kw["bucket_id"])
            return f"eval-{len(submitted)}"

    waiting = bk.new_bucket("w", slug="w")
    bk.add_items(waiting, ["a"])
    finished = bk.new_bucket("f", slug="f")

    async def list_all(db):
        return [waiting, finished]

    monkeypatch.setattr(bk, "list_all", list_all)
    try:
        assert await captioner.resume_all(Spooler(), db=None, ollama=None) == 1
        assert submitted == [waiting["id"]]
    finally:
        captioner._active.drop(waiting["id"])


@pytest.mark.asyncio
async def test_ollama_unreachable_leaves_pictures_waiting_and_the_job_held(store):
    import httpx

    b = bk.new_bucket("x", slug="x")
    bk.add_items(b, ["a"])
    store[b["id"]] = b

    class Down(FakeLlm):
        async def generate_vlm_stream(self, prompt, images, **kw):
            raise httpx.ConnectError("refused")
            yield  # pragma: no cover

    db = FakeDb({"a": {"path": __file__}})
    captioner._active.claim(b["id"], None)
    try:
        with pytest.raises(httpx.ConnectError):
            await captioner.run_lora_caption(_reporter(), _cancel(), db=db, ollama=Down(),
                                             bucket_id=b["id"])
        # The spooler will run the job again: the picture is not an error and
        # the bucket still shows as captioning.
        assert store[b["id"]]["items"][0]["status"] == bk.PENDING_DESCRIBE
        assert captioner.is_captioning(b["id"])
    finally:
        captioner._active.drop(b["id"])


def test_a_job_cancelled_while_queued_frees_the_bucket():
    class Spooler:
        def __init__(self):
            self._registry = {}

        def submit(self, lane, title, func, meta=None, **kw):
            self._registry["eval-1"] = object()
            return "eval-1"

    sp = Spooler()
    b = bk.new_bucket("x", slug="x")
    bk.add_items(b, ["a"])
    assert captioner.ensure_job(sp, db=None, ollama=None, bucket=b) == "eval-1"
    assert captioner.is_captioning(b["id"])
    sp._registry.clear()  # cancelled before it ever ran
    assert not captioner.is_captioning(b["id"])


@pytest.mark.asyncio
async def test_a_model_that_cannot_see_is_refused_up_front(store):
    b = bk.new_bucket("x", slug="x")
    bk.add_items(b, ["a"])
    store[b["id"]] = b

    class TextOnly(FakeLlm):
        async def vision_models(self):
            return ["llava:latest"]

    captioner._active.claim(b["id"], None)
    with pytest.raises(captioner.CaptionError, match="cannot read images"):
        await captioner.run_lora_caption(_reporter(), _cancel(), db=FakeDb({}), ollama=TextOnly(),
                                         bucket_id=b["id"])
    assert not captioner.is_captioning(b["id"])


@pytest.mark.asyncio
async def test_an_answer_cut_by_the_window_is_not_a_caption():
    class Cut(FakeLlm):
        async def generate_vlm_stream(self, prompt, images, **kw):
            yield {"type": "token", "text": "A woman with red hair stands in a sunny"}
            yield {"type": "done", "reason": "length"}

    with pytest.raises(captioner.CaptionError, match="cut off"):
        await captioner.describe(Cut(), b"img", model="m")
