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
    # (the people's own boxes, dropped by this short script, are added after it)
    assert script.split("\n\n")[0] == "An anime illustration of girls singing on a stage."
    assert any("five" in n for n in notes)
    _, ok = ss.finish_script("An anime illustration of five girls singing.", sheet)
    assert not any("people" in n for n in ok)


def test_requested_text_that_was_dropped_is_added_quoted():
    sheet = ss.normalize_sheet({"medium": "anime illustration", "subjects": [_girl("lead")]},
                               literal_texts=["Hello"])
    script, notes = ss.finish_script("An anime illustration of a girl.", sheet)
    assert 'The words "Hello" appear in the image.' in script
    script, notes = ss.finish_script('An anime illustration of a girl under a "Hello" banner.', sheet)
    assert not any("text" in n for n in notes)


def test_empty_words_in_a_box_count_as_empty():
    sheet = ss.normalize_sheet({"subjects": [_girl("lead", holding="empty")], "props": "None."})
    assert sheet["subjects"][0]["holding"] == "" and sheet["props"] == ""


def test_a_lowercase_medium_opening_gets_its_article():
    sheet = ss.normalize_sheet({"medium": "anime illustration", "subjects": [_girl("lead")]})
    script, notes = ss.finish_script("anime illustration of a girl on a stage.", sheet)
    assert script.split("\n\n")[0] == "An anime illustration of a girl on a stage."
    assert not any("medium" in n for n in notes)


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


def _is_rebuild(prompt: str) -> bool:
    return prompt.startswith("You are given the ") and "[Ledger]" in prompt


def _rebuilt(prompt: str) -> str:
    """An identity reconstruction: the sheet handed back as it stands. Fakes
    that read a canned sheet would otherwise answer the last look with the
    reading and undo every step."""
    return json.dumps(json.loads(prompt.split("[Ledger]\n", 1)[1])["current"])


def _writes(llm) -> list[str]:
    """The writer's calls, minus the one that asks for what it left out (a
    fake script leaves most of the sheet out)."""
    return [p for p in llm.text_prompts if "[Your last draft left these out]" not in p]


class _LLM:
    """Answers the sheet step with a five-girl sheet and the script step with
    a script that still has tag syntax and a dropped article in it."""

    def __init__(self):
        self.vlm_prompts, self.text_prompts, self.embeds = [], [], 0
        self.rebuild_prompts = []

    async def generate_vlm_stream(self, prompt, images, **kw):
        if _is_rebuild(prompt):
            self.rebuild_prompts.append(prompt)
            yield {"type": "token", "text": _rebuilt(prompt)}
            return
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
    body = {"sha256s": ["a"], "prompt_style": "detailed", "visual_medium": "anime", "staging": "read",
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


class _TwoRefLLM(_LLM):
    """A different sheet per reference, so the merge can be seen to pick."""

    def __init__(self):
        super().__init__()
        self.images_seen = []

    async def generate_vlm_stream(self, prompt, images, **kw):
        if _is_rebuild(prompt):
            self.rebuild_prompts.append(prompt)
            yield {"type": "token", "text": _rebuilt(prompt)}
            return
        self.vlm_prompts.append(prompt)
        self.images_seen.append(list(images))
        n = len(self.vlm_prompts)
        yield {"type": "token", "text": json.dumps({
            "medium": "photograph",
            "setting": "a concert stage" if n == 1 else "a neon alley",
            "lighting": "stage lights" if n == 1 else "neon signage",
            "subjects": [{"who": "a girl" if n == 1 else "a boy",
                          "appearance": "silver hair" if n == 1 else "black hair"}],
        })}

    async def generate_text_stream(self, prompt, **kw):
        self.text_prompts.append(prompt)
        if "return the result as a patch" in prompt:
            yield {"type": "token", "text": json.dumps({"set": {"situation": "a quiet moment"}})}
        else:
            yield {"type": "token", "text": "An anime illustration of a girl on a stage."}


def img_bytes(tmp_path):
    """The reference's own bytes — what a per-image read must receive."""
    return (tmp_path / "ref.png").read_bytes()


async def _two_ref_run(body_extra, tmp_path):
    from PIL import Image

    from backend.app import runtime_config
    from backend.app.jobs.runners import run_refine_prompt

    runtime_config._cache = None
    img = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), (200, 50, 200)).save(img)
    llm, q = _TwoRefLLM(), asyncio.Queue()
    body = {"prompt_style": "detailed", "visual_medium": "anime", "staging": "read",
            "combine_mode": "merge", "wd14_hints": False, **body_extra}
    await run_refine_prompt(_Reporter(), _Cancel(), body_dict=body, db=_DB(img), ollama=llm,
                            spooler=None, comfy=None, token_queue=q)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return llm, events


@pytest.mark.asyncio
async def test_each_reference_is_read_on_its_own_and_the_mix_picks(tmp_path):
    """Two references at 80/20, read separately, merged in code.

    The weights are no longer a paragraph the reader is asked to honour: the
    person is TAKEN from the heavier sheet.
    """
    llm, events = await _two_ref_run({"sha256s": ["a", "b"], "weights": [0.8, 0.2]}, tmp_path)
    thinking = "".join(e["text"] for e in events if e and e["type"] == "think")
    done = next(e for e in events if e and e["type"] == "done")

    # One call per reference, each holding a single image — not a tile.
    assert len(llm.images_seen) == 2
    assert all(len(imgs) == 1 for imgs in llm.images_seen)
    assert llm.images_seen[0][0] == img_bytes(tmp_path)
    # A single-image read, so no tile instructions and no mix paragraph.
    assert "a tile of several images" not in llm.vlm_prompts[0]
    assert "[The mix]" not in llm.vlm_prompts[0]
    # The heavier reference's person, verbatim; the sheet says so on screen.
    assert done["sheet"]["subjects"][0]["appearance"] == "silver hair"
    assert "[the mix] the first image 80% lead / the second image 20%" in thinking
    assert "[from]" in thinking


@pytest.mark.asyncio
async def test_reversing_the_weights_reads_the_person_from_the_other_reference(tmp_path):
    llm, events = await _two_ref_run({"sha256s": ["a", "b"], "weights": [0.2, 0.8]}, tmp_path)
    done = next(e for e in events if e and e["type"] == "done")
    assert done["sheet"]["subjects"][0]["appearance"] == "black hair"


@pytest.mark.asyncio
async def test_a_zero_weight_reference_is_never_even_read(tmp_path):
    llm, events = await _two_ref_run({"sha256s": ["a", "b"], "weights": [1.0, 0.0]}, tmp_path)
    done = next(e for e in events if e and e["type"] == "done")
    # It is not shown to the reader at all, so it needs no waving off in words.
    assert len(llm.images_seen) == 1
    assert done["sheet"]["setting"] == "a concert stage"
    assert not any("return the result as a patch" in p for p in llm.text_prompts)


@pytest.mark.asyncio
async def test_the_reconcile_step_cannot_undo_the_mix(tmp_path):
    llm, events = await _two_ref_run({"sha256s": ["a", "b"], "weights": [0.8, 0.2]}, tmp_path)
    done = next(e for e in events if e and e["type"] == "done")
    assert any("return the result as a patch" in p for p in llm.text_prompts)
    # It was allowed what the weights did not decide...
    assert done["sheet"]["situation"] == "a quiet moment"
    # ...and the owned boxes stand.
    assert done["sheet"]["subjects"][0]["appearance"] == "silver hair"


