"""The Krea 2 visual script: sheet → script, and the checks after it.

The cases come from a five-girl idol stage that the old 8-section style
turned into "2girls, solo", a straw with no owner, and tags in place of verbs.
"""
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.app.prompt import scene_sheet as ss


def _girl(who, holding=""):
    return {"who": who, "position": "front", "appearance": "pink hair", "outfit": "sequin dress",
            "expression": "grinning", "pose_action": "reaching toward the viewer", "holding": holding}


def test_the_count_is_the_number_of_subjects_written_out():
    sheet = ss.normalize_sheet({
        "medium": "anime illustration", "subject_count": 2,
        "subjects": [_girl("lead"), _girl("left"), _girl("right"), _girl("back"), _girl("drummer")],
    })
    assert sheet["subject_count"] == 5


def test_the_screens_medium_wins_over_the_models():
    sheet = ss.normalize_sheet({"medium": "photograph", "subjects": [_girl("lead")]},
                               medium="anime illustration")
    assert sheet["medium"] == "anime illustration"


def test_unknown_boxes_are_dropped_and_lists_become_text():
    sheet = ss.normalize_sheet({
        "medium": "anime_illustration", "lighting": ["spotlight", "rim light"],
        "subjects": [_girl("lead") | {"extra": "x"}], "rating": "safe",
    })
    assert "rating" not in sheet and "extra" not in sheet["subjects"][0]
    assert sheet["lighting"] == "spotlight, rim light"
    assert sheet["medium"] == "anime illustration"


def test_requested_text_is_kept_in_the_sheet():
    sheet = ss.normalize_sheet({"subjects": [], "setting": "stage"}, literal_texts=["Hello"])
    assert sheet["text_in_image"] == ["Hello"]


def test_the_sheet_prompt_gives_objects_an_owner_and_fixes_the_medium():
    p = ss.sheet_prompt(medium="anime illustration", literal_texts=["Hello"], reference_count=3)
    assert '"holding"' in p and '"props"' in p
    assert 'fixed by the user: "anime illustration"' in p
    assert '"Hello"' in p
    assert "one scene" in p.lower()


def test_the_script_prompt_states_the_count_and_the_medium():
    sheet = ss.normalize_sheet({"medium": "anime illustration",
                                "subjects": [_girl(str(i)) for i in range(5)]})
    p = ss.script_prompt(sheet)
    assert 'Say "five"' in p
    assert '"anime illustration"' in p
    assert "masterpiece" in p  # named as a word to leave out


def test_tag_syntax_is_cleaned_out_of_the_script():
    raw = ("An anime illustration of five girls (multiple\\_girls, solo) on a "
           "(performance_stage). **The lead** holds a microphone.\n- She grins.")
    out = ss.clean_script(raw)
    assert "(" not in out and "_" not in out and "**" not in out
    assert out.startswith("An anime illustration of five girls on a")
    assert "She grins." in out


def test_a_missing_medium_is_put_at_the_front():
    sheet = ss.normalize_sheet({"medium": "anime illustration", "subjects": [_girl("lead")]})
    script, notes = ss.finish_script("A girl with pink hair sings on a stage.", sheet)
    assert script.startswith("An anime illustration. A girl")
    assert notes and "medium" in notes[0]


def test_a_missing_count_is_reported_not_rewritten():
    sheet = ss.normalize_sheet({"medium": "anime illustration",
                                "subjects": [_girl(str(i)) for i in range(5)]})
    script, notes = ss.finish_script("An anime illustration of girls singing on a stage.", sheet)
    assert script == "An anime illustration of girls singing on a stage."
    assert any("five" in n for n in notes)
    _, ok = ss.finish_script("An anime illustration of five girls singing.", sheet)
    assert ok == []


def test_requested_text_that_was_dropped_is_added_quoted():
    sheet = ss.normalize_sheet({"medium": "anime illustration", "subjects": [_girl("lead")]},
                               literal_texts=["Hello"])
    script, notes = ss.finish_script("An anime illustration of a girl.", sheet)
    assert script.endswith('The words "Hello" appear in the image.')
    script, notes = ss.finish_script('An anime illustration of a girl under a "Hello" banner.', sheet)
    assert notes == []


def test_empty_words_in_a_box_count_as_empty():
    sheet = ss.normalize_sheet({"subjects": [_girl("lead", holding="empty")], "props": "None."})
    assert sheet["subjects"][0]["holding"] == "" and sheet["props"] == ""


def test_a_lowercase_medium_opening_gets_its_article():
    sheet = ss.normalize_sheet({"medium": "anime illustration", "subjects": [_girl("lead")]})
    script, notes = ss.finish_script("anime illustration of a girl on a stage.", sheet)
    assert script == "An anime illustration of a girl on a stage." and notes == []


