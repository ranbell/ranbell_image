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
        return {"path": str(self.path),
                "wd14_tags": ["2girls", "solo", "drinking_straw", "night_sky", "lens_flare"],
                "wd14_tags_scores": [0.9, 0.8, 0.5, 0.7, 0.6]}

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


# ── Editing the sheet and saying it again ────────────────────────────────────

def _stage():
    return ss.normalize_sheet({
        "medium": "anime illustration", "lighting": "purple spotlights",
        "setting": "an indoor concert stage",
        "subjects": [_girl("lead"), _girl("right", holding="a golden hoop"), _girl("left")],
    })


def test_a_patch_changes_only_what_it_names():
    sheet, changed, refused = ss.apply_patch(_stage(), {
        "set": {"setting": "an outdoor stage at night", "lighting": "moonlight and lanterns",
                "rating": "safe"},
        "subjects": [{"index": 1, "set": {"holding": "a tambourine in the right hand", "mood": "x"}}],
    })
    assert sheet["setting"] == "an outdoor stage at night"
    assert sheet["subjects"][1]["holding"] == "a tambourine in the right hand"
    assert sheet["subjects"][0] == _stage()["subjects"][0]
    assert sheet["camera"] == _stage()["camera"]
    assert "rating" not in sheet and "mood" not in sheet["subjects"][1]
    assert len(changed) == 3 and refused == []


def test_locked_boxes_and_people_are_refused():
    sheet, changed, refused = ss.apply_patch(_stage(), {
        "set": {"lighting": "daylight"},
        "subjects": [{"index": 0, "set": {"outfit": "a raincoat"}}],
        "remove_subjects": [0],
    }, locked=["lighting", "subjects.0"])
    assert sheet == _stage()
    assert changed == []
    assert set(refused) == {"lighting", "subjects.0.outfit", "subjects.0 (remove)"}


def test_adding_and_removing_people_moves_the_count():
    sheet, changed, _ = ss.apply_patch(_stage(), {
        "remove_subjects": [2],
        "add_subjects": [_girl("drummer"), _girl("bassist")],
    })
    assert sheet["subject_count"] == 4
    assert [s["who"] for s in sheet["subjects"]] == ["lead", "right", "drummer", "bassist"]
    assert any("removed" in c for c in changed)


def test_the_patch_prompt_names_the_locks_and_the_indexes():
    p = ss.patch_prompt(_stage(), "夜の屋外ステージにして", locked=["subjects.0", "camera"])
    assert "subjects.0, camera" in p
    assert '"index": 1' in p
    assert "夜の屋外ステージにして" in p


class _RetakeLLM(_LLM):
    """Answers a patch request with a patch, anything else with a script."""

    def __init__(self, patch):
        super().__init__()
        self.patch = patch

    async def generate_text_stream(self, prompt, **kw):
        self.text_prompts.append(prompt)
        if "Turn the request into a patch" in prompt:
            yield {"type": "token", "text": json.dumps(self.patch)}
        else:
            yield {"type": "token", "text": "An anime illustration of three girls on a stage at night."}


async def _run(body, llm, tmp_path):
    from PIL import Image

    from backend.app import runtime_config
    from backend.app.jobs.runners import run_refine_prompt

    runtime_config._cache = None
    img = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), (200, 50, 200)).save(img)
    q = asyncio.Queue()
    try:
        await run_refine_prompt(_Reporter(), _Cancel(), body_dict={"sha256s": ["a"],
                                "prompt_style": "detailed", **body}, db=_DB(img), ollama=llm,
                                spooler=None, comfy=None, token_queue=q)
    except RuntimeError:
        pass
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return [e for e in events if e]


@pytest.mark.asyncio
async def test_an_edited_sheet_is_not_read_again(tmp_path):
    llm = _RetakeLLM({})
    events = await _run({"sheet": _stage()}, llm, tmp_path)
    done = next(e for e in events if e["type"] == "done")
    assert llm.vlm_prompts == []
    assert len(llm.text_prompts) == 1
    assert done["sheet"]["subject_count"] == 3


@pytest.mark.asyncio
async def test_a_retake_patches_the_sheet_and_respects_the_locks(tmp_path):
    llm = _RetakeLLM({"set": {"setting": "an outdoor stage at night", "lighting": "moonlight"},
                      "subjects": [{"index": 0, "set": {"outfit": "a raincoat"}}]})
    events = await _run({"sheet": _stage(), "sheet_instruction": "夜の屋外にして",
                         "locked": ["subjects.0"]}, llm, tmp_path)
    done = next(e for e in events if e["type"] == "done")
    assert done["sheet"]["setting"] == "an outdoor stage at night"
    assert done["sheet"]["subjects"][0]["outfit"] == _stage()["subjects"][0]["outfit"]
    thinking = "".join(e["text"] for e in events if e["type"] == "think")
    assert "[changed] setting" in thinking and "[locked, not changed] subjects.0.outfit" in thinking
    # one call for the patch, one for the script, none for the picture
    assert len(llm.text_prompts) == 2 and llm.vlm_prompts == []
    # the patch never reaches the prompt pane
    assert not any(e["type"] == "token" and '"set"' in e["text"] for e in events)


@pytest.mark.asyncio
async def test_an_unreadable_retake_stops_instead_of_writing_the_old_sheet(tmp_path):
    llm = _RetakeLLM({})
    events = await _run({"sheet": _stage(), "sheet_instruction": "夜にして"}, llm, tmp_path)
    assert not any(e["type"] == "done" for e in events)
    assert any(e["type"] == "error" and "retake" in e["message"] for e in events)


