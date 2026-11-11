"""LoRA comparison sheet: the graph ranbell builds, the cells, the job, the picture."""
from __future__ import annotations

import copy
import io
import sys
import types

import pytest

for _name in ("sklearn", "sklearn.cluster"):  # render → scanner → colour extractor
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["sklearn.cluster"].KMeans = object

from app.lora import sheet as sh  # noqa: E402
from app.lora import sheet_graph as sg  # noqa: E402
from app.lora import sheet_grid  # noqa: E402
from app.lora.sheet_api import prefill_from_doc  # noqa: E402
from app.spooler.models import CancelToken, ProgressReporter  # noqa: E402

MODELS = {"unet": "krea2_turbo.safetensors", "clip": "qwen3vl_4b.safetensors",
          "vae": "qwen_image_vae.safetensors"}


def _body(**kw):
    body = {"models": dict(MODELS), "prompt": "a girl reading in a cafe", "seed": 42,
            "rows": [{"lora": "my_style/my_style-000010.safetensors", "trigger": "zqx_style"},
                     {"lora": "my_style/my_style.safetensors", "trigger": "zqx_style"}],
            "strengths": [0, 0.5, 1.0]}
    body.update(kw)
    return body


# ── Graph ─────────────────────────────────────────────────────────────────────

def _graph(lora=None, strength=0.0, **sampling):
    s = {"seed": 7, "width": 1024, "height": 1536, "steps": 8, "cfg": 1.0,
         "sampler": "euler", "scheduler": "simple"} | sampling
    return sg.build_krea2(models=sg.Models(**MODELS), sampling=sg.Sampling(**s),
                          prompt="zqx_style, a girl", lora=lora, strength=strength)


def _sampler(g):
    return next(n for n in g.values() if n["class_type"] == "KSampler")


def test_graph_is_core_nodes_only_with_every_wire_landing():
    for g in (_graph(), _graph("a.safetensors", 0.75)):
        assert {n["class_type"] for n in g.values()} <= sg.CORE_NODES
        for node in g.values():
            for v in node["inputs"].values():
                if isinstance(v, list):
                    assert str(v[0]) in g
        assert [n["class_type"] for n in g.values()].count("SaveImage") == 1
        assert not any(n["class_type"] == "PreviewImage" for n in g.values())
        assert set(g) == {k for k in g if k.isdigit()}  # API format, no stray keys


def test_strength_zero_has_no_lora_node():
    g = _graph("a.safetensors", 0.0)
    assert not any(n["class_type"] == "LoraLoaderModelOnly" for n in g.values())
    assert g[_sampler(g)["inputs"]["model"][0]]["class_type"] == "UNETLoader"


def test_lora_sits_between_the_model_and_the_sampler():
    g = _graph("my_style/a.safetensors", 0.75)
    lora = g[_sampler(g)["inputs"]["model"][0]]
    assert lora["class_type"] == "LoraLoaderModelOnly"
    assert lora["inputs"]["lora_name"] == "my_style/a.safetensors"
    assert lora["inputs"]["strength_model"] == 0.75
    assert g[lora["inputs"]["model"][0]]["class_type"] == "UNETLoader"


def test_krea2_negative_is_the_positive_zeroed_and_values_are_set():
    g = _graph(seed=123, steps=6, cfg=1.5, sampler="er_sde")
    ks = _sampler(g)["inputs"]
    neg = g[ks["negative"][0]]
    assert neg["class_type"] == "ConditioningZeroOut"
    assert neg["inputs"]["conditioning"] == ks["positive"]
    assert (ks["seed"], ks["steps"], ks["cfg"], ks["sampler_name"]) == (123, 6, 1.5, "er_sde")
    clip = next(n for n in g.values() if n["class_type"] == "CLIPLoader")
    assert clip["inputs"]["type"] == "krea2"
    lat = g[ks["latent_image"][0]]["inputs"]
    assert (lat["width"], lat["height"], lat["batch_size"]) == (1024, 1536, 1)


@pytest.mark.parametrize("bad", [{"width": 1000}, {"height": 32}, {"steps": 0}])
def test_bad_sampling_is_refused(bad):
    with pytest.raises(sg.SheetGraphError):
        _graph(**bad)