@pytest.mark.asyncio
async def test_a_repair_run_reads_no_picture_and_calls_out_the_missing_boxes(tmp_path):
    """The one-click repair: same sheet, boxes named, no reference re-read.

    That is what makes closing the loop cheap — a repair is one text call.
    """
    from PIL import Image

    from backend.app import runtime_config
    from backend.app.jobs.runners import run_refine_prompt

    runtime_config._cache = None
    img = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), (200, 50, 200)).save(img)
    llm, q = _LLM(), asyncio.Queue()
    sheet = ss.normalize_sheet({
        "medium": "anime illustration", "setting": "a stage",
        "props": "a cracked ring on the floor",
        "subjects": [{"who": "a girl",
                      "hands": "her right hand is hooked over the window frame"}],
    })
    body = {"sha256s": ["a"], "prompt_style": "detailed", "staging": "read",
            "sheet": sheet, "emphasise": ["props", "hands"]}
    await run_refine_prompt(_Reporter(), _Cancel(), body_dict=body, db=_DB(img), ollama=llm,
                            spooler=None, comfy=None, token_queue=q)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    thinking = "".join(e["text"] for e in events if e and e["type"] == "think")

    # The sheet was handed over, so no picture was looked at.
    assert llm.vlm_prompts == []
    script = next(p for p in llm.text_prompts if "[Scene sheet]" in p)
    assert "[These did not reach the last picture]" in script
    assert "- props: a cracked ring on the floor" in script
    assert "- hands (a girl): her right hand is hooked over the window frame" in script
    assert "[emphasised — missing from the last picture]" in thinking


@pytest.mark.asyncio
async def test_one_reference_is_read_once_and_never_merged(tmp_path):
    llm, events = await _two_ref_run({"sha256s": ["a"], "weights": [1.0]}, tmp_path)
    assert len(llm.images_seen) == 1
    assert not any("return the result as a patch" in p for p in llm.text_prompts)


def test_the_length_follows_the_paragraph_slider():
    sheet = ss.normalize_sheet({"medium": "anime illustration",
                                "subjects": [_girl(str(i)) for i in range(5)]})
    assert "exactly 7 paragraphs" in ss.script_prompt(sheet, paragraphs=7)
    assert "exactly 3 paragraphs" in ss.script_prompt(sheet, paragraphs=1)  # clamped
    assert "the action and the people about two thirds of the paragraphs" in ss.script_prompt(sheet, paragraphs=6)


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
    assert script.split("\n\n")[0] == "An anime illustration of a girl."


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
    # A box the sheet does not have is not ignored: it lands in "other".
    assert sheet["other"] == "rating: safe; right — mood: x"
    assert len(changed) == 5 and refused == []


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
            yield {"type": "token", "text": "An anime illustration of three girls on a stage at night. "
                   + "They sing under the lights. " * 30}