# ── The whole Refine job on the detailed style ───────────────────────────────

import asyncio
import io
import json

import pytest


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
        return {"path": str(self.path), "wd14_tags": ["2girls", "solo", "drinking_straw"],
                "wd14_tags_scores": [0.9, 0.8, 0.5]}

    async def get_config(self):
        return {}


class _LLM:
    """Answers the sheet step with a five-girl sheet and the script step with
    a script that still has tag syntax and a dropped article in it."""

    def __init__(self):
        self.vlm_prompts, self.text_prompts, self.embeds = [], [], 0

    async def generate_vlm_stream(self, prompt, images, **kw):
        self.vlm_prompts.append(prompt)
        sheet = {"medium": "photograph", "subject_count": 2, "props": "empty",
                 "subjects": [{"who": f"girl {i}", "holding": "none"} for i in range(5)],
                 "setting": "a concert stage"}
        yield {"type": "token", "text": json.dumps(sheet)}

    async def generate_text_stream(self, prompt, **kw):
        self.text_prompts.append(prompt)
        yield {"type": "token", "text": "anime illustration of five girls (multiple_girls) on a stage."}

    async def embed(self, *a, **k):
        self.embeds += 1
        return [0.0]


@pytest.mark.asyncio
async def test_the_detailed_style_runs_sheet_then_script(tmp_path):
    from PIL import Image

    from backend.app import runtime_config
    from backend.app.jobs.runners import run_refine_prompt

    runtime_config._cache = None
    img = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), (200, 50, 200)).save(img)
    llm, q = _LLM(), asyncio.Queue()
    body = {"sha256s": ["a"], "prompt_style": "detailed", "visual_medium": "anime",
            "negative_prompt": True, "divergence": 0.5, "suppress_conflict_tags": True,
            "instruction": "make it night"}
    await run_refine_prompt(_Reporter(), _Cancel(), body_dict=body, db=_DB(img), ollama=llm,
                            spooler=None, comfy=None, token_queue=q)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    done = next(e for e in events if e and e["type"] == "done")

    assert done["positive"] == "An anime illustration of five girls on a stage."
    assert done["negative"] == ""
    # The sheet is shown as thinking, never as the prompt.
    assert any(e and e["type"] == "think" and '"subjects"' in e["text"] for e in events)
    assert not any(e and e["type"] == "token" and '"subjects"' in e["text"] for e in events)
    # The screen's medium is what the writer was told, and the count is the list's.
    assert '"anime illustration"' in llm.text_prompts[0] and 'Say "five"' in llm.text_prompts[0]
    # No WD14 anywhere: not in the prompts, no conflict analysis, no mutation sampling.
    assert "drinking_straw" not in llm.vlm_prompts[0] + llm.text_prompts[0]
    assert llm.embeds == 0
    assert "make it night" in llm.vlm_prompts[0]


def test_the_length_follows_the_paragraph_slider():
    sheet = ss.normalize_sheet({"medium": "anime illustration",
                                "subjects": [_girl(str(i)) for i in range(5)]})
    assert "exactly 7 paragraphs" in ss.script_prompt(sheet, paragraphs=7)
    assert "exactly 3 paragraphs" in ss.script_prompt(sheet, paragraphs=1)  # clamped
    assert "about half of the paragraphs" in ss.script_prompt(sheet, paragraphs=6)


def test_detail_boxes_reach_the_writer():
    sheet = ss.normalize_sheet({
        "medium": "anime illustration", "layers": "ice shards in front, mountains behind",
        "effects": "snow flurries and light rays",
        "subjects": [_girl("lead") | {"details": "fur trim on the boots"}],
    })
    p = ss.script_prompt(sheet)
    assert "ice shards in front" in p and "snow flurries" in p and "fur trim" in p


def test_an_explained_empty_box_is_empty():
    sheet = ss.normalize_sheet({"subjects": [_girl("lead")],
                                "props": "None (all notable props are held by subjects)"})
    assert sheet["props"] == ""
    # A real value that merely starts like one is kept.
    sheet = ss.normalize_sheet({"subjects": [_girl("lead")], "props": "Nonelike banner"})
    assert sheet["props"] == "Nonelike banner"


def test_a_capitalised_medium_opening_gets_its_article_too():
    sheet = ss.normalize_sheet({"medium": "anime illustration", "subjects": [_girl("lead")]})
    script, _ = ss.finish_script("Anime illustration of a girl.", sheet)
    assert script == "An anime illustration of a girl."