def test_missing_model_file_is_refused():
    with pytest.raises(sg.SheetGraphError, match="vae"):
        sg.build_krea2(models=sg.Models(unet="u", clip="c", vae=""),
                       sampling=sg.Sampling(1, 1024, 1024, 8, 1.0, "euler", "simple"), prompt="x")


# ── Sheet ─────────────────────────────────────────────────────────────────────

def test_rows_sharing_a_trigger_share_the_baseline():
    s = sh.new_sheet(_body())
    g = sh.grid(s)
    assert len(g) == 2 and len(g[0]) == 3
    assert g[0][0] == g[1][0]  # strength 0, same prompt → one picture
    assert len(s["renders"]) == 1 + 2 * 2
    first = next(iter(s["renders"].values()))
    assert first["strength"] == 0 and first["lora"] == ""  # the baseline is drawn first


def test_different_triggers_get_their_own_baseline_and_trigger_goes_in_front():
    s = sh.new_sheet(_body(rows=[{"lora": "a.safetensors", "trigger": "aaa"},
                                 {"lora": "b.safetensors", "trigger": ""}]))
    g = sh.grid(s)
    assert g[0][0] != g[1][0]
    assert s["renders"][g[0][2]]["prompt"] == "aaa, a girl reading in a cafe"
    assert s["renders"][g[1][2]]["prompt"] == "a girl reading in a cafe"


def test_no_trigger_column_is_full_strength_without_the_trigger():
    s = sh.new_sheet(_body(no_trigger_column=True))
    col = sh.columns(s)[-1]
    assert col == {"strength": 1.0, "trigger_on": False}
    r = s["renders"][sh.grid(s)[0][-1]]
    assert r["prompt"] == "a girl reading in a cafe" and r["strength"] == 1.0 and r["lora"]


@pytest.mark.parametrize("bad, msg", [
    ({"rows": []}, "at least one"),
    ({"prompt": " "}, "prompt"),
    ({"strengths": [5]}, "out of range"),
    ({"models": {"unet": "u", "clip": "c"}}, "vae"),
])
def test_unusable_settings_are_refused(bad, msg):
    with pytest.raises(sh.SheetError, match=msg):
        sh.new_sheet(_body(**bad))


def test_defaults_come_from_the_target():
    s = sh.new_sheet(_body(seed=None))
    assert (s["steps"], s["cfg"], s["sampler"], s["scheduler"]) == (8, 1.0, "euler", "simple")
    assert isinstance(s["seed"], int)


def test_duplicate_keeps_settings_and_starts_empty():
    s = sh.new_sheet(_body())
    s["renders"][next(iter(s["renders"]))].update(status=sh.DONE, sha256="x")
    d = sh.duplicate(s, seed=99)
    assert d["id"] != s["id"] and d["seed"] == 99 and d["rows"] == s["rows"]
    assert all(r["status"] == sh.PENDING for r in d["renders"].values())


def test_draw_the_rest_retries_failures_and_keeps_what_is_done():
    s = sh.new_sheet(_body())
    keys = list(s["renders"])
    s["renders"][keys[0]].update(status=sh.DONE, sha256="x")
    s["renders"][keys[1]].update(status=sh.ERROR, error="boom")
    assert sh.request_rest(s) == len(keys) - 1
    assert s["renders"][keys[0]]["status"] == sh.DONE


def test_seconds_per_picture_is_the_median():
    s = sh.new_sheet(_body())
    for r, t in zip(s["renders"].values(), (10, 30, 20)):
        r.update(status=sh.DONE, seconds=t)
    assert sh.seconds_per_picture([s]) == 20


# ── Job ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def store(monkeypatch):
    data: dict[str, dict] = {}

    async def load(db, sid):
        return copy.deepcopy(data.get(sid))

    async def save(db, s):
        data[s["id"]] = copy.deepcopy(s)
        return s

    monkeypatch.setattr(sh, "load", load)
    monkeypatch.setattr(sh, "save", save)
    return data


def _reporter():
    from app.spooler.models import Job, JobLane
    return ProgressReporter(Job(id="gen-1", lane=JobLane.GENERATION, title="t"), lambda *a: None)


def _cancel():
    import asyncio
    return CancelToken(asyncio.Event(), [])