async def _run(body, llm, tmp_path):
    from PIL import Image

    from backend.app import runtime_config
    from backend.app.jobs.runners import run_refine_prompt

    runtime_config._cache = None
    img = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), (200, 50, 200)).save(img)
    q = asyncio.Queue()
    try:
        # Staging has its own tests; these runs read the sheet as it is.
        await run_refine_prompt(_Reporter(), _Cancel(), body_dict={"sha256s": ["a"],
                                "prompt_style": "detailed", "staging": "read", **body}, db=_DB(img), ollama=llm,
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
    assert len(_writes(llm)) == 1
    # Nothing changed the sheet, so there is nothing to rebuild.
    assert llm.rebuild_prompts == []
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
    # one call for the patch, one for the script, none to read the picture again —
    # only the last look, which rebuilds the patched sheet with the reference
    assert len(_writes(llm)) == 2 and llm.vlm_prompts == []
    assert len(llm.rebuild_prompts) == 1 and '"step": "retake"' in llm.rebuild_prompts[0]
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


# ── The mix: the weight sliders as an editorial decision ─────────────────────
#
# The Showrunner (2026-11-01): "the mixing ratio the slider sets does not mean
# very much".
# The sliders used to reach this style only as a word count inside the hints,
# so the reader was never told which reference the picture belongs to.


def test_the_lead_reference_owns_the_person():
    plan = ss.build_mix_plan([0.6, 0.3, 0.1], combine="merge")
    assert plan["identity_panel"] == 0 and plan["palette_panel"] == 0
    assert [p["band"] for p in plan["panels"]] == ["lead", "secondary", "accent"]
    assert plan["active"] == 3


def test_a_style_marked_panel_owns_the_light_even_when_it_is_lighter():
    # The role button is an assignment, not a filter: a panel marked "style
    # only" exists to supply the place and the light, so it owns them.
    plan = ss.build_mix_plan([0.6, 0.3], roles=["both", "style"], combine="merge")
    assert plan["identity_panel"] == 0 and plan["palette_panel"] == 1


def test_a_content_marked_panel_never_owns_the_light():
    plan = ss.build_mix_plan([0.6, 0.4], roles=["content", "both"], combine="merge")
    assert plan["identity_panel"] == 0 and plan["palette_panel"] == 1


def test_without_a_role_the_lead_owns_both():
    # Nothing says whether the second panel is a scene or another person, and
    # handing it the setting on a guess is how references get averaged.
    plan = ss.build_mix_plan([0.6, 0.4], combine="merge")
    assert plan["identity_panel"] == 0 and plan["palette_panel"] == 0


def test_a_tie_has_only_one_lead():
    plan = ss.build_mix_plan([0.5, 0.5], combine="merge")
    assert [p["band"] for p in plan["panels"]] == ["lead", "secondary"]


def test_a_zero_weight_panel_is_marked_ignored():
    # It is not read at all now, so nothing has to wave it off in words.
    plan = ss.build_mix_plan([0.7, 0.3, 0.0], combine="merge")
    assert plan["panels"][2]["band"] == "ignored" and plan["active"] == 2


def test_the_summary_says_what_each_panel_is():
    # The one line the Showrunner reads on screen to see the dial landed.
    plan = ss.build_mix_plan([0.6, 0.3, 0.1], roles=["both", "style", "both"])
    assert ss.mix_summary(plan) == (
        "the first image 60% lead / the second image 30% secondary style"
        " / the third image 10% accent"
    )


def test_the_panel_labels_of_the_mix_and_the_hints_are_the_same():
    plan = ss.build_mix_plan([0.1, 0.6], combine="merge")
    hints, _ = ss.hint_tags(
        {"unique_by_image": {"0": {"must": ["orange_hair"], "ref": []},
                             "1": {"must": ["lace_trim"], "ref": []}}},
        shares=[0.1, 0.6],
    )
    mix_labels = [p["label"] for p in plan["panels"]]
    hint_labels = [k.split(" (")[0] for k in hints]
    assert mix_labels == ["the first image", "the second image"]
    assert sorted(hint_labels) == sorted(mix_labels)


def test_hints_are_labelled_with_their_share_and_the_heaviest_comes_first():
    analysis = {"common_tags": ["witch_hat"],
                "unique_by_image": {"0": {"must": ["orange_hair"], "ref": []},
                                    "1": {"must": ["lace_trim"], "ref": []}}}
    hints, _ = ss.hint_tags(analysis, shares=[0.25, 0.75])
    assert list(hints) == ["in every image",
                           "the second image (75% of the mix)",
                           "the first image (25% of the mix)"]


def test_hints_without_shares_keep_their_old_labels_and_order():
    analysis = {"unique_by_image": {"0": {"must": ["orange_hair"], "ref": []},
                                    "1": {"must": ["lace_trim"], "ref": []}}}
    hints, _ = ss.hint_tags(analysis)
    assert list(hints) == ["the first image", "the second image"]


def test_ingredients_follow_the_shares():
    # Chaos says how many there are; the weights say whose they are.
    pools = {"the first image": ["snow", "ice", "feathers", "frost", "wind", "star"],
             "the second image": ["bookshelf", "candle", "ink", "library", "quill"],
             "the third image": ["neon", "rain", "asphalt", "sign"],
             "another world": ["clockwork", "brass", "steam", "gears"]}
    got = ss.pick_ingredients(
        pools, 0.5, rng=_random.Random(1),
        shares={"the first image": 0.6, "the second image": 0.3, "the third image": 0.1},
    )
    assert len(got["the first image"]) == 3
    assert len(got["the second image"]) == 2
    # A weighted source never drops to nothing, however light it is.
    assert len(got["the third image"]) == 1
    # "another world" is nobody's reference, so it keeps the full count.
    assert len(got["another world"]) == 2 + round(0.5 * 6) - 1  # pool has only 4


def test_ingredients_without_shares_stay_even():
    pools = {"the first image": ["snow", "ice", "feathers", "frost", "wind", "star"],
             "the second image": ["bookshelf", "candle", "ink", "library", "quill"]}
    got = ss.pick_ingredients(pools, 0.5, rng=_random.Random(1))
    assert len(got["the first image"]) == 5 and len(got["the second image"]) == 5


# ── One sheet per reference, merged in code ──────────────────────────────────
#
# The tile gave each of six references 336x504 pixels and asked one call to
# perceive all of them at once. Read separately, the weights stop being a
# paragraph of English a model is asked to honour and become an assignment.


def _ref_sheet(who, appearance, setting, lighting, *, props="", texts=None,
               outfit="a coat", situation=""):
    return ss.normalize_sheet({
        "medium": "anime illustration", "rendering": "soft cel shading",
        "setting": setting, "lighting": lighting, "palette_mood": "warm",
        "props": props, "text_in_image": texts or [],
        "situation": situation or f"{who} is in the middle of something",
        "subjects": [{"who": who, "appearance": appearance, "outfit": outfit,
                      "pose_action": "reaching out", "position": "front centre"}],
    })


_A = _ref_sheet("a girl", "silver hair, violet eyes", "a frozen stage",
                "stage lights", props="a microphone", texts=["ON AIR"])
_B = _ref_sheet("a boy", "black hair", "a neon alley", "neon signage",
                props="a vending machine", texts=["RAMEN"], outfit="a parka")


def test_the_identity_sheet_owns_who_and_appearance():
    plan = ss.build_mix_plan([0.7, 0.3], combine="merge")
    merged, _ = ss.merge_sheets([(0, _A), (1, _B)], plan)
    assert merged["subjects"][0]["who"] == "a girl"
    assert merged["subjects"][0]["appearance"] == "silver hair, violet eyes"
    assert merged["subject_count"] == 1


def test_a_style_marked_reference_gives_the_place_and_the_light_not_the_person():
    # The role button reaches the merge, so it reaches the picture.
    plan = ss.build_mix_plan([0.7, 0.3], roles=["both", "style"], combine="merge")
    merged, _ = ss.merge_sheets([(0, _A), (1, _B)], plan)
    assert merged["subjects"][0]["appearance"] == "silver hair, violet eyes"
    assert merged["setting"] == "a neon alley" and merged["lighting"] == "neon signage"


def test_reversing_the_slider_swaps_the_person_verbatim():
    # The point of the whole change: not a different instruction, a different value.
    one, _ = ss.merge_sheets([(0, _A), (1, _B)], ss.build_mix_plan([0.8, 0.2]))
    other, _ = ss.merge_sheets([(0, _A), (1, _B)], ss.build_mix_plan([0.2, 0.8]))
    assert one["subjects"][0]["who"] == "a girl"
    assert other["subjects"][0]["who"] == "a boy"


def test_additive_boxes_keep_every_reference_heaviest_first():
    plan = ss.build_mix_plan([0.7, 0.3], combine="merge")
    merged, _ = ss.merge_sheets([(0, _A), (1, _B)], plan)
    assert merged["props"] == "a microphone; a vending machine"
    assert merged["text_in_image"] == ["ON AIR", "RAMEN"]


def test_coexist_keeps_every_person_and_recounts():
    plan = ss.build_mix_plan([0.7, 0.3], combine="coexist")
    merged, _ = ss.merge_sheets([(0, _A), (1, _B)], plan)
    assert [s["who"] for s in merged["subjects"]] == ["a girl", "a boy"]
    assert merged["subject_count"] == 2


def test_provenance_says_where_each_box_came_from():
    plan = ss.build_mix_plan([0.7, 0.3], roles=["both", "style"], combine="merge")
    _, prov = ss.merge_sheets([(0, _A), (1, _B)], plan)
    joined = "\n".join(prov)
    assert "← the second image" in joined and "← the first image" in joined
    assert "setting" in joined and "subjects" in joined


def test_one_reference_is_returned_untouched_with_nothing_to_merge():
    plan = ss.build_mix_plan([1.0])
    merged, prov = ss.merge_sheets([(0, _A)], plan)
    assert merged is _A and prov == []


def test_a_zero_share_reference_contributes_nothing_to_the_merge():
    plan = ss.build_mix_plan([1.0, 0.0], combine="merge")
    merged, _ = ss.merge_sheets([(0, _A), (1, _B)], plan)
    assert "vending machine" not in merged["props"]
    assert "RAMEN" not in merged["text_in_image"]


def test_the_reconcile_patch_cannot_break_the_mix():
    # The lock is what makes the dial unbreakable: before, the mix was English
    # a model could average away.
    plan = ss.build_mix_plan([0.7, 0.3], roles=["both", "style"], combine="merge")
    merged, _ = ss.merge_sheets([(0, _A), (1, _B)], plan)
    locks = ss.merge_locks(merged, plan)
    patch = {"set": {"setting": "somewhere else", "situation": "a quiet moment"},
             "subjects": [{"index": 0, "set": {"appearance": "blonde hair",
                                               "outfit": "one coherent parka"}}]}
    out, changed, refused = ss.apply_patch(merged, patch, locks)
    # Refused: the boxes the weights assigned.
    assert out["setting"] == "a neon alley"
    assert out["subjects"][0]["appearance"] == "silver hair, violet eyes"
    assert "setting" in refused and "subjects.0.appearance" in refused
    # Allowed: what the weights did not decide.
    assert out["situation"] == "a quiet moment"
    assert out["subjects"][0]["outfit"] == "one coherent parka"


def test_coexist_locks_every_persons_identity():
    plan = ss.build_mix_plan([0.7, 0.3], combine="coexist")
    merged, _ = ss.merge_sheets([(0, _A), (1, _B)], plan)
    locks = ss.merge_locks(merged, plan)
    assert "subjects.0.appearance" in locks and "subjects.1.appearance" in locks


def test_the_reconcile_prompt_asks_for_what_the_weights_did_not_decide():
    plan = ss.build_mix_plan([0.7, 0.3], combine="merge")
    merged, _ = ss.merge_sheets([(0, _A), (1, _B)], plan)
    p = ss.reconcile_prompt(merged, plan)
    assert "they are locked" in p
    assert "ONE outfit a person could actually wear" in p
    assert '"index": 0' in p
    # coexist has a different job: placing a cast that has never met.
    c = ss.reconcile_prompt(merged, ss.build_mix_plan([0.7, 0.3], combine="coexist"))
    assert "never been in the same picture" in c and "most prominent" in c


def test_hints_for_an_image_are_its_own_tags_plus_the_shared_ones():
    analysis = {"common_tags": ["witch_hat", "masterpiece"],
                "unique_by_image": {"0": {"must": ["orange_hair"], "ref": ["freckles"]},
                                    "1": {"must": ["lace_trim"], "ref": []}}}
    dropped = []
    first = ss.hints_for_image(analysis, 0, skip={"1girl"}, dropped=dropped)
    second = ss.hints_for_image(analysis, 1)
    assert first == {"the reference": ["orange hair", "freckles", "witch hat"]}
    assert second == {"the reference": ["lace trim", "witch hat"]}
    # The same three exclusions, reported the same way.
    assert dropped == [("masterpiece", "quality")]


def test_an_image_the_tagger_had_nothing_for_gets_no_hints():
    assert ss.hints_for_image({"unique_by_image": {"0": {"must": [], "ref": []}}}, 0) == {}


# ── Checking the finished picture against its sheet ──────────────────────────
#
# `uncovered` checks that the WRITER kept every box; nothing checked that
# the IMAGE MODEL did. A check that cries wolf is worse than no check, so the
# calibration below (a faithful render reported clean) is the real test.


_INTENDED = ss.normalize_sheet({
    "medium": "anime illustration",
    "rendering": "soft cel shading with visible line work",
    "situation": "she has just turned away from the window",
    "setting": "a cramped apartment at dusk",
    "lighting": "warm sunset through dirty glass",
    "palette_mood": "amber and dust",
    "camera": "waist-up from slightly below",
    "props": "a cracked ring on the floor",
    "effects": "dust motes in the light",
    "layers": "the window behind her, the table in front",
    "subjects": [{
        "who": "a young woman", "appearance": "silver hair, violet eyes",
        "outfit": "a grey wool coat",
        "hands": "her right hand is hooked over the rusted window frame",
        "motion": "the hem of her coat still swinging from the turn",
        "legs_feet": "weight dropped onto the back foot",
        "gaze": "looking back over her shoulder",
        "expression": "brows drawn, mouth closed",
        "pose_action": "twisting her torso away from the glass",
    }],
})

# The same picture, read back in the reader's own words.
_FAITHFUL = ss.normalize_sheet({
    "medium": "an anime illustration", "rendering": "cel shaded, clear lineart",
    "situation": "she has turned from the window",
    "setting": "a small apartment room at dusk",
    "lighting": "warm sunset light through grimy glass",
    "palette_mood": "amber tones, dusty", "camera": "waist up, low angle",
    "props": "a cracked ring lying on the floor",
    "effects": "dust motes drifting in the light",
    "layers": "window behind, table in front",
    "subjects": [{
        "who": "a woman", "appearance": "silver haired with violet eyes",
        "outfit": "grey wool coat",
        "hands": "right hand hooked over the rusty window frame",
        "motion": "coat hem swinging from her turn",
        "legs_feet": "weight on the back foot",
        "gaze": "glancing back over her shoulder",
        "expression": "brows drawn, lips closed",
        "pose_action": "twisting her torso from the glass",
    }],
})


def test_a_faithful_render_is_reported_clean():
    # The calibration that decides whether this check is worth having.
    missed, checked = ss.compare_sheets(_INTENDED, _FAITHFUL)
    assert missed == [], [f"{m['box']}: {m['value']}" for m in missed]
    assert checked == 17


def test_rendering_is_not_checked_because_it_is_always_reworded():
    # "soft cel shading with visible line work" vs "cel shaded, clear lineart"
    # is the same picture and scored 0.6 missing. medium carries what matters.
    assert "rendering" not in ss._CHECK_SCENE_BOXES
    assert "medium" in ss._CHECK_SCENE_BOXES


def test_a_box_that_landed_in_a_different_box_still_counts_as_reached():
    # The reader of a finished picture files things where it likes; comparing
    # box to box would report every one of those as a loss.
    got = ss.normalize_sheet({
        "medium": "anime illustration",
        "situation": "a woman twisting away from the window, her right hand "
                     "hooked over the rusted frame",
        "subjects": [{"who": "a woman"}],
    })
    missed, _ = ss.compare_sheets(
        {"medium": "anime illustration",
         "subjects": [{"who": "a woman",
                       "hands": "her right hand is hooked over the rusted window frame"}]},
        got,
    )
    assert missed == []


def test_a_photograph_instead_of_an_anime_illustration_is_reported():
    missed, _ = ss.compare_sheets(_INTENDED, {**_FAITHFUL, "medium": "photograph"})
    assert [m["box"] for m in missed] == ["medium"]
    assert missed[0]["value"] == "anime illustration" and missed[0]["person"] is None


def test_a_picture_that_went_stiff_and_lost_its_props_is_reported():
    # The real failure this check exists for: Krea 2 renders the looks and the
    # place but puts the body back at rest, which is the 棒立ち regression.
    got = ss.normalize_sheet({
        "medium": "photograph", "situation": "she stands by the window",
        "setting": "a small apartment room at dusk",
        "lighting": "warm sunset light through grimy glass",
        "palette_mood": "amber tones, dusty", "camera": "waist up, low angle",
        "props": "", "effects": "dust motes drifting in the light",
        "layers": "window behind, table in front",
        "subjects": [{
            "who": "a woman", "appearance": "silver haired with violet eyes",
            "outfit": "grey wool coat", "hands": "both arms resting at her sides",
            "motion": "", "legs_feet": "standing evenly",
            "gaze": "glancing back over her shoulder",
            "expression": "brows drawn, lips closed",
            "pose_action": "standing by the glass",
        }],
    })
    missed, checked = ss.compare_sheets(_INTENDED, got)
    boxes = {m["box"]: m for m in missed}
    assert checked == 17
    # The medium flipped, the moment went, the ring vanished...
    assert {"medium", "situation", "props"} <= set(boxes)
    # ...and every box that said what the body was doing.
    assert {"pose_action", "hands", "legs_feet", "motion"} <= set(boxes)
    # What actually survived is not reported.
    assert "appearance" not in boxes and "lighting" not in boxes and "gaze" not in boxes
    assert boxes["hands"]["person"] == 1 and boxes["hands"]["who"] == "a young woman"
    assert boxes["props"]["person"] is None


def test_who_is_never_reported():
    got = ss.normalize_sheet({"medium": "anime illustration",
                              "subjects": [{"who": "a female figure, young adult"}]})
    missed, _ = ss.compare_sheets(
        {"medium": "anime illustration", "subjects": [{"who": "a young woman"}]}, got)
    assert missed == []


def test_an_empty_box_is_neither_checked_nor_reported():
    missed, checked = ss.compare_sheets({"setting": "", "props": "a ring"},
                                        {"setting": "a ring on the floor"})
    assert missed == [] and checked == 1


def test_emphasis_lines_take_their_values_from_the_sheet():
    lines = ss.emphasis_lines(_INTENDED, ["props", "hands", "nonexistent"])
    assert lines[0] == "props: a cracked ring on the floor"
    assert lines[1] == ("hands (a young woman): her right hand is hooked over "
                        "the rusted window frame")
    assert len(lines) == 2


def test_the_script_prompt_calls_out_what_the_last_picture_dropped():
    plain = ss.script_prompt(_INTENDED)
    assert "did not reach the last picture" not in plain.lower()
    marked = ss.script_prompt(_INTENDED, emphasise=ss.emphasis_lines(_INTENDED, ["hands"]))
    assert "[These did not reach the last picture]" in marked
    assert "- hands (a young woman): her right hand is hooked" in marked
    assert "Being written once was already not enough for them" in marked
    # It must not become licence to drop the rest.
    assert "do not drop anything else to make room" in marked


# ── Fusing worlds ────────────────────────────────────────────────────────────

import random as _random


def test_ingredients_leave_out_only_quality_ratings_counts_and_the_floor_and_grow_with_chaos():
    # The Showrunner (2026-10-31): only quality, person-count and under-the-floor
    # tags are left out of a fusion; unknown words, ratings and the rest are used.
    pools = {"the first image": ["1girl", "general", "snow", "ice_crystal", "feathers", "watermark",
                                 "masterpiece", "sword"],
             "the second image": ["bookshelf", "candle", "ink_bottle", "library"]}
    dropped = []
    low = ss.pick_ingredients(pools, 0.0, skip={"1girl"}, rng=_random.Random(1))
    high = ss.pick_ingredients(pools, 1.0, skip={"1girl"}, rng=_random.Random(1),
                               scores={"sword": 0.4, "snow": 0.9}, min_score=0.5, dropped=dropped)
    assert all(len(w) == 2 for w in low.values())
    assert sorted(high["the first image"]) == ["feathers", "ice crystal", "snow", "watermark"]
    assert sorted(high["the second image"]) == ["bookshelf", "candle", "ink bottle", "library"]
    # A rating says how the file is rated, not what the picture shows.
    assert sorted(dropped) == [("1girl", "person count"), ("general", "rating"),
                               ("masterpiece", "quality"), ("sword", "confidence 0.40")]


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
    # One reference is "the reference", here as in the hints: the panel labels
    # all come from scene_sheet.panel_label, so they cannot drift apart.
    assert "[ingredients from the reference]" in thinking
    assert done["sheet"]["setting"] == "a stage frozen into a glacier"
    assert done["mutation_tags"] and "2girls" not in done["mutation_tags"]
    fuse_prompt = next(p for p in llm.text_prompts if "Fuse them into the scene" in p)
    assert "night sky" in fuse_prompt  # a WD14 world tag, in plain words
    # Only the person counts are left out of the fusion, and that is said.
    assert "[left out of the fusion] " in thinking and "2girls (person count)" in thinking


@pytest.mark.asyncio
async def test_blend_mode_uses_no_wd14(tmp_path):
    llm = _FuseLLM()
    events = await _run({"world_mode": "blend", "divergence": 0.5}, llm, tmp_path)
    assert not any("Fuse them" in p for p in llm.text_prompts)
    assert any(e["type"] == "think" and "only when worlds are fused" in e["text"] for e in events)


def test_world_tags_lead_and_the_rest_follow():
    ranked = ss.world_tags(["hand_on_own_hip", "sneakers", "looking_at_viewer",
                            "blurry_background", "library", "snow", "smile", "lens_flare"])
    assert ranked[:3] == ["library", "snow", "lens_flare"]
    assert ranked[3] == "sneakers"  # clothing next
    # pose and framing words are no longer thrown away — they come last
    assert {"hand_on_own_hip", "looking_at_viewer", "blurry_background", "smile"} <= set(ranked[4:])


def test_unused_ingredients_are_found():
    sheet = ss.normalize_sheet({"subjects": [_girl("lead")], "setting": "a library made of ice"})
    assert ss.unused_ingredients(sheet, {"a": ["library", "ice crystal", "night sky"]}) == ["night sky"]


def test_tags_the_catalog_does_not_know_are_kept_after_the_world():
    ranked = ss.world_tags(["library", "green_eyes", "plugsuit", "some_unknown_word", "moon",
                            "depth_of_field", "jacket"])
    assert ranked == ["library", "moon", "jacket",
                      "green_eyes", "plugsuit", "some_unknown_word", "depth_of_field"]


def test_a_medium_run_into_the_next_sentence_gets_its_full_stop():
    sheet = ss.normalize_sheet({"medium": "anime illustration", "subjects": [_girl("lead")]})
    script, _ = ss.finish_script("anime illustration A young woman stands.", sheet)
    assert script.split("\n\n")[0] == "An anime illustration. A young woman stands."
    script, _ = ss.finish_script("Anime illustration: A young woman stands.", sheet)
    assert script.split("\n\n")[0] == "An anime illustration: A young woman stands."


class _ShortThenLongLLM(_LLM):
    async def generate_text_stream(self, prompt, **kw):
        self.text_prompts.append(prompt)
        if prompt.startswith(ss.SCRIPT_RETRY_PREFIX):
            yield {"type": "token", "text": "An anime illustration. " + "A girl sings on a stage. " * 40}
        else:
            yield {"type": "token", "text": "An anime illustration."}


@pytest.mark.asyncio
async def test_a_short_script_is_asked_for_once_more(tmp_path):
    llm = _ShortThenLongLLM()
    events = await _run({"sheet": _stage()}, llm, tmp_path)
    done = next(e for e in events if e["type"] == "done")
    assert len(_writes(llm)) == 2
    assert len(done["positive"].split()) > 100 and done["prose_missing"] is False
    assert any(e["type"] == "think" and "came back short" in e["text"] for e in events)


# ── WD14 hints: the references' top tags, handed to the sheet reader ─────────

def test_hint_tags_leave_out_only_quality_ratings_and_counts():
    analysis = {
        "common_tags": ["witch_hat", "1girl", "general", "masterpiece"],
        "unique_by_image": {
            "1": {"must": ["lace_trim", "simple_background", "hatsune_miku"], "ref": ["star_(symbol)", "no_humans"]},
            "0": {"must": ["orange_hair", "score_9"], "ref": []},
        },
    }
    hints, dropped = ss.hint_tags(analysis, skip={"1girl"})
    assert dropped == [("1girl", "person count"), ("general", "rating"), ("masterpiece", "quality"),
                       ("score 9", "quality")]
    assert hints == {
        "in every image": ["witch hat"],
        "the first image": ["orange hair"],
        "the second image": ["lace trim", "simple background", "hatsune miku", "star (symbol)", "no humans"],
    }


def test_one_reference_is_called_the_reference():
    hints, _ = ss.hint_tags({"unique_by_image": {"0": {"must": ["lace_trim"], "ref": []}}})
    assert hints == {"the reference": ["lace trim"]}


def test_a_low_confidence_tag_is_removed_in_code():
    analysis = {"unique_by_image": {"0": {"must": ["witch_hat"], "ref": ["sword", "moon"]}}}
    hints, dropped = ss.hint_tags(analysis, scores={"witch_hat": 0.95, "sword": 0.38},
                                  min_score=0.5)
    # "moon" has no stored score: nothing to judge it by, so it stays.
    assert hints == {"the reference": ["witch hat", "moon"]}
    assert dropped == [("sword", "confidence 0.38")]


def test_hints_are_offered_as_a_recommendation_not_a_list_to_copy():
    p = ss.sheet_prompt(hints={"the reference": ["lace trim", "full moon"]})
    assert "- the reference: lace trim, full moon" in p
    assert "None is dropped and none is swapped for a milder or vaguer word" in p
    assert "the tagger's mistake" not in p
    assert "Tagger's reading" not in ss.sheet_prompt()


@pytest.mark.asyncio
async def test_wd14_hints_reach_the_sheet_reader_and_unused_ones_are_said(tmp_path):
    llm = _LLM()
    q = asyncio.Queue()
    from PIL import Image

    from backend.app import runtime_config
    from backend.app.jobs.runners import run_refine_prompt

    runtime_config._cache = None
    img = tmp_path / "ref.png"
    Image.new("RGB", (64, 64)).save(img)
    await run_refine_prompt(_Reporter(), _Cancel(), body_dict={
        "sha256s": ["a"], "prompt_style": "detailed", "wd14_hints": True,
        "wd14_hint_min_score": 0.55,
    }, db=_DB(img), ollama=llm, spooler=None, comfy=None, token_queue=q)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    thinking = "".join(e["text"] for e in events if e and e["type"] == "think")
    # The tags are offered in plain words; counts and ratings are not, and
    # the straw (0.5 < 0.55) is removed before the reader sees it.
    assert "night sky" in llm.vlm_prompts[0] and "lens flare" in llm.vlm_prompts[0]
    assert "drinking straw" not in llm.vlm_prompts[0] and "2girls" not in llm.vlm_prompts[0]
    assert "drinking straw (confidence 0.50)" in thinking and "2girls (person count)" in thinking
    assert "[WD14 hints from the reference] night sky, lens flare" in thinking
    # The fake sheet has a stage and no sky or flare: both go to the placing
    # call, which (in this fake) answers nothing usable — so both are kept as
    # unclassified and written into the prompt, not dropped.
    assert "[WD14 hints not used by the reader] night sky, lens flare" in thinking
    assert llm.text_prompts[0].startswith("Below is the scene sheet for a picture, and tags")
    assert "[unclassified — written into the prompt] night sky, lens flare" in thinking
    done = next(e for e in events if e and e["type"] == "done")
    assert done["sheet"]["unclassified"] == ["night sky", "lens flare"]
    # Missing from the script, they go back to the writer — not pasted on.
    assert "The picture also shows" not in done["positive"]
    again = next(p for p in llm.text_prompts if "[Your last draft left these out]" in p)
    assert "also in the picture: night sky, lens flare" in again
    assert "[not in the script — asking the writer once more]" in thinking
    # The tag under the floor never reaches anything.
    assert "drinking" not in "".join(llm.text_prompts)


# ── Directions: one lens per call, each held to its boxes ───────────────────

def _one_girl():
    return ss.normalize_sheet({
        "medium": "anime illustration", "setting": "a rooftop", "lighting": "flat daylight",
        "camera": "eye level", "subjects": [{"who": "a girl", "appearance": "red hair",
                                              "outfit": "a black dress", "pose_action": "standing"}],
    })


def test_a_direction_is_held_to_its_lens_and_never_touches_who_someone_is():
    sheet = _one_girl()
    patch = {"set": {"lighting": "hard moonlight from below", "setting": "a desert"},
             "subjects": [{"index": 0, "set": {"appearance": "blue hair", "expression": "awed"}}],
             "add_subjects": [{"who": "a stranger"}], "remove_subjects": [0]}
    new, changed, refused, outside = ss.apply_direction(sheet, patch, "light")
    assert new["lighting"] == "hard moonlight from below"
    assert new["setting"] == "a rooftop" and new["subjects"][0]["appearance"] == "red hair"
    assert len(new["subjects"]) == 1  # nobody added or removed
    assert refused == []
    assert "setting" in outside and "subjects.0.appearance" in outside
    assert "subjects.0.expression" in outside  # light does not move faces


def test_the_showrunners_locks_are_reported_apart_from_the_lens():
    sheet = _one_girl()
    patch = {"set": {"setting": "a sunken city"},
             "subjects": [{"index": 0, "set": {"outfit": "a coral dress"}}]}
    new, changed, refused, outside = ss.apply_direction(sheet, patch, "world", locked=["subjects.0"])
    assert new["setting"] == "a sunken city"
    assert refused == ["subjects.0.outfit"] and outside == []


def test_each_lens_may_touch_its_own_boxes():
    sheet = _one_girl()
    for kind, spec in ss.DIRECTION_KINDS.items():
        locks = set(ss.direction_locks(sheet, kind))
        for box in spec["scene"]:
            assert box not in locks, (kind, box)
        for box in spec["person"]:
            assert f"subjects.0.{box}" not in locks, (kind, box)
        assert {"medium", "subjects.0.who", "subjects.0.appearance"} <= locks


def test_the_direction_prompt_names_no_example_idea():
    p = ss.direction_prompt(_one_girl(), "light", avoid=["Cold dawn rim"])
    assert "[Already proposed through this lens — go somewhere else]\n- Cold dawn rim" in p
    for word in ("neon", "rain", "sunset", "golden hour", "fog", "candle"):
        assert word not in p.lower(), word


class _DirectionsLLM:
    """Proposes per lens, then patches; the camera lens answers nonsense."""

    def __init__(self):
        self.prompts = []

    async def generate_text_stream(self, prompt, **kw):
        self.prompts.append((prompt, kw.get("options")))
        if prompt.startswith("Below is the scene sheet a vision model read"):
            if "cinematographer" in prompt:
                yield {"type": "token", "text": "I would move the camera."}
            else:
                yield {"type": "token", "text": json.dumps(
                    {"title": "Lit from below", "direction": "Light rises from the floor."})}
        else:
            yield {"type": "token", "text": json.dumps(
                {"set": {"lighting": "light rising from the floor", "setting": "a cave"}})}


@pytest.mark.asyncio
async def test_directions_stream_one_card_per_lens_and_say_which_failed(tmp_path):
    from backend.app import runtime_config
    from backend.app.jobs.runners import run_refine_directions

    runtime_config._cache = None
    llm, q = _DirectionsLLM(), asyncio.Queue()
    await run_refine_directions(_Reporter(), _Cancel(), body_dict={
        "sheet": _one_girl(), "kinds": ["light", "camera", "world"], "temperature": 0.5,
        "avoid": {"light": ["Cold dawn rim"]},
    }, db=_DB(tmp_path / "x.png"), ollama=llm, token_queue=q)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    cards = [e for e in events if e and e["type"] == "direction"]
    failed = [e for e in events if e and e["type"] == "direction_failed"]
    assert [c["kind"] for c in cards] == ["light", "world"]
    assert [f["kind"] for f in failed] == ["camera"]
    light, world = cards
    # light may change the lighting but not the place; world may change both
    assert light["sheet"]["lighting"] == "light rising from the floor"
    assert light["sheet"]["setting"] == "a rooftop" and "setting" in light["outside"]
    assert world["sheet"]["setting"] == "a cave"
    assert events[-2]["type"] == "done" and events[-1] is None
    # the proposal runs hot, the patch at the Showrunner's temperature
    assert llm.prompts[0][1]["temperature"] == 1.0 and llm.prompts[1][1]["temperature"] == 0.5
    assert "- Cold dawn rim" in llm.prompts[0][0]


# ── Nothing seen is lost: placing, other, unclassified, coverage ─────────────

def test_placement_appends_and_never_rewrites_and_keeps_the_rest():
    sheet = _one_girl()
    placement = {"place": [
        {"tag": "lace trim", "box": "outfit", "person": 0},
        {"tag": "full moon", "box": "lighting", "person": None},
        {"tag": "star (symbol)", "box": "a box that does not exist", "person": None},
    ]}
    new, placed, unclassified = ss.apply_placement(sheet, placement, ["lace trim", "full moon",
                                                                        "star (symbol)", "sword"])
    assert new["subjects"][0]["outfit"] == "a black dress, lace trim"  # appended, not replaced
    assert new["lighting"] == "flat daylight, full moon"
    assert placed == ["lace trim → person 1 outfit", "full moon → lighting"]
    assert unclassified == ["star (symbol)", "sword"] == new["unclassified"]


def test_what_the_writer_dropped_is_found_and_never_pasted():
    """The Showrunner (2026-11-03): pasted fragments broke the story. What is
    missing is found and handed back to the writer; the script is left as
    written."""
    sheet = ss.normalize_sheet({
        "medium": "anime illustration", "setting": "a ruined cathedral",
        "other": "a cracked mirror leaning on the far wall", "unclassified": ["star (symbol)"],
        "subjects": [{"who": "a girl", "outfit": "a torn white dress with a bare shoulder"}],
    })
    text = "An anime illustration of a girl in a ruined cathedral."
    script, notes = ss.finish_script(text, sheet, required=["full moon"])
    assert script == text
    missing = ss.uncovered(script, sheet, ["full moon"])
    assert "outfit (a girl): a torn white dress with a bare shoulder" in missing
    assert "other: a cracked mirror leaning on the far wall" in missing
    assert "also in the picture: star, full moon" in missing
    assert any("not in the script — outfit" in n for n in notes)
    # a script that keeps everything has nothing missing
    full = ("An anime illustration of a girl in a torn white dress with a bare shoulder, in a "
            "ruined cathedral; a cracked mirror leans on the far wall under a full moon and a star.")
    assert ss.uncovered(full, sheet, ["full moon"]) == []


def test_a_joined_word_counts_as_written():
    sheet = ss.normalize_sheet({"medium": "anime illustration", "unclassified": ["hairclip"]})
    assert ss.uncovered("An anime illustration of a girl with a gold hair clip.", sheet) == []


def test_people_who_share_a_label_are_told_apart():
    sheet = ss.normalize_sheet({"medium": "anime illustration", "subjects": [
        {"who": "young woman, background", "gaze": "the stalls across the street"},
        {"who": "young woman, background", "gaze": "her friend beside her"},
    ]})
    missing = ss.uncovered("An anime illustration of two young women.", sheet)
    assert "gaze (person 1, young woman, background): the stalls across the street" in missing
    assert "gaze (person 2, young woman, background): her friend beside her" in missing


def test_a_box_the_sheet_does_not_have_goes_to_other():
    notes = []
    sheet = ss.normalize_sheet({
        "medium": "photograph", "background": "a neon alley",
        "subjects": [{"who": "a man", "tattoos": "a dragon on the left arm"}],
    }, notes=notes)
    assert sheet["other"] == "background: a neon alley; a man — tattoos: a dragon on the left arm"
    assert len(notes) == 2


def test_the_writer_is_told_to_transcribe_not_judge():
    p = ss.script_prompt(_one_girl())
    assert "You are transcribing the sheet, not judging it" in p
    assert "do not swap a word for a milder or vaguer one" in p
    assert '"unclassified"' in p


# ── Staging: from people posing to a moment where something happens ─────────

def test_staging_moves_the_action_and_never_the_looks():
    sheet = _one_girl()
    patch = {"set": {"situation": "she has just caught a falling lantern", "camera": "low angle",
                     "setting": "a harbour"},
             "subjects": [{"index": 0, "set": {"pose_action": "lunging forward", "gaze": "the lantern",
                                                "legs_feet": "weight thrown onto the front foot",
                                                "outfit": "armour", "appearance": "blue hair",
                                                "position": "far left"}}],
             "add_subjects": [{"who": "a stranger"}]}
    new, changed, refused, outside = ss.apply_staging(sheet, patch, "move")
    assert new["situation"] == "she has just caught a falling lantern"
    assert new["subjects"][0]["pose_action"] == "lunging forward"
    assert new["subjects"][0]["legs_feet"] == "weight thrown onto the front foot"
    assert new["subjects"][0]["outfit"] == "a black dress" and new["subjects"][0]["appearance"] == "red hair"
    assert len(new["subjects"]) == 1
    # "move" keeps the place, the camera and where she stands
    assert {"camera", "setting", "subjects.0.position", "subjects.0.outfit"} <= set(outside)
    restaged, _, _, outside2 = ss.apply_staging(sheet, patch, "restage")
    assert restaged["camera"] == "low angle" and restaged["subjects"][0]["position"] == "far left"
    assert "setting" in outside2 and "subjects.0.outfit" in outside2


def test_the_staging_prompt_names_its_boxes_and_no_example_action():
    p = ss.stage_prompt(_one_girl(), "move")
    assert "Person boxes: pose_action, intent, feeling, expression, gaze, hands, legs_feet, motion, holding" in p
    assert "Keep what the reference shows them doing and where" in p
    assert "a different action and situation" in ss.stage_prompt(_one_girl(), "restage")
    for word in ("running", "dancing", "jumping", "fighting", "kiss", "wind"):
        assert word not in p.lower(), word


def test_the_writer_starts_with_what_is_happening_and_ends_with_the_mood():
    p = ss.script_prompt(_one_girl())
    assert "what is happening in this scene — the situation and the main action" in p
    assert p.index("what they are doing and why") < p.index("then how they look and what they wear")
    assert "a sentence that is only atmosphere names nothing the picture can show" in p


def test_a_dropped_action_box_is_found():
    sheet = ss.normalize_sheet({
        "medium": "anime illustration", "situation": "the ferry is pulling away from the pier",
        "subjects": [{"who": "a girl", "pose_action": "sprinting down the pier",
                      "gaze": "the departing ferry", "legs_feet": "mid-stride, one foot off the boards"}],
    })
    missing = ss.uncovered("An anime illustration of a girl on a pier, sprinting down the pier.", sheet)
    assert "situation: the ferry is pulling away from the pier" in missing
    assert "gaze (a girl): the departing ferry" in missing
    assert "legs_feet (a girl): mid-stride, one foot off the boards" in missing
    p = ss.script_prompt(sheet, left_out=missing)
    assert "[Your last draft left these out]" in p and "- gaze (a girl): the departing ferry" in p


class _StagingLLM(_LLM):
    """Reads one girl; stages her; writes a script."""

    async def generate_vlm_stream(self, prompt, images, **kw):
        if _is_rebuild(prompt):
            self.rebuild_prompts.append(prompt)
            yield {"type": "token", "text": _rebuilt(prompt)}
            return
        self.vlm_prompts.append(prompt)
        yield {"type": "token", "text": json.dumps(
            {"medium": "anime illustration", "setting": "a pier",
             "subjects": [{"who": "a girl", "pose_action": "standing", "outfit": "a sailor dress"}]})}

    async def generate_text_stream(self, prompt, **kw):
        self.text_prompts.append(prompt)
        if prompt.startswith("Below is the scene sheet a vision model read from a reference picture. As read"):
            yield {"type": "token", "text": json.dumps(
                {"set": {"situation": "the ferry is leaving without her"},
                 "subjects": [{"index": 0, "set": {"pose_action": "sprinting down the pier",
                                                    "outfit": "a raincoat"}}]})}
        else:
            yield {"type": "token", "text": "An anime illustration. " + "A girl sprints down the pier after the ferry. " * 30}


async def _staging_run(body, tmp_path):
    from PIL import Image

    from backend.app import runtime_config
    from backend.app.jobs.runners import run_refine_prompt

    runtime_config._cache = None
    img = tmp_path / "ref.png"
    Image.new("RGB", (64, 64)).save(img)
    llm, q = _StagingLLM(), asyncio.Queue()
    await run_refine_prompt(_Reporter(), _Cancel(), body_dict={
        "sha256s": ["a"], "prompt_style": "detailed", **body,
    }, db=_DB(img), ollama=llm, spooler=None, comfy=None, token_queue=q)
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return llm, [e for e in events if e]


@pytest.mark.asyncio
async def test_staging_runs_once_after_a_fresh_reading(tmp_path):
    llm, events = await _staging_run({}, tmp_path)  # "move" is the default
    staged = [p for p in llm.text_prompts if "Stage it" in p]
    assert len(staged) == 1
    done = next(e for e in events if e["type"] == "done")
    assert done["sheet"]["subjects"][0]["pose_action"] == "sprinting down the pier"
    assert done["sheet"]["subjects"][0]["outfit"] == "a sailor dress"  # clothing is held
    assert done["sheet"]["situation"] == "the ferry is leaving without her"
    thinking = "".join(e["text"] for e in events if e["type"] == "think")
    assert "[staged] person 1 pose_action" in thinking
    assert "[staging outside its range, not used] subjects.0.outfit" in thinking


@pytest.mark.asyncio
async def test_staging_is_skipped_when_read_or_when_the_sheet_was_edited(tmp_path):
    llm, _ = await _staging_run({"staging": "read"}, tmp_path)
    assert not any("Stage it" in p for p in llm.text_prompts)
    llm, _ = await _staging_run({"sheet": _one_girl()}, tmp_path)
    assert not any("Stage it" in p for p in llm.text_prompts)


# ── Reconstruction: the ledger and the reference, read back into one picture ─

def _pier():
    return ss.normalize_sheet({
        "medium": "anime illustration", "setting": "a wooden pier or dock over water",
        "props": "a coiled rope on the railing",
        "subjects": [
            {"who": "a girl, the centre", "signature": "chin in her right palm, left forearm "
             "flat on the railing, hips cocked back", "hands": "both hands under her chin",
             "outfit": "a yellow dress"},
            {"who": "a woman, background", "pose_action": "walking past", "outfit": "a white t-shirt"},
        ],
    })


def test_the_sheet_reader_is_asked_for_the_pose_and_the_story():
    p = ss.sheet_prompt()
    assert '"signature"' in p and '"story"' in p
    assert "what makes THIS pose their own" in p


def test_staging_keeps_the_signature_on_move_and_earns_a_new_one_on_restage():
    move = ss.stage_prompt(_pier(), "move")
    assert "[The pose]" in move and "is not yours to change" in move
    assert "signature" not in move.split("Person boxes:")[1].split(".")[0]
    restage = ss.stage_prompt(_pier(), "restage")
    assert "Rewrite \"signature\"" in restage
    assert "signature" in ss.STAGING_SCOPES["restage"]["person"]
    # a staged signature on "move" is refused: it is the reference's pose
    new, _, _, outside = ss.apply_staging(
        _pier(), {"subjects": [{"index": 0, "set": {"signature": "standing straight"}}]}, "move")
    assert new["subjects"][0]["signature"].startswith("chin in her right palm")
    assert "subjects.0.signature" in outside


def test_the_reconstruct_prompt_carries_the_ledger_and_the_pose():
    sheet = _pier()
    ledger = {"read": sheet, "steps": [ss.ledger_step("objects (elaborate)", "fitted", ["props: a → b"])],
              "current": sheet}
    p = ss.reconstruct_prompt(ledger, staging="move")
    assert p.startswith("You are given the reference picture")
    assert '"read"' in p and '"steps"' in p and '"current"' in p
    assert "chin in her right palm" in p
    assert "never a correction" in p
    assert "has gone general" in ss.reconstruct_prompt(ledger, staging="restage")
    assert "the two reference pictures" in ss.reconstruct_prompt(ledger, reference_count=2)


def test_a_rebuilt_sheet_is_taken_as_the_model_wrote_it_except_the_locks():
    before = _pier()
    answer = json.loads(json.dumps(before))
    answer["setting"] = "a wooden pier over the harbour, beside a night market"
    answer["subjects"][0]["hands"] = "right palm under her chin, left forearm on the railing"
    answer["subjects"][1]["outfit"] = "a raincoat"
    answer["medium"] = "photograph"
    new, changed, notes = ss.apply_reconstruction(before, answer, locked=["subjects.1.outfit"])
    assert new["setting"] == "a wooden pier over the harbour, beside a night market"
    assert new["subjects"][0]["hands"].startswith("right palm")
    assert new["subjects"][1]["outfit"] == "a white t-shirt"   # locked by the user
    assert new["medium"] == "anime illustration"                 # the medium is not the model's
    assert any(c.startswith("setting:") for c in changed) and not notes


def test_an_unreadable_rebuild_keeps_the_sheet_as_it_stood():
    before = _pier()
    for answer in ({}, {"medium": "x"}, {"setting": "a pier", "subjects": []}):
        new, changed, notes = ss.apply_reconstruction(before, answer)
        assert new is before and not changed and notes


def test_a_rebuild_that_changes_the_people_under_a_person_lock_is_not_taken():
    before = _pier()
    answer = json.loads(json.dumps(before))
    answer["subjects"] = answer["subjects"][:1]
    new, _, notes = ss.apply_reconstruction(before, answer, locked=["subjects.0"])
    assert new is before and "locked" in notes[0]


@pytest.mark.asyncio
async def test_a_staged_run_is_rebuilt_once_with_the_reference(tmp_path):
    llm, events = await _staging_run({}, tmp_path)
    assert len(llm.rebuild_prompts) == 1
    p = llm.rebuild_prompts[0]
    assert '"step": "staging (move)"' in p and "pose_action" in p
    done = next(e for e in events if e["type"] == "done")
    assert done["ledger"]["read"]["subjects"][0]["pose_action"] == "standing"
    assert done["ledger"]["current"] == done["sheet"]
    assert [st["step"] for st in done["ledger"]["steps"]] == ["staging (move)", "reconstruct"]


@pytest.mark.asyncio
async def test_nothing_is_rebuilt_when_no_step_changed_the_sheet(tmp_path):
    llm, events = await _staging_run({"staging": "read"}, tmp_path)
    assert llm.rebuild_prompts == []
    done = next(e for e in events if e["type"] == "done")
    assert done["ledger"]["steps"] == []
