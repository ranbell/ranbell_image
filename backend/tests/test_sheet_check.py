"""Closing the loop: the finished picture, read back and compared with its sheet.

Everything before this was open loop. `uncovered` checks that the WRITER
kept every box of the sheet; nothing checked whether the IMAGE MODEL did, so an
intended right hand hooked over a window frame could be quietly absent and the
run still reported success.

The check lives on the EVALUATION lane because that lane is auto-paused while
GENERATION, PROMPT and EMBEDDING are active: it waits for the card on its own,
with no `ollama.unload()` dance and no chance of landing on top of a render.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.jobs.runners import run_sheet_check


INTENDED = {
    "medium": "anime illustration",
    "situation": "she has just turned away from the window",
    "setting": "a cramped apartment at dusk",
    "lighting": "warm sunset through dirty glass",
    "props": "a cracked ring on the floor",
    "subjects": [{
        "who": "a young woman", "appearance": "silver hair, violet eyes",
        "hands": "her right hand is hooked over the rusted window frame",
        "motion": "the hem of her coat still swinging from the turn",
    }],
}

FAITHFUL = {
    "medium": "an anime illustration",
    "situation": "she has turned from the window",
    "setting": "a small apartment room at dusk",
    "lighting": "warm sunset light through grimy glass",
    "props": "a cracked ring lying on the floor",
    "subjects": [{
        "who": "a woman", "appearance": "silver haired with violet eyes",
        "hands": "right hand hooked over the rusty window frame",
        "motion": "coat hem swinging from her turn",
    }],
}

STIFF = {
    "medium": "photograph",
    "situation": "she stands by the window",
    "setting": "a small apartment room at dusk",
    "lighting": "warm sunset light through grimy glass",
    "props": "",
    "subjects": [{
        "who": "a woman", "appearance": "silver haired with violet eyes",
        "hands": "both arms resting at her sides", "motion": "",
    }],
}


class _Reporter:
    def update(self, *a, **k): pass
    def indeterminate(self): pass


class _Cancel:
    def on_cancel(self, fn): pass
    def raise_if_set(self): pass
    async def pause_checkpoint(self): pass


class _DB:
    """One picture on disk, with whatever creation_record the case needs."""

    def __init__(self, path, sheet=INTENDED, *, exists=True):
        self.path, self.sheet, self.exists = path, sheet, exists
        self.written: dict[str, dict] = {}

    async def get(self, sha):
        rec = {"scene_sheet": self.sheet} if self.sheet is not None else {}
        return {"path": str(self.path) if self.exists else "/nowhere/missing.png",
                "creation_record": rec}

    async def get_config(self):
        return {}

    async def set_payload(self, sha, payload):
        self.written.setdefault(sha, {}).update(payload)


class _VLM:
    def __init__(self, got=FAITHFUL):
        self.got, self.calls, self.images = got, 0, []

    async def generate_vlm_stream(self, prompt, images, **kw):
        self.calls += 1
        self.images.append(list(images))
        body = "" if self.got is None else json.dumps(self.got)
        yield {"type": "token", "text": body}


def _png(tmp_path, name="pic.png"):
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", (48, 48), (10, 120, 200)).save(p)
    return p


# ── The check itself ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_faithful_picture_is_recorded_clean(tmp_path):
    db, vlm = _DB(_png(tmp_path)), _VLM(FAITHFUL)
    out = await run_sheet_check(_Reporter(), _Cancel(), db=db, ollama=vlm, sha256s=["a"])

    assert out == {"checked": 1, "skipped": 0}
    rec = db.written["a"]["sheet_check"]
    assert rec["missed"] == [], rec["missed"]
    assert rec["checked"] == rec["covered"] > 0
    assert rec["error"] == "" and rec["checked_at"]
    # The read-back is kept, so a visual diff can be built on it later.
    assert rec["got_sheet"]["medium"] == "an anime illustration"


@pytest.mark.asyncio
async def test_a_stiff_picture_reports_the_boxes_it_dropped(tmp_path):
    db, vlm = _DB(_png(tmp_path)), _VLM(STIFF)
    await run_sheet_check(_Reporter(), _Cancel(), db=db, ollama=vlm, sha256s=["a"])

    rec = db.written["a"]["sheet_check"]
    boxes = {m["box"] for m in rec["missed"]}
    # The medium flipped, the moment went, the ring vanished, the body rested.
    assert {"medium", "situation", "props", "hands", "motion"} <= boxes
    assert rec["covered"] == rec["checked"] - len(rec["missed"])
    hands = next(m for m in rec["missed"] if m["box"] == "hands")
    assert hands["person"] == 1 and hands["who"] == "a young woman"


@pytest.mark.asyncio
async def test_the_picture_is_read_whole_and_on_its_own(tmp_path):
    img = _png(tmp_path)
    db, vlm = _DB(img), _VLM()
    await run_sheet_check(_Reporter(), _Cancel(), db=db, ollama=vlm, sha256s=["a"])
    # One call, one image, the file's own bytes — no tile, no downscale.
    assert vlm.calls == 1 and len(vlm.images[0]) == 1
    assert vlm.images[0][0] == img.read_bytes()


@pytest.mark.asyncio
async def test_an_image_with_no_sheet_is_skipped(tmp_path):
    db, vlm = _DB(_png(tmp_path), sheet=None), _VLM()
    out = await run_sheet_check(_Reporter(), _Cancel(), db=db, ollama=vlm, sha256s=["a"])
    # Only the sheet styles carry one; the rest is not ours to judge.
    assert out == {"checked": 0, "skipped": 1}
    assert vlm.calls == 0 and db.written == {}


@pytest.mark.asyncio
async def test_a_picture_that_cannot_be_read_is_recorded_and_does_not_raise(tmp_path):
    # A model that cannot read pictures fails silently with an empty answer
    # (CLAUDE.md). A failed check must never cost the picture it was checking.
    db, vlm = _DB(_png(tmp_path)), _VLM(None)
    out = await run_sheet_check(_Reporter(), _Cancel(), db=db, ollama=vlm, sha256s=["a"])
    assert out == {"checked": 0, "skipped": 0}
    assert db.written["a"]["sheet_check"]["error"]
    assert db.written["a"]["sheet_check"]["missed"] == []


@pytest.mark.asyncio
async def test_a_missing_file_is_recorded_and_does_not_raise(tmp_path):
    db, vlm = _DB(_png(tmp_path), exists=False), _VLM()
    out = await run_sheet_check(_Reporter(), _Cancel(), db=db, ollama=vlm, sha256s=["a"])
    assert out == {"checked": 0, "skipped": 0}
    assert db.written["a"]["sheet_check"]["error"]


@pytest.mark.asyncio
async def test_every_picture_of_a_batch_is_checked(tmp_path):
    db, vlm = _DB(_png(tmp_path)), _VLM(FAITHFUL)
    out = await run_sheet_check(_Reporter(), _Cancel(), db=db, ollama=vlm,
                                sha256s=["a", "b", "c", "d"])
    assert out == {"checked": 4, "skipped": 0}
    assert vlm.calls == 4 and set(db.written) == {"a", "b", "c", "d"}


# ── Who queues it, and when ──────────────────────────────────────────────────


class _Spooler:
    def __init__(self, active=True):
        self.active, self.submitted = active, []

    def is_lane_active(self, lane):
        return self.active

    def submit(self, lane, title, fn, **kw):
        self.submitted.append({"lane": lane, "title": title, "fn": fn, "kw": kw})
        return "job-1"


class _GenDB(_DB):
    async def set_payload(self, sha, payload):
        await super().set_payload(sha, payload)


async def _run_generation(tmp_path, *, creation_meta, spooler, ollama, cfg=None):
    """run_generation with ComfyUI stubbed down to one produced image."""
    from backend.app import runtime_config
    from backend.app.jobs.runners import run_generation

    runtime_config._cache = None
    img = _png(tmp_path, "out.png")
    data = img.read_bytes()

    class _Comfy:
        def load_workflow(self, name): return {}
        def patch_workflow(self, *a, **k): return {}
        def new_client_id(self): return "c1"
        async def queue_prompt(self, *a, **k): return "p1"
        async def stream_progress(self, *a, **k):
            yield {"type": "comfy_output",
                   "images": [{"filename": "out.png", "subfolder": "", "type": "output"}]}
        async def fetch_image(self, *a, **k): return data
        async def fetch_history(self, *a, **k): return []

    db = _GenDB(img)
    if cfg is not None:
        async def _get_config(): return cfg
        db.get_config = _get_config

    import backend.app.scanner.save as save_mod
    orig = save_mod.save_generated_image

    async def _fake_save(img_bytes, name, _db, **kw):
        return "newsha"
    save_mod.save_generated_image = _fake_save
    try:
        return await run_generation(
            _Reporter(), _Cancel(), comfy=_Comfy(), db=db, spooler=spooler, ollama=ollama,
            workflow_name="wf", positive="p", negative="",
            creation_meta=creation_meta,
        )
    finally:
        save_mod.save_generated_image = orig


@pytest.mark.asyncio
async def test_a_sheet_style_render_queues_the_check(tmp_path):
    sp, vlm = _Spooler(), _VLM()
    await _run_generation(tmp_path, creation_meta={"scene_sheet": INTENDED},
                          spooler=sp, ollama=vlm)
    assert [s["title"] for s in sp.submitted] == ["sheet_check"]
    assert sp.submitted[0]["kw"]["sha256s"] == ["newsha"]
    assert sp.submitted[0]["fn"] is run_sheet_check


@pytest.mark.asyncio
async def test_a_render_with_no_sheet_queues_nothing(tmp_path):
    # Having a scene sheet IS the condition — there is no style name to test.
    sp, vlm = _Spooler(), _VLM()
    await _run_generation(tmp_path, creation_meta={"prompt_style": "natural"},
                          spooler=sp, ollama=vlm)
    assert sp.submitted == []


@pytest.mark.asyncio
async def test_nothing_is_queued_while_the_lane_is_paused(tmp_path):
    sp, vlm = _Spooler(active=False), _VLM()
    await _run_generation(tmp_path, creation_meta={"scene_sheet": INTENDED},
                          spooler=sp, ollama=vlm)
    assert sp.submitted == []


@pytest.mark.asyncio
async def test_nothing_is_queued_when_the_setting_is_off(tmp_path):
    sp, vlm = _Spooler(), _VLM()
    await _run_generation(tmp_path, creation_meta={"scene_sheet": INTENDED},
                          spooler=sp, ollama=vlm, cfg={"auto_sheet_check": False})
    assert sp.submitted == []