@pytest.mark.asyncio
async def test_job_draws_every_picture_and_a_failure_does_not_stop_it(store, monkeypatch):
    import app.jobs.render as render

    s = sh.new_sheet(_body())
    store[s["id"]] = s
    calls = []

    async def fake_render(reporter, cancel, **kw):
        calls.append(kw)
        if kw["payload_extra"]["lora_sheet"]["strength"] == 0.5 and "000010" in kw["payload_extra"]["lora_sheet"]["lora"]:
            raise RuntimeError("lora_name not in list")
        return {"sha256s": [f"sha{len(calls)}"]}

    monkeypatch.setattr(render, "run_render", fake_render)
    out = await sh.run_lora_sheet(_reporter(), _cancel(), db=None, comfy=None, sheet_id=s["id"])
    assert out == {"sheet_id": s["id"], "drawn": 4, "errors": 1}
    assert len(calls) == 5
    for kw in calls:
        assert kw["workflow"] is not None and kw["seed"] == 42 and kw["subdir"] == sh.SUBDIR
    saved = store[s["id"]]["renders"]
    assert sum(r["status"] == sh.DONE for r in saved.values()) == 4
    assert any("lora_name" in r["error"] for r in saved.values())
    assert not sh.is_drawing(s["id"])


@pytest.mark.asyncio
async def test_in_flight_frames_are_kept_per_cell_and_dropped_at_the_end(store, monkeypatch):
    import app.jobs.render as render

    s = sh.new_sheet(_body())
    store[s["id"]] = s
    seen = []

    async def fake_render(reporter, cancel, **kw):
        # Before the first frame the cell is already known (shown as drawing).
        before = sh.latest_preview(s["id"])
        await kw["preview"](b"jpeg-1")
        await kw["preview"](b"jpeg-2")
        now = sh.latest_preview(s["id"])
        seen.append((before["key"], before["jpeg"], now["key"], now["jpeg"]))
        return {"sha256s": [f"sha{len(seen)}"]}

    monkeypatch.setattr(render, "run_render", fake_render)
    await sh.run_lora_sheet(_reporter(), _cancel(), db=None, comfy=None, sheet_id=s["id"])
    keys = [k for k, *_ in seen]
    assert len(set(keys)) == len(keys) == len(s["renders"])
    assert all(b == b"" and k == k2 and j == b"jpeg-2" for k, b, k2, j in seen)
    assert sh.latest_preview(s["id"]) is None


