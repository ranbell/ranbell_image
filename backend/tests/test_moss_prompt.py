"""The MOSS-Image style: caption, layout and tags.

The layout string is checked against what ComfyUI's own MOSSImageLayoutPrompt
node would build, and the USERGUIDE's example box is the fixed point.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.ai.comfy import ComfyUIClient
from backend.app.prompt import moss
from backend.app.prompt import scene_sheet as ss


def _sheet(**over):
    data = {
        "medium": "anime illustration", "setting": "a rooftop at dusk",
        "lighting": "orange backlight", "camera": "wide shot",
        "props": "a bench on the right, a lamp post on the left",
        "subjects": [
            {"who": "a girl", "position": "center", "appearance": "long black hair",
             "outfit": "school uniform", "pose_action": "waving", "holding": "a phone in her right hand"},
            {"who": "a boy", "position": "left", "outfit": "hoodie"},
        ],
        "text_in_image": ["OPEN"],
    }
    data.update(over)
    return ss.normalize_sheet(data)


# ── Pixels ───────────────────────────────────────────────────────────────────

def test_the_userguide_box_comes_out_as_the_userguide_writes_it():
    # USERGUIDE: "EXPLORE" title at [-416, -384, -288, 384] on 1024x1024.
    assert moss.to_pixels([125, 93.75, 875, 218.75], 1024, 1024) == [-416, -384, -288, 384]


def test_pixels_follow_the_canvas_not_a_square():
    # The whole grid is the whole picture, whatever its shape.
    assert moss.to_pixels([0, 0, 1000, 1000], 832, 1216) == [-608, -416, 608, 416]
    # Edges enclose the box: floor the near edge, ceil the far one.
    assert moss.to_pixels([333, 333, 667, 667], 1000, 1000) == [-167, -167, 167, 167]


# ── Layout ───────────────────────────────────────────────────────────────────

def test_every_person_and_every_text_gets_a_box_from_the_sheet():
    items = moss.layout_items(_sheet())
    assert [(i["id"], i["label"]) for i in items] == [
        ("person 1", "character"), ("person 2", "character"), ("text 1", "text")]
    assert items[2]["text"] == "OPEN"


def test_the_layout_prompt_lists_the_ids_and_the_canvas():
    sheet = _sheet()
    p = moss.layout_prompt(sheet, moss.layout_items(sheet), width=832, height=1216)
    assert '"person 1"' in p and '"text 1"' in p and "832x1216 pixels (portrait)" in p
    assert '"object: "' in p  # props are split into boxes by the writer


def test_a_clean_draft_compiles_to_the_nodes_string():
    sheet = _sheet()
    items = moss.layout_items(sheet)
    draft = {
        "caption": "An anime illustration of a rooftop at dusk.",
        "boxes": [
            {"id": "person 1", "caption": "a girl waving", "box": [300, 200, 700, 1000]},
            {"id": "Person 2", "caption": "a boy in a hoodie", "box": [0, 300, 300, 1000]},
            {"id": "text 1", "caption": "white neon letters", "box": [400, 50, 600, 120]},
            {"id": "object: bench", "caption": "a wooden bench", "box": [700, 700, 1000, 900]},
        ],
    }
    out, notes = moss.compile_layout(draft, items, sheet, width=1024, height=1024)
    assert notes == []
    # The node's own separators and key order.
    assert out.startswith('{"summary":{"caption":"An anime illustration of a rooftop at dusk."},"items":[')
    assert out.endswith('"bbox_coord_space":"abs_centralized","bbox_format":"yxyx"}')
    layout = json.loads(out)
    assert [i["label"] for i in layout["items"]] == ["character", "character", "text", "object"]
    assert list(layout["items"][2]) == ["label", "caption", "text", "bbox"]
    # The text is the sheet's, whatever the writer did.
    assert layout["items"][2]["text"] == "OPEN"
    assert layout["items"][0]["bbox"] == moss.to_pixels([300, 200, 700, 1000], 1024, 1024)


def test_a_broken_draft_is_repaired_and_says_so():
    sheet = _sheet()
    items = moss.layout_items(sheet)
    draft = {
        "caption": "",
        "boxes": [
            {"id": "person 1", "caption": "", "box": [700, 1000, 300, 200]},  # swapped
            {"id": "text 1", "caption": "letters", "box": [0.4, 0.05, 0.6, 0.12]},  # 0-1 scale
            {"id": "a dragon", "caption": "a dragon", "box": [0, 0, 100, 100]},  # not asked for
        ],
    }
    out, notes = moss.compile_layout(draft, items, sheet, width=1024, height=1024)
    layout = json.loads(out)
    text = " | ".join(notes)
    assert "summary caption was missing" in text
    assert "person 1 box: corners swapped" in text
    assert "person 1 had no caption — taken from the sheet" in text
    assert "person 2 had no box — placed by default" in text
    assert "text 1 box: written on a 0-1 scale" in text
    # not on the list, but kept: nothing is dropped for want of a place
    assert 'box "a dragon" was not on the list — kept as an object' in text
    assert len(layout["items"]) == 4 and layout["items"][3]["label"] == "object"
    assert layout["summary"]["caption"].startswith("An anime illustration.")
    assert "long black hair" in layout["items"][0]["caption"]


def test_no_boxes_means_the_caption_alone_like_the_node():
    sheet = ss.normalize_sheet({"medium": "oil painting", "setting": "a calm sea"})
    out, _ = moss.compile_layout({"caption": "An oil painting of a calm sea."}, [], sheet,
                                 width=1024, height=1024)
    assert out == "An oil painting of a calm sea."


def test_kana_is_noted_not_stopped():
    assert "garbled" in moss.kana_note(["ひらがな"])
    assert moss.kana_note(["OPEN", "营业中"]) == ""


# ── Caption ──────────────────────────────────────────────────────────────────

def test_the_caption_is_written_for_moss_and_krea_is_unchanged():
    sheet = _sheet()
    m = ss.script_prompt(sheet, model="moss")
    k = ss.script_prompt(sheet)
    assert m.startswith("Write the prompt for the MOSS-Image image model")
    assert "never translated" in m and "left of, behind or in front of" in m
    assert k.startswith("Write the prompt for the Krea 2 image model")
    assert "How Krea 2 reads: it was trained on long, detailed captions in natural English, " \
           "and it attaches a detail to the noun it is written next to. So:" in k
    assert "- Write visible text in double quotes, exactly as in the sheet.\n" in k


# ── Tags ─────────────────────────────────────────────────────────────────────

def test_tags_come_out_in_moss_order_with_spaces():
    raw = ("1girl, solo, blue_hair, hatsune_miku, masterpiece, sensitive, @foo_bar, "
           "year_2024, score_9, ^_^, blue_hair")
    out, notes = moss.compile_tags(raw, characters={"hatsune_miku"})
    assert out == ("best quality, highres, masterpiece, score 9, year 2024, hatsune miku, "
                   "@foo bar, 1girl, solo, blue hair, ^_^")
    assert notes == ["rating words left out: sensitive"]


def test_a_character_name_is_moved_never_added():
    out, _ = moss.compile_tags("1girl, smile", characters={"hatsune_miku"})
    assert "hatsune" not in out


# ── The workflow canvas ──────────────────────────────────────────────────────

def test_the_moss_latent_is_read_and_batched():
    wf = {
        "6": {"class_type": "EmptyFlux2LatentImage",
              "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
        "16": {"class_type": "KSampler", "inputs": {"seed": 1, "latent_image": ["6", 0],
                                                     "positive": ["4", 0], "negative": ["5", 0]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
    }
    c = ComfyUIClient.__new__(ComfyUIClient)
    assert c.workflow_canvas(wf) == {"width": 832, "height": 1216}
    out = c.patch_workflow(wf, "pos", moss.MOSS_NEGATIVE, batch_count=2)
    assert out["6"]["inputs"]["batch_size"] == 2
    assert out["4"]["inputs"]["text"] == "pos" and out["5"]["inputs"]["text"] == moss.MOSS_NEGATIVE


# ── The whole job ────────────────────────────────────────────────────────────

class _Reporter:
    def update(self, *a, **k): pass
    def indeterminate(self): pass


class _Cancel:
    def on_cancel(self, fn): pass
    def raise_if_set(self): pass


class _DB:
    def __init__(self, path):
        self.path = path

    async def get(self, sha):
        return {"path": str(self.path), "wd14_tags": ["1girl", "solo", "blue_hair"],
                "wd14_tags_scores": [0.9, 0.8, 0.7]}

    async def get_config(self):
        return {}


class _LLM:
    """The sheet step reads one girl and a sign; the text step answers with
    ``layout`` (a list, one answer per call) or a caption."""

    def __init__(self, layout=None, tags="1girl, blue_hair, masterpiece"):
        self.layout = list(layout or [])
        self.tags = tags
        self.vlm_prompts, self.text_prompts = [], []

    async def generate_vlm_stream(self, prompt, images, **kw):
        self.vlm_prompts.append(prompt)
        if "scene sheet" in prompt:
            sheet = {"medium": "anime illustration", "setting": "a shop front",
                     "subjects": [{"who": "a girl", "outfit": "apron"}],
                     "text_in_image": ["OPEN"]}
            yield {"type": "token", "text": json.dumps(sheet)}
        else:
            yield {"type": "token", "text": self.tags}

    async def generate_text_stream(self, prompt, **kw):
        self.text_prompts.append(prompt)
        if self.layout:
            yield {"type": "token", "text": self.layout.pop(0)}
        else:
            yield {"type": "token", "text": "An anime illustration. " + 'A girl stands under a sign reading "OPEN". ' * 30}

    async def embed(self, *a, **k):
        return [0.0]


class _Comfy:
    def load_workflow(self, name):
        return {"6": {"class_type": "EmptyFlux2LatentImage",
                      "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
                "16": {"class_type": "KSampler", "inputs": {"latent_image": ["6", 0]}}}

    def workflow_canvas(self, wf):
        return ComfyUIClient.workflow_canvas(ComfyUIClient.__new__(ComfyUIClient), wf)


async def _run(body, llm, tmp_path, comfy=None):
    from PIL import Image

    from backend.app import runtime_config
    from backend.app.jobs.runners import run_refine_prompt

    runtime_config._cache = None
    img = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), (200, 50, 200)).save(img)
    q = asyncio.Queue()
    try:
        await run_refine_prompt(_Reporter(), _Cancel(), body_dict={"sha256s": ["a"], "staging": "read", **body},
                                db=_DB(img), ollama=llm, spooler=None, comfy=comfy, token_queue=q)
    except RuntimeError:
        pass
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return [e for e in events if e]


_GOOD_LAYOUT = json.dumps({
    "caption": "An anime illustration of a shop front.",
    "boxes": [{"id": "person 1", "caption": "a girl in an apron", "box": [300, 200, 700, 1000]},
              {"id": "text 1", "caption": "red letters", "box": [350, 40, 650, 140]}],
})


@pytest.mark.asyncio
async def test_the_caption_runs_sheet_then_moss_caption(tmp_path):
    llm = _LLM()
    events = await _run({"prompt_style": "moss", "moss_format": "caption", "negative_prompt": True},
                        llm, tmp_path)
    done = next(e for e in events if e["type"] == "done")
    assert llm.text_prompts[0].startswith("Write the prompt for the MOSS-Image image model")
    assert '"OPEN"' in done["positive"]
    assert done["negative"] == moss.MOSS_NEGATIVE
    assert done["sheet"]["text_in_image"] == ["OPEN"]


@pytest.mark.asyncio
async def test_the_layout_uses_the_workflows_canvas(tmp_path):
    llm = _LLM(layout=[_GOOD_LAYOUT])
    events = await _run({"prompt_style": "moss", "moss_format": "layout",
                         "workflow_name": "API_MOSS_SFT.json"}, llm, tmp_path, comfy=_Comfy())
    done = next(e for e in events if e["type"] == "done")
    layout = json.loads(done["positive"])
    assert layout["items"][0]["bbox"] == moss.to_pixels([300, 200, 700, 1000], 832, 1216)
    assert layout["items"][1] | {"bbox": None} == {"label": "text", "caption": "red letters",
                                                    "text": "OPEN", "bbox": None}
    thinking = "".join(e["text"] for e in events if e["type"] == "think")
    assert "[layout canvas 832x1216 from API_MOSS_SFT.json]" in thinking
    # the draft is thinking, not the prompt pane
    assert not any(e["type"] == "token" and '"boxes"' in e["text"] for e in events)
    assert "832x1216 pixels (portrait)" in llm.text_prompts[0]


@pytest.mark.asyncio
async def test_an_unreadable_layout_is_asked_for_once_more(tmp_path):
    llm = _LLM(layout=["I would place the girl in the middle.", _GOOD_LAYOUT])
    events = await _run({"prompt_style": "moss", "moss_format": "layout"}, llm, tmp_path)
    done = next(e for e in events if e["type"] == "done")
    assert len(llm.text_prompts) == 2 and llm.text_prompts[1].startswith(moss.LAYOUT_RETRY_PREFIX)
    assert json.loads(done["positive"])["items"][0]["label"] == "character"
    thinking = "".join(e["text"] for e in events if e["type"] == "think")
    assert "[layout canvas 1024x1024 — no workflow canvas to read]" in thinking


@pytest.mark.asyncio
async def test_a_layout_unreadable_twice_stops_with_an_error(tmp_path):
    llm = _LLM(layout=["no", "still no"])
    events = await _run({"prompt_style": "moss", "moss_format": "layout"}, llm, tmp_path)
    assert not any(e["type"] == "done" for e in events)
    assert any(e["type"] == "error" and "layout" in e["message"] for e in events)


@pytest.mark.asyncio
async def test_tags_are_the_danbooru_call_in_moss_order(tmp_path):
    llm = _LLM(tags="1girl, blue_hair, masterpiece")
    events = await _run({"prompt_style": "moss", "moss_format": "tags", "negative_prompt": True},
                        llm, tmp_path)
    done = next(e for e in events if e["type"] == "done")
    # "solo" is the danbooru path's WD14 must-tag, kept.
    assert done["positive"] == "best quality, highres, masterpiece, 1girl, solo, blue hair"
    assert done["negative"] == moss.MOSS_NEGATIVE
    # the model was not asked for a negative of its own
    assert "NEGATIVE:" not in llm.vlm_prompts[0]
    assert done["sheet"] is None


def test_what_has_no_box_reaches_the_layout_summary():
    sheet = ss.normalize_sheet({"medium": "anime illustration", "setting": "a shrine",
                                "other": "a paper charm fluttering", "unclassified": ["star (symbol)"]})
    out, notes = moss.compile_layout({"caption": "An anime illustration of a shrine."}, [], sheet,
                                     width=1024, height=1024)
    assert out == ("An anime illustration of a shrine. Also in the picture: "
                   "a paper charm fluttering; star.")