def test_merging_or_keeping_the_people_of_several_references():
    merge = ss.sheet_prompt(reference_count=2, combine="merge")
    coexist = ss.sheet_prompt(reference_count=2, combine="coexist")
    assert "ONE person who combines" in merge and "sum" not in merge.split("Fuse")[1][:200]
    assert "sum of the images' people" in coexist and "which image they come from" in coexist
    single = ss.sheet_prompt(reference_count=1, combine="coexist")
    assert "Fuse" not in single and "Keep every main person" not in single


# ── Fusing worlds ────────────────────────────────────────────────────────────

import random as _random


def test_ingredients_skip_counts_ratings_and_noise_and_grow_with_chaos():
    pools = {"the first image": ["1girl", "general", "snow", "ice_crystal", "feathers", "watermark"],
             "the second image": ["bookshelf", "candle", "ink_bottle", "library"]}
    skip = {"1girl", "watermark"}
    low = ss.pick_ingredients(pools, 0.0, skip=skip, rng=_random.Random(1))
    high = ss.pick_ingredients(pools, 1.0, skip=skip, rng=_random.Random(1))
    assert all(len(w) == 2 for w in low.values())
    # ice_crystal and ink_bottle are unknown to the catalog, so they do not pass
    assert sorted(high["the first image"]) == ["feathers", "snow"]
    assert sorted(high["the second image"]) == ["bookshelf", "candle", "library"]
    assert not ({"1girl", "general", "watermark"} & {w for ws in high.values() for w in ws})


def test_the_fuse_prompt_asks_for_transformation_not_addition():
    p = ss.fuse_prompt(_stage(), {"the first image": ["snow"], "another world": ["clockwork"]},
                       0.9, locked=["camera"])
    assert "Fusion is not addition" in p
    assert "from the first image: snow" in p and "from another world: clockwork" in p
    assert "worlds collide" in p
    assert "camera" in p.split("Locked by the user")[1]


def test_a_fusion_keeps_who_people_are_and_how_many():
    fused, changed, refused = ss.apply_fusion(_stage(), {
        "set": {"setting": "a concert stage grown from ice"},
        "subjects": [{"index": 0, "set": {"appearance": "a robot", "outfit": "a frost-woven dress"}}],
        "add_subjects": [_girl("stranger")], "remove_subjects": [1],
    }, locked=["lighting"])
    assert fused["subject_count"] == 3
    assert fused["subjects"][0]["appearance"] == _stage()["subjects"][0]["appearance"]
    assert fused["subjects"][0]["outfit"] == "a frost-woven dress"
    assert fused["setting"] == "a concert stage grown from ice"
    # identity is held by design and not reported as the user's lock
    assert refused == []


class _FuseLLM(_LLM):
    async def generate_text_stream(self, prompt, **kw):
        self.text_prompts.append(prompt)
        if "Fuse them into the scene" in prompt:
            yield {"type": "token", "text": json.dumps(
                {"set": {"setting": "a stage frozen into a glacier"}})}
        else:
            yield {"type": "token", "text": "An anime illustration of five girls on a frozen stage."}


@pytest.mark.asyncio
async def test_fuse_mode_draws_wd14_ingredients_into_the_sheet(tmp_path):
    llm = _FuseLLM()
    events = await _run({"world_mode": "fuse", "divergence": 0.5}, llm, tmp_path)
    done = next(e for e in events if e["type"] == "done")
    thinking = "".join(e["text"] for e in events if e["type"] == "think")
    assert "[ingredients from the first image]" in thinking
    assert done["sheet"]["setting"] == "a stage frozen into a glacier"
    assert done["mutation_tags"] and "2girls" not in done["mutation_tags"]
    fuse_prompt = next(p for p in llm.text_prompts if "Fuse them into the scene" in p)
    assert "night sky" in fuse_prompt  # a WD14 world tag, in plain words
    assert "drinking straw" not in fuse_prompt  # an action tag cannot change a world


@pytest.mark.asyncio
async def test_blend_mode_uses_no_wd14(tmp_path):
    llm = _FuseLLM()
    events = await _run({"world_mode": "blend", "divergence": 0.5}, llm, tmp_path)
    assert not any("Fuse them" in p for p in llm.text_prompts)
    assert any(e["type"] == "think" and "only when worlds are fused" in e["text"] for e in events)


def test_world_tags_lead_and_pose_words_are_left_out():
    ranked = ss.world_tags(["hand_on_own_hip", "sneakers", "looking_at_viewer",
                            "blurry_background", "library", "snow", "smile", "lens_flare"])
    assert ranked[:3] == ["library", "snow", "lens_flare"]
    assert "sneakers" in ranked  # clothing fills in, last
    assert not {"hand_on_own_hip", "looking_at_viewer", "blurry_background", "smile"} & set(ranked)


def test_unused_ingredients_are_found():
    sheet = ss.normalize_sheet({"subjects": [_girl("lead")], "setting": "a library made of ice"})
    assert ss.unused_ingredients(sheet, {"a": ["library", "ice crystal", "night sky"]}) == ["night sky"]


def test_only_tags_the_catalog_knows_become_ingredients():
    ranked = ss.world_tags(["library", "green_eyes", "plugsuit", "some_unknown_word", "moon",
                            "depth_of_field", "jacket"])
    assert ranked == ["library", "moon", "jacket"]