@pytest.mark.asyncio
async def test_comfy_gone_leaves_the_rest_waiting(store, monkeypatch):
    import httpx

    import app.jobs.render as render

    s = sh.new_sheet(_body())
    store[s["id"]] = s

    async def gone(reporter, cancel, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(render, "run_render", gone)
    with pytest.raises(httpx.ConnectError):
        await sh.run_lora_sheet(_reporter(), _cancel(), db=None, comfy=None, sheet_id=s["id"])
    assert all(r["status"] == sh.PENDING for r in store[s["id"]]["renders"].values())


@pytest.mark.asyncio
async def test_run_render_queues_a_given_graph_untouched():
    from app.jobs.render import run_render

    queued = []

    class Comfy:
        def load_workflow(self, name):
            raise AssertionError("must not read from disk")

        def patch_workflow(self, *a, **kw):
            raise AssertionError("must not patch")

        @staticmethod
        def new_client_id():
            return "c"

        async def queue_prompt(self, wf, preview=False, client_id=""):
            queued.append(wf)
            return "pid"

        async def stream_progress(self, pid, client_id=""):
            yield {"type": "comfy_done"}

        async def fetch_history(self, pid):
            return []

    class Reporter:
        def indeterminate(self): pass
        def update(self, *a, **k): pass

    class Cancel:
        def on_cancel(self, fn): pass
        def raise_if_set(self): pass

    g = _graph("a.safetensors", 1.0)
    await run_render(Reporter(), Cancel(), db=None, comfy=Comfy(), workflow_name="lora_sheet:krea2",
                     positive="x", seed=7, workflow=g)
    assert queued == [g]


# ── Picture and prefill ──────────────────────────────────────────────────────

def test_grid_png_has_a_cell_per_column_and_no_metadata():
    from PIL import Image

    s = sh.new_sheet(_body(no_trigger_column=True))
    keys = list(s["renders"])
    s["renders"][keys[0]].update(status=sh.DONE, sha256="aa")
    s["renders"][keys[1]].update(status=sh.ERROR, error="x")

    def open_image(sha):
        return Image.new("RGB", (64, 96), (200, 0, 0)) if sha == "aa" else None

    data = sheet_grid.compose(s, open_image)
    with Image.open(io.BytesIO(data)) as im:
        assert im.format == "PNG" and not im.text
        cols = len(sh.columns(s))
        assert im.width == sheet_grid.LABEL_W + sheet_grid.CELL_W * cols + sheet_grid.PAD
        cell_h = round(sheet_grid.CELL_W * s["height"] / s["width"])
        assert im.height > cell_h * len(s["rows"])
        # the baseline cell (row 0, column 0) is the red picture
        y = im.height - sheet_grid.PAD - cell_h * 2 + cell_h // 2
        assert im.getpixel((sheet_grid.LABEL_W + sheet_grid.CELL_W // 2, y)) == (200, 0, 0)
    assert sheet_grid.column_label({"strength": 0.0, "trigger_on": True}) == "0 · base"
    assert sheet_grid.row_label({"lora": "my_style/my_style-000010.safetensors"}) == "my_style-000010"


def test_prefill_reads_the_graph_the_picture_was_drawn_with():
    graph = {
        "37": {"class_type": "UNETLoader", "inputs": {"unet_name": "jank.safetensors"}},
        "38": {"class_type": "CLIPLoader", "inputs": {"clip_name": "te.safetensors", "type": "krea2"}},
        "39": {"class_type": "VAELoader", "inputs": {"vae_name": "vae.safetensors"}},
        "3": {"class_type": "KSampler", "inputs": {
            "seed": ["82", 0], "steps": ["84", 0], "cfg": ["83", 0],
            "sampler_name": ["85", 0], "scheduler": ["86", 0]}},
        "62": {"class_type": "EmptyLatentImage", "inputs": {"width": ["80", 0], "height": ["81", 0]}},
        "80": {"class_type": "PrimitiveInt", "inputs": {"value": 1284}},
        "81": {"class_type": "PrimitiveInt", "inputs": {"value": 1824}},
        "82": {"class_type": "Seed Generator", "inputs": {"seed": 555}},
        "83": {"class_type": "Cfg Literal", "inputs": {"float": 1}},
        "84": {"class_type": "Int Literal", "inputs": {"int": 8}},
        "85": {"class_type": "Sampler Selector", "inputs": {"sampler_name": "er_sde"}},
        "86": {"class_type": "Scheduler Selector", "inputs": {"scheduler": "simple"}},
    }
    doc = {"positive_prompt": "a girl", "raw_metadata": {"content": graph}}
    p = prefill_from_doc(doc)
    assert p["models"] == {"unet": "jank.safetensors", "clip": "te.safetensors", "vae": "vae.safetensors"}
    assert (p["seed"], p["steps"], p["cfg"], p["sampler"], p["scheduler"]) == (555, 8, 1, "er_sde", "simple")
    assert (p["width"], p["height"], p["prompt"]) == (1284, 1824, "a girl")
    # ranbell's own record wins for prompt and seed
    doc["creation_record"] = {"positive_prompt_generated": "rendered", "seed": 9}
    assert (prefill_from_doc(doc)["prompt"], prefill_from_doc(doc)["seed"]) == ("rendered", 9)


@pytest.mark.asyncio
async def test_model_lists_come_from_models_or_object_info():
    import httpx

    from app.ai.comfy import ComfyUIClient

    def handler(request):
        path = request.url.path
        if path == "/models/loras":
            return httpx.Response(200, json=["b.safetensors", "my/a.safetensors"])
        if path == "/models/vae":  # an older ComfyUI
            return httpx.Response(404)
        if path == "/object_info/VAELoader":
            return httpx.Response(200, json={"VAELoader": {"input": {"required": {
                "vae_name": [["v1.safetensors", "v0.safetensors"]]}}}})
        if path == "/object_info/KSampler":
            return httpx.Response(200, json={"KSampler": {"input": {"required": {
                "sampler_name": ["COMBO", {"options": ["euler", "er_sde"]}],
                "scheduler": [["simple", "karras"]]}}}})
        return httpx.Response(404)

    c = ComfyUIClient()
    c._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://x")
    import app.ai.comfy as comfy_mod
    old = comfy_mod.settings.comfyui_url
    comfy_mod.settings.comfyui_url = "http://x"
    try:
        assert await c.list_models("loras") == ["b.safetensors", "my/a.safetensors"]
        assert await c.list_models("vae") == ["v0.safetensors", "v1.safetensors"]
        assert await c.sampler_choices() == {"samplers": ["euler", "er_sde"],
                                             "schedulers": ["simple", "karras"]}
        with pytest.raises(ValueError):
            await c.list_models("../etc")
    finally:
        comfy_mod.settings.comfyui_url = old


def test_default_name_is_the_folder_or_the_file():
    assert sh.new_sheet(_body())["name"] == "my_style"
    assert sh.new_sheet(_body(rows=[{"lora": "x/a.safetensors"}, {"lora": "b.safetensors"}]))["name"] == "a"


def test_prefill_takes_the_shape_from_the_picture_itself():
    # The latent said 1024x1024, but the picture on disk is 832x1216 (cropped,
    # or drawn by a graph whose size node ranbell cannot read): the file wins.
    doc = {"raw_metadata": {"content": {
        "1": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024}}}}}
    p = prefill_from_doc(doc, (832, 1216))
    assert (p["source_width"], p["source_height"]) == (832, 1216)
    # Without the file the latent size is the fallback; without either, nothing.
    p = prefill_from_doc(doc)
    assert (p["source_width"], p["source_height"]) == (1024, 1024)
    assert "source_width" not in prefill_from_doc({})


def test_prefill_reads_the_pixel_size_from_the_file_header(tmp_path):
    from PIL import Image

    from app.lora.sheet_api import _pixel_size

    path = tmp_path / "x.png"
    Image.new("RGB", (640, 960)).save(path)
    assert _pixel_size(str(path)) == (640, 960)
    assert _pixel_size(str(tmp_path / "missing.png")) is None


def test_prefill_from_another_models_picture_keeps_only_prompt_seed_and_shape():
    # An Anima picture opened for a Krea2 sheet: taking its text encoder made
    # ComfyUI fail ("expects 12x2560 features but got 1024").
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "anima.safetensors"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_06b.safetensors",
                                                      "type": "stable_diffusion"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
        "4": {"class_type": "KSampler", "inputs": {"seed": 77, "steps": 30, "cfg": 4.5,
                                                   "sampler_name": "er_sde", "scheduler": "sgm_uniform"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 896, "height": 1152}},
    }
    doc = {"positive_prompt": "a girl", "raw_metadata": {"content": graph}}
    p = prefill_from_doc(doc, None, "krea2")
    assert p["foreign"] == {"clip_types": ["stable_diffusion"]}
    assert (p["prompt"], p["seed"], p["source_width"], p["source_height"]) == ("a girl", 77, 896, 1152)
    assert not p.get("models")
    assert not {"steps", "cfg", "sampler", "scheduler"} & p.keys()
    # The same picture, opened with no target in mind, still gives everything.
    assert prefill_from_doc(doc)["models"]["clip"] == "qwen_3_06b.safetensors"
    # A Krea2 picture is not foreign.
    graph["2"]["inputs"]["type"] = "krea2"
    p = prefill_from_doc(doc, None, "krea2")
    assert "foreign" not in p and p["cfg"] == 4.5


def test_a_sheet_whose_job_left_the_spooler_is_not_drawing():
    # Cancelled while still queued: the runner never starts, so its `finally`
    # never clears the sheet. The spooler's registry is the truth.
    class Spooler:
        def __init__(self):
            self._registry = {}

        def submit(self, lane, title, func, meta=None, **kw):
            self._registry["job-1"] = object()
            return "job-1"

    sp = Spooler()
    s = sh.new_sheet(_body())
    assert sh.submit(sp, db=None, comfy=None, sheet=s) == "job-1"
    assert sh.is_drawing(s["id"]) and sh.active_job(s["id"]) == "job-1"
    assert sh.submit(sp, db=None, comfy=None, sheet=s) is None  # one job per sheet
    sp._registry.clear()
    assert not sh.is_drawing(s["id"]) and sh.active_job(s["id"]) is None
